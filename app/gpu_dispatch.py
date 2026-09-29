"""Private, isolated Kaggle voice jobs via the official CLI.

CLI arguments/output checked against Kaggle/kaggle-api cli.py and
kaggle_api_extended.py. collect() performs exactly one status request, never waits.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import DATA_DIR
from .kaggle_worker import DEFAULT_TEXT


@dataclass(frozen=True)
class KaggleGpuSettings:
    enabled: bool = False
    username: str | None = None
    api_token: str | None = None
    kernel_ref: str | None = None
    dataset_slug: str | None = None

    @classmethod
    def from_env(cls) -> "KaggleGpuSettings":
        return cls(os.getenv("KAGGLE_GPU_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
                   os.getenv("KAGGLE_USERNAME") or None, os.getenv("KAGGLE_API_TOKEN") or None,
                   os.getenv("KAGGLE_KERNEL_REF") or None, os.getenv("KAGGLE_DATASET_SLUG") or None)


@dataclass(frozen=True)
class DispatchResult:
    status: str
    provider: str
    message: str
    kernel_ref: str | None = None
    dataset_ref: str | None = None
    output_path: str | None = None
    manifest: dict[str, Any] | None = None


CommandExecutor = Callable[[list[str], dict[str, str], Path], subprocess.CompletedProcess[str]]


def sanitize_dispatch_error(error: Exception | str) -> str:
    """Never expose arbitrary remote errors (which may contain secrets or paths)."""
    return "Falha na operação Kaggle; verifique configuração, conectividade e disponibilidade da GPU."


class KaggleGpuDispatcher:
    provider_name = "kaggle"

    def __init__(self, settings: KaggleGpuSettings | None = None, executor: CommandExecutor | None = None,
                 staging_root: Path | None = None, output_root: Path | None = None):
        self.settings = settings or KaggleGpuSettings.from_env()
        self._executor = executor or self._run_cli
        self.staging_root = staging_root or DATA_DIR / "kaggle-staging"
        self.output_root = output_root or DATA_DIR / "outputs"

    @staticmethod
    def _job_id(job: dict[str, Any]) -> str:
        value = str(job["id"])
        if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", value):
            raise ValueError("Invalid job identifier")
        return value

    def _configured(self) -> bool:
        return bool(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", self.settings.username or "") and
                    self.settings.api_token and self.settings.api_token != "***" and
                    not self.settings.api_token.startswith("oc-sent-"))

    def dispatch(self, job: dict[str, Any]) -> DispatchResult:
        if not self.settings.enabled:
            return DispatchResult("uploaded", self.provider_name, "Encaminhamento GPU desativado por configuração.")
        if not self._configured():
            return DispatchResult("gpu_dispatch_failed", self.provider_name, "Configuração Kaggle incompleta.")
        try:
            bundle = self._build_bundle(job)
            self._forward(bundle, self._cli_env(Path(bundle["root"])))
            receipt = {key: str(bundle[key]) for key in ("kernel_ref", "dataset_ref")}
            receipt["job_id"] = self._job_id(job)
            self._write_json(Path(bundle["root"]).parent / "dispatch.json", receipt)
            return DispatchResult("gpu_queued", self.provider_name, "Job privado enviado; síntese de voz pendente.",
                                  receipt["kernel_ref"], receipt["dataset_ref"])
        except Exception:
            return DispatchResult("gpu_dispatch_failed", self.provider_name, sanitize_dispatch_error(""))

    def _build_bundle(self, job: dict[str, Any]) -> dict[str, Path | str]:
        job_id = self._job_id(job)
        source = Path(job["upload_path"])
        text = str(job.get("synthesis_text") or DEFAULT_TEXT).strip()
        if not source.is_file() or source.suffix.lower() not in {".mp4", ".mov", ".webm"}:
            raise ValueError("Invalid upload")
        if not text or len(text) > 1000:
            raise ValueError("Synthesis text must contain 1-1000 characters")
        # Never version a shared dataset or overwrite a running job's kernel.
        unique = uuid.uuid4().hex
        dataset_slug = f"avatar-input-{unique}"
        kernel_slug = f"avatar-voice-{unique}"
        dataset_ref = f"{self.settings.username}/{dataset_slug}"
        kernel_ref = f"{self.settings.username}/{kernel_slug}"
        root = self.staging_root / job_id / unique
        dataset_dir, kernel_dir = root / "dataset", root / "kernel"
        dataset_dir.mkdir(parents=True)
        kernel_dir.mkdir()
        video_name = "reference" + source.suffix.lower()
        shutil.copy2(source, dataset_dir / video_name)
        self._write_json(dataset_dir / "job.json", {"job_id": job_id, "video_name": video_name, "text": text, "language": "pt"})
        self._write_json(dataset_dir / "dataset-metadata.json", {"title": dataset_slug, "id": dataset_ref,
                                                                 "licenses": [{"name": "other"}], "isPrivate": True})
        self._write_json(kernel_dir / "kernel-metadata.json", {
            "id": kernel_ref, "title": kernel_slug, "code_file": "kernel.py", "language": "python",
            "kernel_type": "script", "is_private": True, "enable_gpu": True, "enable_internet": True,
            "dataset_sources": [dataset_ref]})
        shutil.copy2(Path(__file__).with_name("kaggle_worker.py"), kernel_dir / "kernel.py")
        return {"root": root, "dataset_dir": dataset_dir, "kernel_dir": kernel_dir,
                "dataset_ref": dataset_ref, "kernel_ref": kernel_ref}

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _cli_env(self, root: Path) -> dict[str, str]:
        config = root / "kaggle-config"
        config.mkdir(parents=True, exist_ok=True)
        env = {name: os.environ[name] for name in ("PATH", "SystemRoot", "WINDIR", "HOME", "USERPROFILE", "TEMP", "TMP") if os.environ.get(name)}
        env.update(KAGGLE_API_TOKEN=self.settings.api_token or "", KAGGLE_CONFIG_DIR=str(config))
        return env

    def _forward(self, bundle: dict[str, Path | str], env: dict[str, str]) -> None:
        dataset, kernel = Path(bundle["dataset_dir"]), Path(bundle["kernel_dir"])
        # Official CLI creates datasets PRIVATE unless --public is provided.
        create = self._execute(["kaggle", "datasets", "create", "-p", str(dataset)], env, dataset)
        self._require_success(create, "create")
        if re.search(r"\b(error|failed|forbidden)\b", create.stdout + create.stderr, re.I):
            raise RuntimeError("Dataset create failed")
        push = self._execute(["kaggle", "kernels", "push", "-p", str(kernel)], env, kernel)
        self._require_success(push, "push")
        # CLI can return zero even when ApiSaveKernelResponse.error is populated.
        if (not re.search(r"Kernel version(?: \d+)? successfully pushed", push.stdout, re.I)
                or str(bundle["kernel_ref"]) not in push.stdout
                or re.search(r"push error|not valid dataset sources", push.stdout + push.stderr, re.I)):
            raise RuntimeError("Kernel push not confirmed")

    def collect(self, job: dict[str, Any]) -> DispatchResult:
        """One status poll. Read saved receipt; return local WAV only after validation.

        status: gpu_queued/gpu_running/voice_ready/gpu_failed/gpu_collect_failed.
        No dispatch, upload, scheduler, retry loop or DB mutations are performed.
        """
        if not self.settings.enabled or not self._configured():
            return DispatchResult("gpu_collect_failed", self.provider_name, "Coleta Kaggle desativada ou configuração incompleta.")
        try:
            job_id = self._job_id(job)
            root = self.staging_root / job_id
            receipt = json.loads((root / "dispatch.json").read_text(encoding="utf-8"))
            ref = receipt["kernel_ref"]
            if receipt["job_id"] != job_id or not re.fullmatch(re.escape(self.settings.username or "") + r"/avatar-voice-[a-f0-9]{32}", ref):
                raise ValueError("Invalid receipt")
            env = self._cli_env(root)
            status = self._execute(["kaggle", "kernels", "status", ref], env, root)
            self._require_success(status, "status")
            match = re.search(r'has status "(?:KernelWorkerStatus\.)?([a-zA-Z]+)"', status.stdout)
            if not match:
                raise RuntimeError("Unknown status")
            state = match.group(1).lower()
            if state in {"queued", "running"}:
                return DispatchResult("gpu_" + state, self.provider_name, "Execução GPU ainda pendente.", ref, receipt["dataset_ref"])
            if state not in {"complete", "completed", "error", "failed", "cancelled", "canceled"}:
                raise RuntimeError("Unknown state")
            self.output_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="collect-", dir=self.output_root) as temp:
                download = Path(temp)
                result = self._execute(["kaggle", "kernels", "output", ref, "-p", str(download),
                                        "--file-pattern", r"^(dispatch_result\.json|voice\.wav)$"], env, root)
                self._require_success(result, "output")
                manifest = json.loads((download / "dispatch_result.json").read_text(encoding="utf-8"))
                if manifest.get("job_id") != job_id:
                    raise ValueError("Mismatched output job")
                if state not in {"complete", "completed"} or manifest.get("state") != "completed":
                    return DispatchResult("gpu_failed", self.provider_name, "Worker GPU não concluiu a síntese.", ref, receipt["dataset_ref"])
                wav = download / "voice.wav"
                artifacts = manifest.get("artifacts", [])
                artifact = next(item for item in artifacts if item.get("name") == "voice.wav")
                if wav.is_symlink() or not wav.is_file() or wav.stat().st_size != artifact["bytes"]:
                    raise ValueError("Invalid output size")
                if hashlib.sha256(wav.read_bytes()).hexdigest() != artifact["sha256"]:
                    raise ValueError("Invalid output hash")
                with wave.open(str(wav), "rb") as audio:
                    if audio.getnframes() <= 0 or audio.getframerate() <= 0:
                        raise ValueError("Empty WAV")
                destination = self.output_root / job_id
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copy2(wav, destination / "voice.wav")
                self._write_json(destination / "dispatch_result.json", manifest)
            return DispatchResult("voice_ready", self.provider_name, "Áudio sintetizado recuperado; vídeo de avatar não foi gerado.",
                                  ref, receipt["dataset_ref"], str(destination / "voice.wav"), manifest)
        except Exception:
            return DispatchResult("gpu_collect_failed", self.provider_name, sanitize_dispatch_error(""))

    def _execute(self, command: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return self._executor(command, env, cwd)

    @staticmethod
    def _run_cli(command: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False, timeout=600)

    @staticmethod
    def _require_success(result: subprocess.CompletedProcess[str], action: str) -> None:
        if result.returncode != 0:
            raise RuntimeError("Kaggle operation failed: " + action)


def dispatch_history_entry(result: DispatchResult) -> dict[str, str]:
    entry = {"at": datetime.now(timezone.utc).isoformat(), "provider": result.provider,
             "status": result.status, "message": result.message}
    for key in ("kernel_ref", "dataset_ref", "output_path"):
        value = getattr(result, key)
        if value:
            entry[key] = value
    return entry

"""Optional GPU-dispatch providers.

Providers are isolated from the upload route so a durable queue/worker provider can
replace Kaggle without changing consent or media-validation flow. Kaggle is disabled
by default and its CLI is called only when dispatch is explicitly enabled.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import DATA_DIR


@dataclass(frozen=True)
class KaggleGpuSettings:
    enabled: bool = False
    username: str | None = None
    api_token: str | None = None
    kernel_ref: str | None = None
    dataset_slug: str | None = None

    @classmethod
    def from_env(cls) -> "KaggleGpuSettings":
        return cls(
            enabled=os.getenv("KAGGLE_GPU_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
            username=os.getenv("KAGGLE_USERNAME") or None,
            api_token=os.getenv("KAGGLE_API_TOKEN") or None,
            kernel_ref=os.getenv("KAGGLE_KERNEL_REF") or None,
            dataset_slug=os.getenv("KAGGLE_DATASET_SLUG") or None,
        )


@dataclass(frozen=True)
class DispatchResult:
    status: str
    provider: str
    message: str


CommandExecutor = Callable[[list[str], dict[str, str], Path], subprocess.CompletedProcess[str]]


def sanitize_dispatch_error(error: Exception | str) -> str:
    """Return an operator-safe message without tokens, paths, or API responses."""
    text = str(error).replace("\n", " ").replace("\r", " ")
    text = re.sub(r"(?i)(key|token|password|secret)\s*[=:]\s*[^\s,;]+", r"\1=[redacted]", text)
    text = re.sub(r"(?i)(kaggle_api_token|kaggle_username)\s*[^\s,;]*", "[redacted]", text)
    return text[:240] or "Falha desconhecida ao encaminhar para o worker GPU."


class KaggleGpuDispatcher:
    """Create a per-job Kaggle dataset version and push a GPU kernel CLI bundle.

    A `gpu_queued` result means the official Kaggle CLI accepted the input dataset
    operation and kernel push. It does *not* claim the ephemeral Kaggle run finished
    or that an output file was produced.
    """

    provider_name = "kaggle"

    def __init__(
        self,
        settings: KaggleGpuSettings | None = None,
        executor: CommandExecutor | None = None,
        staging_root: Path | None = None,
    ):
        self.settings = settings or KaggleGpuSettings.from_env()
        self._executor = executor or self._run_cli
        self.staging_root = staging_root or DATA_DIR / "kaggle-staging"

    def dispatch(self, job: dict[str, Any]) -> DispatchResult:
        if not self.settings.enabled:
            return DispatchResult("uploaded", self.provider_name, "Encaminhamento GPU desativado por configuração.")
        missing = [name for name, value in {
            "KAGGLE_USERNAME": self.settings.username,
            "KAGGLE_API_TOKEN": self.settings.api_token,
            "KAGGLE_KERNEL_REF": self.settings.kernel_ref,
            "KAGGLE_DATASET_SLUG": self.settings.dataset_slug,
        }.items() if not value]
        if missing:
            return DispatchResult("gpu_dispatch_failed", self.provider_name, "Configuração Kaggle incompleta.")
        try:
            bundle = self._build_bundle(job)
            env = self._cli_env(Path(bundle["root"]))
            self._forward(bundle, env)
            return DispatchResult("gpu_queued", self.provider_name, "Dataset versionado e kernel GPU enviados ao Kaggle; execução pendente.")
        except Exception as exc:  # CLI/remote failures are expected at this boundary
            return DispatchResult("gpu_dispatch_failed", self.provider_name, sanitize_dispatch_error(exc))

    def _build_bundle(self, job: dict[str, Any]) -> dict[str, Path | str]:
        source_video = Path(job["upload_path"])
        if not source_video.is_file():
            raise RuntimeError("Arquivo de upload não está disponível para o adaptador Kaggle.")
        job_id = str(job["id"])
        if not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", job_id):
            raise RuntimeError("Identificador de job inválido para staging Kaggle.")
        dataset_ref = f"{self.settings.username}/{self.settings.dataset_slug}"
        root = self.staging_root / job_id
        dataset_dir, kernel_dir = root / "dataset", root / "kernel"
        if root.exists():
            shutil.rmtree(root)
        dataset_dir.mkdir(parents=True)
        kernel_dir.mkdir(parents=True)
        video_name = f"reference{source_video.suffix.lower()}"
        shutil.copy2(source_video, dataset_dir / video_name)
        self._write_json(dataset_dir / "dataset-metadata.json", {
            "title": f"Avatar Factory job {job_id}", "id": dataset_ref,
            "licenses": [{"name": "other"}],
        })
        self._write_json(kernel_dir / "kernel-metadata.json", {
            "id": self.settings.kernel_ref, "title": f"Avatar Factory GPU job {job_id}",
            "code_file": "kernel.py", "language": "python", "kernel_type": "script",
            "is_private": True, "enable_gpu": True, "dataset_sources": [dataset_ref],
        })
        (kernel_dir / "kernel.py").write_text(self._kernel_script(dataset_ref, job_id), encoding="utf-8")
        return {"root": root, "dataset_dir": dataset_dir, "kernel_dir": kernel_dir, "dataset_ref": dataset_ref}

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _kernel_script(dataset_ref: str, job_id: str) -> str:
        return f'''"""Generated Avatar Factory Kaggle worker contract."""
import json
from pathlib import Path

DATASET_REF = {dataset_ref!r}
JOB_ID = {job_id!r}
input_dir = Path("/kaggle/input") / DATASET_REF.split("/", 1)[-1]
videos = [str(path) for path in input_dir.glob("reference.*")]
output = Path("/kaggle/working/dispatch_result.json")
output.write_text(json.dumps({{"job_id": JOB_ID, "dataset": DATASET_REF, "input_videos": videos, "state": "worker_started"}}), encoding="utf-8")
print(output.read_text(encoding="utf-8"))
'''

    def _cli_env(self, root: Path) -> dict[str, str]:
        """Minimal inheritable environment; token is process-only, never written."""
        config_dir = root / "kaggle-config"
        config_dir.mkdir(parents=True, exist_ok=True)
        env = {name: os.environ[name] for name in ("PATH", "SystemRoot", "WINDIR", "HOME", "USERPROFILE") if os.environ.get(name)}
        env["KAGGLE_API_TOKEN"] = self.settings.api_token or ""
        env["KAGGLE_CONFIG_DIR"] = str(config_dir)
        return env

    def _forward(self, bundle: dict[str, Path | str], env: dict[str, str]) -> None:
        dataset_dir, kernel_dir = Path(bundle["dataset_dir"]), Path(bundle["kernel_dir"])
        dataset_ref = str(bundle["dataset_ref"])
        view = self._execute(["kaggle", "datasets", "view", "-d", dataset_ref], env, dataset_dir)
        if view.returncode == 0:
            self._require_success(self._execute(
                ["kaggle", "datasets", "version", "-p", str(dataset_dir), "-m", "Avatar Factory job input"], env, dataset_dir
            ), "versionar dataset Kaggle")
        elif self._is_unambiguous_not_found(view):
            self._require_success(self._execute(
                ["kaggle", "datasets", "create", "-p", str(dataset_dir), "--private"], env, dataset_dir
            ), "criar dataset Kaggle")
        else:
            raise RuntimeError("Não foi possível verificar o dataset Kaggle; criação não será tentada.")
        self._require_success(self._execute(["kaggle", "kernels", "push", "-p", str(kernel_dir)], env, kernel_dir), "enviar kernel Kaggle")

    def _execute(self, command: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return self._executor(command, env, cwd)

    @staticmethod
    def _run_cli(command: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)
        except FileNotFoundError as exc:
            raise RuntimeError("Kaggle CLI não instalado ou ausente do PATH.") from exc

    @staticmethod
    def _is_unambiguous_not_found(result: subprocess.CompletedProcess[str]) -> bool:
        output = f"{result.stdout}\n{result.stderr}".lower()
        return bool(re.search(r"\b404\b|dataset[^\n]*not found|not found[^\n]*dataset", output))

    @staticmethod
    def _require_success(result: subprocess.CompletedProcess[str], action: str) -> None:
        if result.returncode != 0:
            raise RuntimeError(f"Falha ao {action} (Kaggle CLI retornou código {result.returncode}).")


def dispatch_history_entry(result: DispatchResult) -> dict[str, str]:
    return {"at": datetime.now(timezone.utc).isoformat(), "provider": result.provider, "status": result.status, "message": result.message}

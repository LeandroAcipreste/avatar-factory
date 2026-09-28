"""Optional GPU-dispatch providers.

Providers are isolated from the upload route so a durable queue/worker provider can
replace Kaggle without changing consent or media-validation flow. Kaggle is disabled
by default and its SDK is imported only when dispatch is explicitly enabled.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import DATA_DIR


@dataclass(frozen=True)
class KaggleGpuSettings:
    enabled: bool = False
    username: str | None = None
    key: str | None = None
    kernel_ref: str | None = None
    dataset_slug: str | None = None

    @classmethod
    def from_env(cls) -> "KaggleGpuSettings":
        return cls(
            enabled=os.getenv("KAGGLE_GPU_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
            username=os.getenv("KAGGLE_USERNAME") or None,
            key=os.getenv("KAGGLE_KEY") or None,
            kernel_ref=os.getenv("KAGGLE_KERNEL_REF") or None,
            dataset_slug=os.getenv("KAGGLE_DATASET_SLUG") or None,
        )


@dataclass(frozen=True)
class DispatchResult:
    status: str
    provider: str
    message: str


def sanitize_dispatch_error(error: Exception | str) -> str:
    """Return an operator-safe message without tokens, paths, or API responses."""
    text = str(error).replace("\n", " ").replace("\r", " ")
    text = re.sub(r"(?i)(key|token|password|secret)\s*[=:]\s*[^\s,;]+", r"\1=[redacted]", text)
    text = re.sub(r"(?i)(kaggle_key|kaggle_username)\s*[^\s,;]*", "[redacted]", text)
    return text[:240] or "Falha desconhecida ao encaminhar para o worker GPU."


class KaggleGpuDispatcher:
    """Create a per-job Kaggle dataset version and push a GPU kernel bundle.

    A `gpu_queued` result means the official Kaggle client accepted both the input
    dataset operation and the kernel push. It does *not* claim that the ephemeral
    Kaggle run finished or that an output file was produced.
    """

    provider_name = "kaggle"

    def __init__(
        self,
        settings: KaggleGpuSettings | None = None,
        api_factory: Callable[[], Any] | None = None,
        staging_root: Path | None = None,
    ):
        self.settings = settings or KaggleGpuSettings.from_env()
        self._api_factory = api_factory
        self.staging_root = staging_root or DATA_DIR / "kaggle-staging"

    def dispatch(self, job: dict[str, Any]) -> DispatchResult:
        if not self.settings.enabled:
            return DispatchResult("uploaded", self.provider_name, "Encaminhamento GPU desativado por configuração.")
        missing = [name for name, value in {
            "KAGGLE_USERNAME": self.settings.username,
            "KAGGLE_KEY": self.settings.key,
            "KAGGLE_KERNEL_REF": self.settings.kernel_ref,
            "KAGGLE_DATASET_SLUG": self.settings.dataset_slug,
        }.items() if not value]
        if missing:
            return DispatchResult("gpu_dispatch_failed", self.provider_name, "Configuração Kaggle incompleta.")
        try:
            bundle = self._build_bundle(job)
            api = self._make_api()
            previous_username, previous_key = os.environ.get("KAGGLE_USERNAME"), os.environ.get("KAGGLE_KEY")
            os.environ["KAGGLE_USERNAME"] = self.settings.username or ""
            os.environ["KAGGLE_KEY"] = self.settings.key or ""
            try:
                api.authenticate()
                self._forward(api, bundle)
            finally:
                self._restore_env("KAGGLE_USERNAME", previous_username)
                self._restore_env("KAGGLE_KEY", previous_key)
            return DispatchResult("gpu_queued", self.provider_name, "Dataset versionado e kernel GPU enviados ao Kaggle; execução pendente.")
        except Exception as exc:  # remote/SDK failures are expected at this boundary
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
            "title": f"Avatar Factory job {job_id}",
            "id": dataset_ref,
            "licenses": [{"name": "other"}],
        })
        self._write_json(kernel_dir / "kernel-metadata.json", {
            "id": self.settings.kernel_ref,
            "title": f"Avatar Factory GPU job {job_id}",
            "code_file": "kernel.py",
            "language": "python",
            "kernel_type": "script",
            "is_private": True,
            "enable_gpu": True,
            "dataset_sources": [dataset_ref],
        })
        (kernel_dir / "kernel.py").write_text(self._kernel_script(dataset_ref, job_id), encoding="utf-8")
        return {"dataset_dir": dataset_dir, "kernel_dir": kernel_dir, "dataset_ref": dataset_ref}

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _kernel_script(dataset_ref: str, job_id: str) -> str:
        """A safe worker contract: locate input and leave an output manifest.

        The configured Kaggle kernel may replace this script with a project-specific
        worker, but must retain output handling under /kaggle/working.
        """
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

    def _make_api(self) -> Any:
        if self._api_factory:
            return self._api_factory()
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
        except ImportError as exc:
            raise RuntimeError("SDK Kaggle opcional não instalado.") from exc
        return KaggleApi()

    def _forward(self, api: Any, bundle: dict[str, Path | str]) -> None:
        dataset_dir, kernel_dir = str(bundle["dataset_dir"]), str(bundle["kernel_dir"])
        dataset_ref = str(bundle["dataset_ref"])
        # The official API exposes dataset_view/create_new/create_version. A
        # non-404 lookup error is intentionally not treated as a missing dataset,
        # avoiding an unverified create on authentication or transport failures.
        try:
            api.dataset_view(dataset_ref)
        except Exception as exc:
            if self._http_status(exc) != 404:
                raise RuntimeError("Não foi possível verificar o dataset Kaggle antes do despacho.") from exc
            api.dataset_create_new(dataset_dir, public=False, quiet=True)
        else:
            api.dataset_create_version(dataset_dir, version_notes="Avatar Factory job input", quiet=True)
        api.kernels_push(kernel_dir)

    @staticmethod
    def _http_status(error: Exception) -> int | None:
        return getattr(error, "status", None) or getattr(getattr(error, "response", None), "status_code", None)

    @staticmethod
    def _restore_env(name: str, old_value: str | None) -> None:
        if old_value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old_value


def dispatch_history_entry(result: DispatchResult) -> dict[str, str]:
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "provider": result.provider,
        "status": result.status,
        "message": result.message,
    }

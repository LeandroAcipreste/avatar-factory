import json
from pathlib import Path

from app.gpu_dispatch import KaggleGpuDispatcher, KaggleGpuSettings, sanitize_dispatch_error


class FakeKaggleApi:
    def __init__(self):
        self.authenticated = False
        self.calls = []

    def authenticate(self):
        self.authenticated = True

    def dataset_view(self, dataset_ref):
        self.calls.append(("dataset_view", dataset_ref))
        return {"ref": dataset_ref}

    def dataset_create_version(self, folder, version_notes, quiet):
        self.calls.append(("dataset_create_version", folder, version_notes, quiet))

    def dataset_create_new(self, folder, public, quiet):
        self.calls.append(("dataset_create_new", folder, public, quiet))

    def kernels_push(self, folder):
        self.calls.append(("kernels_push", folder))


class NotFoundError(Exception):
    status = 404


class NewDatasetApi(FakeKaggleApi):
    def dataset_view(self, dataset_ref):
        self.calls.append(("dataset_view", dataset_ref))
        raise NotFoundError("not found")


def settings():
    return KaggleGpuSettings(True, "user", "not-a-real-key", "user/worker", "input-data")


def test_disabled_dispatch_never_constructs_sdk(tmp_path):
    dispatcher = KaggleGpuDispatcher(KaggleGpuSettings(enabled=False), api_factory=lambda: (_ for _ in ()).throw(AssertionError()))

    result = dispatcher.dispatch({"id": "job-1", "upload_path": str(tmp_path / "video.mp4")})

    assert result.status == "uploaded"


def test_enabled_dispatch_builds_isolated_bundle_and_uses_mocked_official_api(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"test video")
    api = FakeKaggleApi()
    staging = tmp_path / "staging"
    result = KaggleGpuDispatcher(settings(), api_factory=lambda: api, staging_root=staging).dispatch(
        {"id": "job-1", "upload_path": str(video)}
    )

    dataset_dir = staging / "job-1" / "dataset"
    kernel_dir = staging / "job-1" / "kernel"
    assert result.status == "gpu_queued"
    assert api.authenticated
    assert [call[0] for call in api.calls] == ["dataset_view", "dataset_create_version", "kernels_push"]
    assert (dataset_dir / "reference.mp4").read_bytes() == b"test video"
    assert json.loads((dataset_dir / "dataset-metadata.json").read_text())["id"] == "user/input-data"
    metadata = json.loads((kernel_dir / "kernel-metadata.json").read_text())
    assert metadata["id"] == "user/worker"
    assert metadata["enable_gpu"] is True
    assert metadata["dataset_sources"] == ["user/input-data"]
    assert "dispatch_result.json" in (kernel_dir / "kernel.py").read_text()


def test_missing_dataset_is_created_before_kernel_push(tmp_path):
    video = tmp_path / "video.webm"
    video.write_bytes(b"test")
    api = NewDatasetApi()
    result = KaggleGpuDispatcher(settings(), api_factory=lambda: api, staging_root=tmp_path / "staging").dispatch(
        {"id": "job-1", "upload_path": str(video)}
    )

    assert result.status == "gpu_queued"
    assert [call[0] for call in api.calls] == ["dataset_view", "dataset_create_new", "kernels_push"]


def test_missing_configuration_is_safe_failure(tmp_path):
    result = KaggleGpuDispatcher(KaggleGpuSettings(enabled=True)).dispatch({"id": "job-1", "upload_path": str(tmp_path / "video.mp4")})

    assert result.status == "gpu_dispatch_failed"
    assert "incompleta" in result.message


def test_error_sanitization_removes_secret_values():
    message = sanitize_dispatch_error("request failed KAGGLE_KEY=super-secret token=abc123")

    assert "super-secret" not in message
    assert "abc123" not in message

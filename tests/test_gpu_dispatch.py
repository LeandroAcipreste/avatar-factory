import json
import subprocess

from app.gpu_dispatch import KaggleGpuDispatcher, KaggleGpuSettings, sanitize_dispatch_error


class FakeKaggleCli:
    def __init__(self, dataset_missing=False):
        self.dataset_missing = dataset_missing
        self.calls = []
        self.envs = []

    def __call__(self, command, env, cwd):
        self.calls.append(command)
        self.envs.append(env)
        if command[1:3] == ["datasets", "view"]:
            if self.dataset_missing:
                return subprocess.CompletedProcess(command, 1, "", "404 dataset not found")
            return subprocess.CompletedProcess(command, 0, "dataset found", "")
        return subprocess.CompletedProcess(command, 0, "ok", "")


def settings():
    return KaggleGpuSettings(True, "user", "not-a-real-token", "user/worker", "input-data")


def test_disabled_dispatch_never_constructs_executor(tmp_path):
    dispatcher = KaggleGpuDispatcher(KaggleGpuSettings(enabled=False), executor=lambda *_: (_ for _ in ()).throw(AssertionError()))
    result = dispatcher.dispatch({"id": "job-1", "upload_path": str(tmp_path / "video.mp4")})
    assert result.status == "uploaded"


def test_enabled_dispatch_builds_isolated_bundle_and_uses_mocked_cli(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"test video")
    cli = FakeKaggleCli()
    staging = tmp_path / "staging"
    result = KaggleGpuDispatcher(settings(), executor=cli, staging_root=staging).dispatch({"id": "job-1", "upload_path": str(video)})

    dataset_dir = staging / "job-1" / "dataset"
    kernel_dir = staging / "job-1" / "kernel"
    assert result.status == "gpu_queued"
    assert [command[1:3] for command in cli.calls] == [["datasets", "view"], ["datasets", "version"], ["kernels", "push"]]
    assert (dataset_dir / "reference.mp4").read_bytes() == b"test video"
    assert json.loads((dataset_dir / "dataset-metadata.json").read_text())["id"] == "user/input-data"
    metadata = json.loads((kernel_dir / "kernel-metadata.json").read_text())
    assert metadata["id"] == "user/worker"
    assert metadata["enable_gpu"] is True
    assert "dispatch_result.json" in (kernel_dir / "kernel.py").read_text()
    assert cli.envs[0]["KAGGLE_API_TOKEN"] == "not-a-real-token"
    assert cli.envs[0]["KAGGLE_CONFIG_DIR"].endswith("kaggle-config")
    assert "KAGGLE_KEY" not in cli.envs[0]


def test_unambiguously_missing_dataset_is_created_before_kernel_push(tmp_path):
    video = tmp_path / "video.webm"
    video.write_bytes(b"test")
    cli = FakeKaggleCli(dataset_missing=True)
    result = KaggleGpuDispatcher(settings(), executor=cli, staging_root=tmp_path / "staging").dispatch({"id": "job-1", "upload_path": str(video)})

    assert result.status == "gpu_queued"
    assert [command[1:3] for command in cli.calls] == [["datasets", "view"], ["datasets", "create"], ["kernels", "push"]]


def test_ambiguous_dataset_failure_does_not_create(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"test")
    cli = FakeKaggleCli()
    cli.__call__ = None

    def ambiguous(command, env, cwd):
        if command[1:3] == ["datasets", "view"]:
            return subprocess.CompletedProcess(command, 1, "", "permission denied")
        raise AssertionError("must not attempt dataset create/version or kernel push")

    result = KaggleGpuDispatcher(settings(), executor=ambiguous, staging_root=tmp_path / "staging").dispatch({"id": "job-1", "upload_path": str(video)})
    assert result.status == "gpu_dispatch_failed"
    assert "verificar" in result.message


def test_missing_configuration_is_safe_failure(tmp_path):
    result = KaggleGpuDispatcher(KaggleGpuSettings(enabled=True)).dispatch({"id": "job-1", "upload_path": str(tmp_path / "video.mp4")})
    assert result.status == "gpu_dispatch_failed"
    assert "incompleta" in result.message


def test_error_sanitization_removes_secret_values():
    message = sanitize_dispatch_error("request failed KAGGLE_API_TOKEN=super-secret token=abc123")
    assert "super-secret" not in message
    assert "abc123" not in message

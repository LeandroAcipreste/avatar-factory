import hashlib
import json
from pathlib import Path
import subprocess
import wave

import pytest
from app.gpu_dispatch import KaggleGpuDispatcher, KaggleGpuSettings, sanitize_dispatch_error


class FakeKaggleCli:
    def __init__(self, status="complete", push_error=False, mismatch=False):
        self.calls, self.envs = [], []
        self.status, self.push_error, self.mismatch = status, push_error, mismatch

    def __call__(self, command, env, cwd):
        self.calls.append(command)
        self.envs.append(env)
        output = "ok"
        if command[1:3] == ["kernels", "push"]:
            ref = json.loads((cwd / "kernel-metadata.json").read_text())["id"]
            output = "Kernel push error: secret" if self.push_error else f"Kernel version 1 successfully pushed. Please check progress at https://www.kaggle.com/code/{ref}"
        if command[1:3] == ["kernels", "status"]:
            output = f'{command[3]} has status "{self.status}"'
        if command[1:3] == ["kernels", "output"]:
            dest = Path(command[command.index("-p") + 1])
            with wave.open(str(dest / "voice.wav"), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(b"\0\0" * 2400)
            content = (dest / "voice.wav").read_bytes()
            (dest / "dispatch_result.json").write_text(json.dumps({"job_id": "other" if self.mismatch else "job-1", "state": "completed", "artifacts": [{"name": "voice.wav", "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}]}))
        return subprocess.CompletedProcess(command, 0, output, "")


def settings():
    return KaggleGpuSettings(True, "user", "not-a-real-token", "user/worker", "input-data")


def setup(tmp_path, cli=None):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"test")
    job = {"id": "job-1", "upload_path": str(video), "synthesis_text": "Olá, mundo!"}
    cli = cli or FakeKaggleCli()
    dispatcher = KaggleGpuDispatcher(settings(), cli, tmp_path / "staging", tmp_path / "outputs")
    return dispatcher, job, cli


def test_disabled_dispatch_never_calls_executor():
    dispatcher = KaggleGpuDispatcher(KaggleGpuSettings(), executor=lambda *_: pytest.fail("called"))
    assert dispatcher.dispatch({}).status == "uploaded"


def test_private_unique_bundle_and_receipt(tmp_path):
    dispatcher, job, cli = setup(tmp_path)
    result = dispatcher.dispatch(job)
    assert result.status == "gpu_queued"
    assert [c[1:3] for c in cli.calls] == [["datasets", "create"], ["kernels", "push"]]
    assert "--public" not in cli.calls[0]
    kernel_dir = Path(cli.calls[1][-1])
    metadata = json.loads((kernel_dir / "kernel-metadata.json").read_text())
    assert metadata["is_private"] and metadata["enable_gpu"] and metadata["enable_internet"]
    assert metadata["id"] != "user/worker"
    payload = json.loads((kernel_dir.parent / "dataset" / "job.json").read_text(encoding="utf-8"))
    assert payload["text"] == job["synthesis_text"]
    assert "not-a-real-token" not in (kernel_dir / "kernel.py").read_text()
    assert cli.envs[0]["KAGGLE_API_TOKEN"] == "not-a-real-token"
    second = dispatcher.dispatch(job)
    assert second.kernel_ref != result.kernel_ref and second.dataset_ref != result.dataset_ref


def test_zero_exit_push_error_is_not_success(tmp_path):
    dispatcher, job, cli = setup(tmp_path, FakeKaggleCli(push_error=True))
    assert dispatcher.dispatch(job).status == "gpu_dispatch_failed"
    assert not (dispatcher.staging_root / job["id"] / "dispatch.json").exists()


@pytest.mark.parametrize("status", ["queued", "running"])
def test_collect_single_poll_without_download(tmp_path, status):
    dispatcher, job, cli = setup(tmp_path, FakeKaggleCli(status=status))
    dispatcher.dispatch(job)
    cli.calls.clear()
    assert dispatcher.collect(job).status == "gpu_" + status
    assert len(cli.calls) == 1


def test_collect_validated_local_wav(tmp_path):
    dispatcher, job, cli = setup(tmp_path)
    dispatcher.dispatch(job)
    cli.calls.clear()
    result = dispatcher.collect(job)
    assert result.status == "voice_ready"
    assert Path(result.output_path).is_file()
    assert [c[1:3] for c in cli.calls] == [["kernels", "status"], ["kernels", "output"]]


def test_collect_rejects_wrong_job(tmp_path):
    dispatcher, job, cli = setup(tmp_path, FakeKaggleCli(mismatch=True))
    dispatcher.dispatch(job)
    assert dispatcher.collect(job).status == "gpu_collect_failed"
    assert not (dispatcher.output_root / job["id"] / "voice.wav").exists()


def test_missing_configuration_and_path_traversal(tmp_path):
    assert KaggleGpuDispatcher(KaggleGpuSettings(enabled=True)).dispatch({}).status == "gpu_dispatch_failed"
    dispatcher, job, cli = setup(tmp_path)
    job["id"] = "../escape"
    assert dispatcher.dispatch(job).status == "gpu_dispatch_failed"
    assert not cli.calls


def test_error_sanitization_removes_arbitrary_remote_details():
    message = sanitize_dispatch_error("https://private/path password=x Bearer abc123")
    assert "abc123" not in message and "private/path" not in message

"""Worker contract tests with fake inference; no GPU/model download claimed."""
import hashlib
import json
from pathlib import Path
import sys
import types
import wave
from contextlib import nullcontext

from app import kaggle_worker as worker


def test_worker_real_contract_with_mocked_inference(tmp_path, monkeypatch):
    inputs, outputs = tmp_path / "input", tmp_path / "output"
    inputs.mkdir()
    (inputs / "job.json").write_text(json.dumps({"job_id": "test", "video_name": "reference.mp4", "text": "Olá!"}))
    (inputs / "reference.mp4").write_bytes(b"fake video")
    calls = {}

    def ffmpeg(command, **kwargs):
        calls["ffmpeg"] = command
        with wave.open(command[-1], "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(b"\0\0" * 24000 * 4)

    class Tensor:
        def squeeze(self, *_): return self
        def detach(self): return self
        def cpu(self): return self
        def numpy(self): return [0.1] * 2400

    class Model:
        sr = 24000
        @classmethod
        def from_local(cls, path, device):
            assert device == "cuda"
            return cls()
        def generate(self, text, **kwargs):
            calls["generate"] = (text, kwargs)
            return Tensor()

    def save(path, data, sr, **kwargs):
        with wave.open(path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sr)
            wav.writeframes(b"\1\0" * len(data))

    def download(repo, **kwargs):
        assert repo == "ResembleAI/chatterbox" and kwargs["token"] is False
        assert "t3_mtl23ls_v2.safetensors" in kwargs["allow_patterns"]
        return "fake-weights"

    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True), inference_mode=nullcontext))
    monkeypatch.setitem(sys.modules, "soundfile", types.SimpleNamespace(write=save))
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", types.SimpleNamespace(get_ffmpeg_exe=lambda: "ffmpeg"))
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=download))
    monkeypatch.setitem(sys.modules, "chatterbox", types.ModuleType("chatterbox"))
    monkeypatch.setitem(sys.modules, "chatterbox.mtl_tts", types.SimpleNamespace(ChatterboxMultilingualTTS=Model))
    monkeypatch.setattr(worker.subprocess, "run", ffmpeg)
    result = worker.synthesize(inputs, outputs)
    assert result["state"] == "completed"
    assert result["avatar_video_generated"] is False
    assert result["voice_training_performed"] is False
    assert calls["generate"][1]["language_id"] == "pt"
    assert result["artifacts"][0]["sha256"] == hashlib.sha256((outputs / "voice.wav").read_bytes()).hexdigest()
    assert not (outputs / "reference.wav").exists()


def test_bootstrap_failure_writes_honest_manifest(tmp_path, monkeypatch):
    inputs, outputs = tmp_path / "input", tmp_path / "output"
    inputs.mkdir()
    (inputs / "job.json").write_text('{"job_id":"test"}')
    def fail(*args, **kwargs):
        raise RuntimeError("secret exception path")
    monkeypatch.setattr(worker.subprocess, "run", fail)
    assert worker.bootstrap(inputs, outputs) == 1
    manifest = json.loads((outputs / "dispatch_result.json").read_text())
    assert manifest["state"] == "failed" and manifest["artifacts"] == []
    assert "secret" not in str(manifest)

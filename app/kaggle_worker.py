"""Self-contained Kaggle worker; never requires application code or credentials.

Official sources: https://pypi.org/project/chatterbox-tts/0.1.6/
https://huggingface.co/ResembleAI/chatterbox (MIT, Portuguese supported).
Dependencies run in an isolated Python 3.11 environment, not Kaggle's base env.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

MODEL_REPO = "ResembleAI/chatterbox"
MODEL_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
DEFAULT_TEXT = "Olá! Esta é uma demonstração autorizada da minha voz em português."


def write_manifest(output: Path, data: dict) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "dispatch_result.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def synthesize(input_dir: Path, output: Path) -> dict:
    """Extract a reference and synthesize; success means a verified nonempty WAV only."""
    job = json.loads((input_dir / "job.json").read_text(encoding="utf-8"))
    manifest = {"schema_version": 1, "job_id": job["job_id"], "state": "failed",
                "model": MODEL_REPO, "model_revision": MODEL_REVISION, "license": "MIT",
                "language": "pt", "avatar_video_generated": False, "voice_training_performed": False,
                "method": "zero-shot voice cloning", "artifacts": []}
    try:
        import torch
        import soundfile as sf
        import imageio_ffmpeg
        from huggingface_hub import snapshot_download
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable")
        text = job["text"].strip()
        if not text or len(text) > 1000:
            raise ValueError("Invalid synthesis text")
        video = input_dir / job["video_name"]
        if video.parent.resolve() != input_dir.resolve() or not video.is_file():
            raise ValueError("Invalid video")
        output.mkdir(parents=True, exist_ok=True)
        # Reference and downloaded weights are temporary, not kernel output artifacts.
        with tempfile.TemporaryDirectory(prefix="avatar-voice-") as temp:
            reference = Path(temp) / "reference.wav"
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-v", "error", "-y", "-i", str(video),
                            "-map", "0:a:0", "-t", "12", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(reference)],
                           check=True, capture_output=True, timeout=120)
            with wave.open(str(reference), "rb") as audio:
                if audio.getnframes() < audio.getframerate() * 3:
                    raise ValueError("Reference shorter than three seconds")
            checkpoint = snapshot_download(MODEL_REPO, revision=MODEL_REVISION, token=False,
                                           cache_dir=str(Path(temp) / "weights"),
                                           allow_patterns=["ve.pt", "t3_mtl23ls_v2.safetensors", "s3gen.pt",
                                                           "grapheme_mtl_merged_expanded_v1.json", "conds.pt", "Cangjie5_TC.json"])
            model = ChatterboxMultilingualTTS.from_local(checkpoint, device="cuda")
            with torch.inference_mode():
                wav = model.generate(text, language_id="pt", audio_prompt_path=str(reference))
            target = output / "voice.wav"
            sf.write(str(target), wav.squeeze(0).detach().cpu().numpy(), model.sr, subtype="PCM_16")
        with wave.open(str(target), "rb") as audio:
            frames, rate = audio.getnframes(), audio.getframerate()
            if frames <= 0 or rate <= 0:
                raise ValueError("Empty synthesized audio")
        manifest.update(state="completed", sample_rate=rate, duration_seconds=frames / rate,
                        watermark="Chatterbox built-in Perth watermark; not independently verified",
                        artifacts=[{"name": "voice.wav", "bytes": target.stat().st_size,
                                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}])
    except Exception as exc:
        # No raw exception: external libraries can include local paths or credentials.
        manifest["error"] = "Voice synthesis failed: " + type(exc).__name__
    write_manifest(output, manifest)
    return manifest


def bootstrap(input_dir: Path, output: Path) -> int:
    """Use uv's managed 3.11 to avoid NumPy 1.25/Python 3.12 incompatibility."""
    job = json.loads((input_dir / "job.json").read_text(encoding="utf-8"))
    try:
        with tempfile.TemporaryDirectory(prefix="avatar-runtime-") as temp:
            tools = Path(temp) / "tools"
            subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--target", str(tools), "uv==0.8.22"], check=True)
            uv = tools / "bin" / "uv"
            runtime = Path(temp) / "venv"
            env = dict(os.environ, UV_CACHE_DIR=str(Path(temp) / "uv-cache"), UV_PYTHON_INSTALL_DIR=str(Path(temp) / "python"))
            subprocess.run([str(uv), "venv", "--python", "3.11", str(runtime)], env=env, check=True)
            python = runtime / "bin" / "python"
            subprocess.run([str(uv), "pip", "install", "--python", str(python), "chatterbox-tts==0.1.6",
                            "numpy==1.25.2", "torch==2.6.0", "torchaudio==2.6.0", "soundfile==0.13.1", "imageio-ffmpeg==0.6.0"], env=env, check=True)
            return subprocess.run([str(python), str(Path(__file__).resolve()), "--synthesize", str(input_dir), str(output)], env=env).returncode
    except Exception as exc:
        write_manifest(output, {"schema_version": 1, "job_id": job["job_id"], "state": "failed", "artifacts": [],
                                "avatar_video_generated": False, "error": "Dependency setup failed: " + type(exc).__name__})
        return 1


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--synthesize":
        result = synthesize(Path(sys.argv[2]), Path(sys.argv[3]))
        sys.exit(0 if result["state"] == "completed" else 1)
    else:
        candidates = list(Path("/kaggle/input").rglob("job.json"))
        if len(candidates) != 1:
            raise RuntimeError("Expected one private job dataset")
        sys.exit(bootstrap(candidates[0].parent, Path("/kaggle/working")))

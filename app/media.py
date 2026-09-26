import json
import subprocess
from pathlib import Path
from .config import DERIVED_DIR

class MediaValidationError(ValueError):
    pass


def _run(command: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except FileNotFoundError as exc:
        raise MediaValidationError("ffprobe/ffmpeg não está disponível no PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaValidationError("A análise da mídia excedeu o tempo permitido.") from exc


def probe_video(path: Path) -> dict:
    result = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height", "-of", "json", str(path)])
    if result.returncode != 0:
        raise MediaValidationError("Arquivo inválido ou não suportado pelo ffprobe.")
    try:
        data = json.loads(result.stdout)
        stream = next(s for s in data.get("streams", []) if s.get("codec_type") == "video")
        duration = float(data.get("format", {}).get("duration", 0))
        if duration <= 0 or not stream.get("width") or not stream.get("height"):
            raise ValueError
        return {"duration": duration, "width": int(stream["width"]), "height": int(stream["height"]), "codec": stream.get("codec_name", "desconhecido")}
    except (ValueError, KeyError, StopIteration, TypeError, json.JSONDecodeError) as exc:
        raise MediaValidationError("O vídeo não possui uma faixa de vídeo válida.") from exc


def extract_images(video: Path, job_id: str, duration: float) -> tuple[str | None, list[str]]:
    job_dir = DERIVED_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    thumbnail = job_dir / "thumbnail.jpg"
    thumb = _run(["ffmpeg", "-y", "-ss", "0", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(thumbnail)])
    thumbnail_rel = f"derived/{job_id}/thumbnail.jpg" if thumb.returncode == 0 and thumbnail.exists() else None
    frames: list[str] = []
    for index, fraction in enumerate((0.2, 0.5, 0.8), start=1):
        output = job_dir / f"sample-{index}.jpg"
        position = max(0, duration * fraction)
        result = _run(["ffmpeg", "-y", "-ss", f"{position:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "4", str(output)])
        if result.returncode == 0 and output.exists():
            frames.append(f"derived/{job_id}/{output.name}")
    return thumbnail_rel, frames

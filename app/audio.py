"""Local technical audio analysis; it does not identify or clone a voice."""
import json
import re
from pathlib import Path

from .media import _run


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _audio_quality(stream: dict, loudness: dict, silence: dict) -> tuple[str, list[str]]:
    notes: list[str] = []
    sample_rate = _as_float(stream.get("sample_rate"))
    channels = stream.get("channels")
    bitrate = _as_float(stream.get("bit_rate"))
    integrated = loudness.get("integrated_lufs")
    if sample_rate is not None and sample_rate < 16000:
        notes.append("A frequência de amostragem é baixa; prefira ao menos 16 kHz para fala futura.")
    if channels == 1:
        notes.append("Áudio mono detectado; estéreo pode ajudar na edição, mas fala mono também é utilizável.")
    if bitrate is not None and bitrate < 64000:
        notes.append("O bitrate de áudio é baixo; uma fonte menos comprimida pode preservar melhor a fala.")
    if integrated is not None and integrated < -30:
        notes.append("O áudio parece baixo; grave mais perto do microfone e reduza ruído ambiente.")
    elif integrated is not None and integrated > -12:
        notes.append("O áudio parece alto; evite clipping e mantenha distância estável do microfone.")
    silence_ratio = silence.get("ratio")
    if silence_ratio is not None and silence_ratio > 0.5:
        notes.append("Há muitos trechos silenciosos; inclua fala contínua e clara para uma etapa futura autorizada.")
    if not notes:
        notes.append("Sinais técnicos básicos adequados para fala. Isto não avalia identidade, conteúdo ou semelhança de voz.")
    return ("atenção" if len(notes) > 1 or any("baixa" in n or "baixo" in n for n in notes) else "adequada"), notes


def _parse_loudness(stderr: str) -> dict:
    # volumedetect is broadly available and reports mean/max volume in dB.
    mean = re.search(r"mean_volume:\s*([\-0-9.]+) dB", stderr)
    maximum = re.search(r"max_volume:\s*([\-0-9.]+) dB", stderr)
    return {
        "integrated_lufs": _as_float(mean.group(1)) if mean else None,
        "max_db": _as_float(maximum.group(1)) if maximum else None,
    }


def _parse_silence(stderr: str, duration: float | None) -> dict:
    intervals = [float(value) for value in re.findall(r"silence_duration:\s*([0-9.]+)", stderr)]
    total = sum(intervals)
    return {"total_seconds": round(total, 3), "ratio": round(total / duration, 3) if duration and duration > 0 else None}


def analyze_audio(path: Path) -> dict:
    """Return a serialisable, best-effort report without making audio failures fatal."""
    report = {"status": "absent", "quality": "não disponível", "metadata": {}, "loudness": {}, "silence": {}, "recommendations": [], "analysis_error": None}
    try:
        probe = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,sample_rate,channels,bit_rate,duration", "-of", "json", str(path)])
        if probe.returncode != 0:
            report.update(status="analysis_unavailable", analysis_error="Não foi possível inspecionar a faixa de áudio com ffprobe.")
            return report
        data = json.loads(probe.stdout)
        stream = next((item for item in data.get("streams", []) if item.get("codec_type") == "audio"), None)
        if stream is None:
            report["recommendations"] = ["Nenhuma faixa de áudio foi detectada. Para preparar um avatar falante, envie um vídeo com fala clara e contínua; a declaração única do upload já cobre imagem e voz."]
            return report
        duration = _as_float(stream.get("duration")) or _as_float(data.get("format", {}).get("duration"))
        metadata = {"codec": stream.get("codec_name", "desconhecido"), "duration_seconds": duration, "sample_rate_hz": int(stream["sample_rate"]) if str(stream.get("sample_rate", "")).isdigit() else None, "channels": stream.get("channels"), "bitrate_bps": int(stream["bit_rate"]) if str(stream.get("bit_rate", "")).isdigit() else None}
        report.update(status="detected", metadata=metadata)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        report.update(status="analysis_unavailable", analysis_error="Metadados de áudio inválidos ou incompletos.")
        return report
    try:
        loudness_run = _run(["ffmpeg", "-hide_banner", "-i", str(path), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"])
        if loudness_run.returncode == 0:
            report["loudness"] = _parse_loudness(loudness_run.stderr)
        silence_run = _run(["ffmpeg", "-hide_banner", "-i", str(path), "-map", "0:a:0", "-af", "silencedetect=noise=-35dB:d=0.5", "-f", "null", "-"])
        if silence_run.returncode == 0:
            report["silence"] = _parse_silence(silence_run.stderr, report["metadata"]["duration_seconds"])
        if loudness_run.returncode != 0 and silence_run.returncode != 0:
            report["analysis_error"] = "A análise de loudness/silêncio não pôde ser concluída, mas a faixa foi detectada."
    except Exception:  # Technical analysis is optional and must never fail an otherwise valid upload.
        report["analysis_error"] = "A análise de loudness/silêncio não está disponível neste ambiente."
    report["quality"], report["recommendations"] = _audio_quality(report["metadata"], report["loudness"], report["silence"])
    return report

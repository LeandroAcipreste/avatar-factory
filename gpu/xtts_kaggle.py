"""Teste de pesquisa com XTTS-v2: vídeo de referência -> WAV pt-BR.

Execute no Kaggle após instalar requirements-kaggle.txt. O primeiro uso baixa os
pesos do modelo no ambiente efêmero do Kaggle; este script não os baixa localmente.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"


def require_command(command: str) -> None:
    if shutil.which(command) is None:
        raise RuntimeError(f"{command} não está no PATH. Instale-o e tente novamente.")


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, check=True)


def extract_reference_audio(video: Path, reference_wav: Path) -> None:
    """Extrai áudio mono WAV 24 kHz, formato simples para referência do XTTS."""
    reference_wav.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "24000",
        "-c:a", "pcm_s16le", str(reference_wav),
    ])


def synthesize(reference_wav: Path, text: str, output_wav: Path) -> None:
    if not text.strip():
        raise ValueError("O texto não pode estar vazio.")
    if not reference_wav.is_file():
        raise FileNotFoundError(f"Referência não encontrada: {reference_wav}")

    import torch
    from TTS.api import TTS

    print(f"PyTorch: {torch.__version__}")
    print(f"CUDA disponível: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"GPU: {torch.cuda.get_device_name(0)}")
    else:
        print("AVISO: sem GPU CUDA; o teste pode ser muito lento.")

    output_wav.parent.mkdir(parents=True, exist_ok=True)
    tts = TTS(MODEL_NAME, gpu=torch.cuda.is_available())
    tts.tts_to_file(
        text=text,
        speaker_wav=str(reference_wav),
        language="pt",
        file_path=str(output_wav),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Teste XTTS-v2 pt-BR no Kaggle")
    parser.add_argument("--video", type=Path, required=True, help="Vídeo de referência")
    parser.add_argument("--text", required=True, help="Texto em português brasileiro")
    parser.add_argument("--reference-wav", type=Path, default=Path("outputs/reference.wav"))
    parser.add_argument("--output", type=Path, default=Path("outputs/xtts_ptbr.wav"))
    parser.add_argument("--skip-extract", action="store_true", help="Usa --reference-wav existente")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require_command("ffmpeg")
    if not args.skip_extract:
        if not args.video.is_file():
            raise FileNotFoundError(f"Vídeo não encontrado: {args.video}")
        extract_reference_audio(args.video, args.reference_wav)
    synthesize(args.reference_wav, args.text, args.output)
    print(f"WAV gerado: {args.output.resolve()}")
    print("Limitação: este teste não mede fidelidade/semelhança de voz e não faz lip-sync.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, FileNotFoundError, subprocess.CalledProcessError) as error:
        print(f"ERRO: {error}", file=sys.stderr)
        raise SystemExit(1)

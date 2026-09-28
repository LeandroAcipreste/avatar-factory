from pathlib import Path
from subprocess import CompletedProcess

from app.audio import analyze_audio


def test_audio_report_detects_metadata_and_technical_signals(monkeypatch):
    calls = []

    def fake_run(command):
        calls.append(command)
        if command[0] == "ffprobe":
            return CompletedProcess(command, 0, '{"format":{"duration":"20"},"streams":[{"codec_type":"audio","codec_name":"aac","sample_rate":"48000","channels":2,"bit_rate":"128000"}]}', "")
        if "volumedetect" in command:
            return CompletedProcess(command, 0, "", "mean_volume: -18.2 dB\nmax_volume: -2.0 dB")
        return CompletedProcess(command, 0, "", "silence_duration: 1.5")

    monkeypatch.setattr("app.audio._run", fake_run)
    report = analyze_audio(Path("voice.mp4"))

    assert report["status"] == "detected"
    assert report["metadata"]["codec"] == "aac"
    assert report["metadata"]["sample_rate_hz"] == 48000
    assert report["loudness"]["integrated_lufs"] == -18.2
    assert report["silence"]["ratio"] == 0.075
    assert report["quality"] == "adequada"
    assert len(calls) == 3


def test_audio_report_handles_missing_stream(monkeypatch):
    monkeypatch.setattr("app.audio._run", lambda command: CompletedProcess(command, 0, '{"streams": []}', ""))
    report = analyze_audio(Path("silent.mp4"))
    assert report["status"] == "absent"
    assert "Nenhuma faixa" in report["recommendations"][0]


def test_audio_report_keeps_upload_valid_when_ffmpeg_analysis_fails(monkeypatch):
    def fake_run(command):
        if command[0] == "ffprobe":
            return CompletedProcess(command, 0, '{"format":{"duration":"10"},"streams":[{"codec_type":"audio","codec_name":"opus","sample_rate":"48000","channels":1}]}', "")
        return CompletedProcess(command, 1, "", "unsupported filter")

    monkeypatch.setattr("app.audio._run", fake_run)
    report = analyze_audio(Path("voice.webm"))
    assert report["status"] == "detected"
    assert report["analysis_error"]

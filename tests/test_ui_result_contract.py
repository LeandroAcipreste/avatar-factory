import json
from types import SimpleNamespace

from app import database, main
from app.gpu_dispatch import DispatchResult
from test_ui_results import client, seed


def test_worker_dataclass_output_contract(client, monkeypatch):
    seed(status='gpu_running')
    folder = main.DATA_DIR / 'outputs' / 'job-1'
    folder.mkdir(parents=True)
    wav = folder / 'voice.wav'
    wav.write_bytes(b'RIFFfakeWAVE')
    result = DispatchResult('voice_ready', 'kaggle', 'Recuperado', 'u/kernel', 'u/dataset', str(wav), {'job_id': 'job-1', 'state': 'completed'})
    monkeypatch.setattr(main, 'KaggleGpuDispatcher', lambda: SimpleNamespace(collect=lambda _: result))
    job = client.get('/api/jobs/job-1').json()
    assert job['status'] == 'voice_ready'
    assert job['wav_available'] is True
    assert job['gpu_refs'] == {'kernel_ref': 'u/kernel', 'dataset_ref': 'u/dataset'}
    assert job['gpu_result']['state'] == 'completed'
    assert client.get('/jobs/job-1/audio.wav').status_code == 200
    assert client.get('/media/outputs/job-1/voice.wav').status_code == 404
    assert json.loads(database.get_job('job-1')['gpu_result_json'])['wav_path'] == str(wav)

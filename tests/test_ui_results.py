import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from app import database, main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DATABASE_PATH', tmp_path / 'private.db')
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    for name in ('DERIVED_DIR', 'UPLOAD_DIR'):
        folder = tmp_path / name.lower()
        folder.mkdir()
        monkeypatch.setattr(main, name, folder)
    with TestClient(main.app) as client:
        yield client


def seed(job_id='job-1', **changes):
    data = dict(id=job_id, original_filename='reference.mp4', stored_filename='private.mp4',
                consent_name='Pessoa', created_at='2026-09-29', status='uploaded', file_size=10,
                speech_text='Olá, mundo!', gpu_result_json='{}', gpu_refs_json='{}')
    data.update(changes)
    database.create_job(data)


def test_speech_upload_persisted_and_auto_dispatched(client, monkeypatch):
    calls = []
    def dispatch(job):
        calls.append(job)
        return SimpleNamespace(status='gpu_queued', provider='kaggle', message='Pendente', kernel_ref='u/k', dataset_ref=None, output_path=None, manifest=None)
    monkeypatch.setattr(main, 'KaggleGpuDispatcher', lambda: SimpleNamespace(dispatch=dispatch))
    monkeypatch.setattr(main, 'probe_video', lambda _: dict(duration=12, width=1280, height=720, codec='h264'))
    monkeypatch.setattr(main, 'analyze_audio', lambda _: dict(status='detected', quality='adequada'))
    monkeypatch.setattr(main, 'extract_images', lambda *_: (None, []))
    response = client.post('/api/jobs', data={'consent_name': 'Pessoa', 'consent': 'on', 'speech_text': ' Olá Brasil! '}, files={'video': ('v.mp4', b'video', 'video/mp4')})
    assert response.status_code == 201
    job = response.json()
    assert job['speech_text'] == 'Olá Brasil!'
    assert len(calls) == 1 and calls[0]['speech_text'] == 'Olá Brasil!'
    assert calls[0]['synthesis_text'] == 'Olá Brasil!'
    assert job['gpu_refs'] == {'kernel_ref': 'u/k'}
    assert database.get_job(job['id'])['speech_text'] == 'Olá Brasil!'
    assert 'name="speech_text"' in client.get('/').text


def test_private_files_not_public_and_images_preserved(client):
    seed()
    folder = main.DERIVED_DIR / 'job-1'
    folder.mkdir()
    (folder / 'thumbnail.jpg').write_bytes(b'jpeg')
    (folder / 'result.json').write_text('private')
    assert client.get('/media/derived/job-1/thumbnail.jpg').status_code == 200
    for path in ('/media/private.db', '/media/uploads/private.mp4', '/media/kaggle-staging/secret', '/media/derived/job-1/result.json'):
        assert client.get(path).status_code == 404


def test_collect_once_and_download_only_job_wav(client, monkeypatch):
    seed(status='gpu_queued')
    folder = main.DERIVED_DIR / 'job-1'
    folder.mkdir()
    (folder / 'speech.wav').write_bytes(b'RIFFfakeWAVE')
    calls = []
    def collect(job):
        calls.append(job['id'])
        return {'status': 'gpu_completed', 'result': {'state': 'completed', 'wav_path': 'speech.wav'}}
    monkeypatch.setattr(main, 'KaggleGpuDispatcher', lambda: SimpleNamespace(collect=collect))
    response = client.get('/jobs/job-1')
    assert response.status_code == 200
    assert 'Baixar áudio WAV' in response.text
    assert calls == ['job-1']
    assert client.get('/jobs/job-1/audio.wav').content == b'RIFFfakeWAVE'
    assert client.get('/api/jobs/job-1').json()['wav_available']
    assert calls == ['job-1']
    seed('job-2', gpu_result_json=json.dumps({'wav_path': str(folder / 'speech.wav')}))
    assert client.get('/jobs/job-2/audio.wav').status_code == 404
    database.update_job('job-1', gpu_result_json=json.dumps({'wav_path': str(main.DATA_DIR / 'private.db')}))
    assert client.get('/jobs/job-1/audio.wav').status_code == 404


def test_collect_failure_remains_retryable_without_secret(client, monkeypatch):
    seed(status='gpu_queued')
    def collect(_):
        raise RuntimeError('token=secret')
    monkeypatch.setattr(main, 'KaggleGpuDispatcher', lambda: SimpleNamespace(collect=collect))
    response = client.get('/api/jobs/job-1')
    assert response.status_code == 200
    assert response.json()['status'] == 'gpu_queued'
    assert 'secret' not in response.text


def test_text_limit_and_missing_wav(client):
    response = client.post('/api/jobs', data={'consent_name': 'Pessoa', 'consent': 'on', 'speech_text': 'x' * 1001}, files={'video': ('v.mp4', b'video')})
    assert response.status_code == 400
    seed()
    assert client.get('/jobs/job-1/audio.wav').status_code == 404
    assert 'Nenhum WAV disponível' in client.get('/jobs/job-1').text


def test_migration_idempotent(client):
    database.init_db()
    seed()
    assert database.get_job('job-1')['speech_text'] == 'Olá, mundo!'

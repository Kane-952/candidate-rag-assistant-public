from urllib.parse import urlparse, parse_qs

from fastapi.testclient import TestClient

from backend.app.config import Settings

EMAIL = 'invited@example.com'
BASE_URL = 'http://127.0.0.1:8000'


def make_settings(tmp_path):
    return Settings(
        _env_file=None, feedback_db=tmp_path / 'feedback.sqlite3',
        auth_db=tmp_path / 'auth.sqlite3', auth_outbox_dir=tmp_path / 'outbox',
        auth_public_url=BASE_URL,
    )


def client_for(app):
    return TestClient(app, base_url=BASE_URL, client=('127.0.0.1', 50000))


def login(client, settings, email=EMAIL):
    response = client.post('/auth/request', json={'email': email})
    assert response.status_code == 200
    files = sorted(settings.auth_outbox_dir.glob('*.txt'), key=lambda p: p.stat().st_mtime_ns)
    assert files
    link = files[-1].read_text(encoding='utf-8').splitlines()[-1]
    token = parse_qs(urlparse(link).fragment)['token'][0]
    response = client.post('/auth/verify', json={'token': token})
    assert response.status_code == 200
    return token

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from backend.app.auth import AuthStore
from backend.app.config import Settings
from backend.app.main import create_app
from backend.tests.auth_helpers import EMAIL


def settings_for(tmp_path):
    return Settings(
        _env_file=None, auth_db=tmp_path / 'auth.sqlite3',
        feedback_db=tmp_path / 'feedback.sqlite3',
        auth_public_url='https://portfolio.example', auth_delivery='smtp',
        smtp_host='smtp.example', smtp_username='sender',
        smtp_password='test-password', smtp_from='sender@example.com',
        inspector_admin_emails=[EMAIL],
    )


def verify(client, app, settings, email, headers=None):
    token = app.state.auth.issue_link(
        email, 'test-request', settings.auth_link_ttl_seconds,
        settings.auth_email_cooldown_seconds, settings.auth_ip_window_seconds,
        settings.auth_ip_max_requests,
    )
    assert token
    return client.post('/auth/verify',
                       json={'token': token, 'ip_address': '192.0.2.250'},
                       headers=headers or {})


@pytest.mark.parametrize(('peer', 'headers', 'expected'), [
    ('203.0.113.10',
     {'X-Forwarded-For': '198.51.100.99', 'X-Real-IP': '198.51.100.99'},
     '203.0.113.10'),
    ('127.0.0.1',
     {'X-Forwarded-For': '198.51.100.99, 203.0.113.44',
      'X-Real-IP': '203.0.113.44'}, '203.0.113.44'),
    ('127.0.0.1',
     {'X-Forwarded-For': 'not-an-ip', 'X-Real-IP': '203.0.113.45'},
     '203.0.113.45'),
    ('127.0.0.1', {}, '127.0.0.1'),
])
def test_successful_login_records_server_derived_ip(tmp_path, peer, headers, expected):
    settings = settings_for(tmp_path)
    app = create_app(settings)
    with TestClient(app, base_url=settings.auth_public_url, client=(peer, 50000)) as client:
        before = int(time.time())
        assert verify(client, app, settings, EMAIL, headers).status_code == 200
        records = client.get('/inspector/access-log').json()['records']
        assert len(records) == 1
        assert records[0]['user_email'] == EMAIL
        assert records[0]['ip_address'] == expected
        assert before <= records[0]['login_time'] <= int(time.time())
        assert len(records[0]['session_id']) == 36
        assert client.post('/auth/verify', json={'token': 'x' * 32}).status_code == 400
        assert len(client.get('/inspector/access-log').json()['records']) == 1


def test_access_log_is_admin_only_and_filterable(tmp_path):
    settings = settings_for(tmp_path)
    app = create_app(settings)
    with TestClient(app, base_url=settings.auth_public_url,
                    client=('127.0.0.1', 50000)) as admin, TestClient(
                        app, base_url=settings.auth_public_url,
                        client=('198.51.100.20', 50000)) as viewer:
        assert viewer.get('/inspector/access-log').status_code == 401
        assert verify(admin, app, settings, EMAIL,
                      {'X-Real-IP': '203.0.113.50'}).status_code == 200
        assert verify(viewer, app, settings, 'viewer@example.org',
                      {'X-Forwarded-For': '192.0.2.99'}).status_code == 200
        denied = viewer.get('/inspector/access-log')
        assert denied.status_code == 403
        assert '203.0.113.50' not in denied.text
        assert admin.get('/inspector/access-log', params={'email': 'VIEWER@EXAMPLE.ORG'}
                         ).json()['records'][0]['ip_address'] == '198.51.100.20'
        assert len(admin.get('/inspector/access-log').json()['records']) == 2
        assert '登录访问日志' in admin.get('/inspector').text
        assert '/inspector/access-log' in admin.get('/inspector/app.js').text


def test_existing_access_log_adds_nullable_ip_column(tmp_path):
    path = tmp_path / 'auth.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE access_events (
            id INTEGER PRIMARY KEY, email TEXT NOT NULL, session_id TEXT NOT NULL,
            event TEXT NOT NULL, occurred_at INTEGER NOT NULL)''')
        db.execute('INSERT INTO access_events(email,session_id,event,occurred_at) VALUES(?,?,?,?)',
                   (EMAIL, 'old-session', 'login', 123))
    store = AuthStore(path)
    assert store.recent_logins() == [{
        'user_email': EMAIL, 'ip_address': None,
        'login_time': 123, 'session_id': 'old-session',
    }]
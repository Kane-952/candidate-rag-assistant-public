import sqlite3
from urllib.parse import parse_qs, urlparse
from unittest.mock import Mock

import pytest

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models.schemas import ChatResponse
from backend.tests.auth_helpers import BASE_URL, EMAIL, client_for, login, make_settings


def test_private_routes_links_logout_and_audit(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with client_for(app) as client:
        page = client.get('/', headers={'Accept': 'text/html'}, follow_redirects=False)
        assert page.status_code == 303 and page.headers['location'] == '/login'
        assert client.get('/login').status_code == 200
        assert client.get('/login.css').status_code == 200
        assert client.get('/login.js').status_code == 200
        for path in ('/app.js', '/style.css', '/assets/demo-avatar.svg', '/docs', '/openapi.json'):
            assert client.get(path).status_code == 401
        assert client.get('/health').status_code == 503  # Public, but RAG is not configured in this test.
        for path in ('/chat', '/chat/stream', '/feedback'):
            assert client.post(path, json={}).status_code == 401

        # Any syntactically valid mailbox can receive a link.
        arbitrary = client.post('/auth/request', json={'email': 'other@example.com'})
        assert arbitrary.status_code == 200
        assert len(list(settings.auth_outbox_dir.glob('*.txt'))) == 1
        token = login(client, settings)
        cookie = client.cookies.get('candidate_session')
        assert cookie and token not in cookie
        set_cookie = client.post('/auth/verify', json={'token': token})
        assert set_cookie.status_code == 400  # one-time use
        assert client.get('/').status_code == 200
        assert client.get('/app.js').status_code == 200
        assert client.get('/health').status_code == 503
        assert client.post('/chat', json={}).status_code == 422
        assert client.post('/chat', json={}, headers={'Origin':'https://evil.example'}).status_code == 403
        assert client.post('/chat', json={}, headers={'Sec-Fetch-Site':'cross-site'}).status_code == 403
        assert client.post('/auth/logout').status_code == 200
        assert client.get('/health').status_code == 503
    with sqlite3.connect(settings.auth_db) as db:
        events = db.execute('SELECT email,session_id,event,occurred_at FROM access_events ORDER BY id').fetchall()
        assert [row[2] for row in events] == ['login', 'logout']
        assert events[0][0] == EMAIL and events[0][1] == events[1][1]
        assert len(events[0][1]) == 36 and isinstance(events[0][3], int)
        assert token not in db.execute('SELECT token_hash FROM login_links').fetchall().__repr__()


def test_expiry_and_session_persistence(tmp_path):
    settings = make_settings(tmp_path)
    with client_for(create_app(settings)) as client:
        login(client, settings)
        cookie = client.cookies.get('candidate_session')
        with client_for(create_app(settings)) as another_client:
            another_client.cookies.set('candidate_session', cookie)
            assert another_client.get('/app.js').status_code == 200
            with sqlite3.connect(settings.auth_db) as db:
                db.execute('UPDATE sessions SET expires_at=0')
            assert another_client.get('/app.js').status_code == 401
        assert client.get('/app.js').status_code == 401
        with sqlite3.connect(settings.auth_db) as db:
            db.execute('UPDATE email_cooldowns SET last_issued_at=0')
        login(client, settings)
        assert client.get('/app.js').status_code == 200


def test_expired_link_and_rate_limit(tmp_path):
    settings = make_settings(tmp_path)
    with client_for(create_app(settings)) as client:
        assert client.post('/auth/request', json={'email': EMAIL}).status_code == 200
        link = next(settings.auth_outbox_dir.glob('*.txt')).read_text(encoding='utf-8').splitlines()[-1]
        token = parse_qs(urlparse(link).fragment)['token'][0]
        assert link.startswith(BASE_URL + '/login#token=')
        with sqlite3.connect(settings.auth_db) as db:
            db.execute('UPDATE login_links SET expires_at=0')
        assert client.post('/auth/verify', json={'token': token}).status_code == 400
        first_message = client.post('/auth/request', json={'email': EMAIL}).json()['message']
        for _ in range(3):
            assert client.post('/auth/request', json={'email': EMAIL}).json()['message'] == first_message
        assert len(list(settings.auth_outbox_dir.glob('*.txt'))) == 1
        with sqlite3.connect(settings.auth_db) as db:
            db.execute('UPDATE email_cooldowns SET last_issued_at=0')
        assert client.post('/auth/request', json={'email': EMAIL}).status_code == 200
        assert len(list(settings.auth_outbox_dir.glob('*.txt'))) == 2


def test_email_cooldown_survives_restart_without_invalidating_first_link(tmp_path):
    settings = make_settings(tmp_path)
    with client_for(create_app(settings)) as first:
        sent = first.post('/auth/request', json={'email': 'new.person@example.org'})
        assert sent.status_code == 200
        link = next(settings.auth_outbox_dir.glob('*.txt')).read_text(encoding='utf-8').splitlines()[-1]
        token = parse_qs(urlparse(link).fragment)['token'][0]
    with client_for(create_app(settings)) as second:
        repeated = second.post('/auth/request', json={'email': 'new.person@example.org'})
        assert repeated.json() == sent.json()
        assert len(list(settings.auth_outbox_dir.glob('*.txt'))) == 1
        assert second.post('/auth/verify', json={'token': token}).status_code == 200
        assert second.post('/auth/verify', json={'token': token}).status_code == 400


def test_email_validation_cooldown_and_ip_limit_have_generic_response(tmp_path):
    settings = make_settings(tmp_path)
    settings.auth_ip_max_requests = 2
    with client_for(create_app(settings)) as client:
        assert client.post('/auth/request', json={'email': 'not-an-email'}).status_code == 422
        first = client.post('/auth/request', json={'email': 'first@example.com'})
        second = client.post('/auth/request', json={'email': 'second+tag@example.org'})
        limited = client.post('/auth/request', json={'email': 'third@example.net'})
        assert [response.status_code for response in (first, second, limited)] == [200, 200, 200]
        assert first.json() == second.json() == limited.json()
        assert len(list(settings.auth_outbox_dir.glob('*.txt'))) == 2
        with sqlite3.connect(settings.auth_db) as db:
            requests = db.execute('SELECT email_hash,ip_hash FROM login_requests').fetchall()
            assert len(requests) == 3
            assert all('@' not in value for row in requests for value in row)


def test_same_client_conversation_id_is_scoped_to_email(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with client_for(app) as first, client_for(app) as second:
        login(first, settings)
        login(second, settings, 'second@example.com')
        pipeline = Mock()
        pipeline.answer.return_value = ChatResponse(answer='我做过 RAG。', refused=False)
        app.state.pipeline = pipeline
        first_result = first.post('/chat', json={'session_id':'shared', 'message':'项目？'}).json()
        assert second.post('/chat', json={'session_id':'shared', 'message':'经历？'}).status_code == 200
        assert pipeline.answer.call_args.args[0] == []
        rating = {'answer_id':first_result['answer_id'], 'session_id':'shared', 'rating':'up'}
        assert second.post('/feedback', json=rating).status_code == 404
        assert first.post('/feedback', json=rating).status_code == 200


def test_public_deployment_needs_https_and_smtp(tmp_path):
    with pytest.raises(ValueError):
        Settings(_env_file=None, auth_public_url='http://portfolio.example', auth_delivery='smtp')
    with pytest.raises(ValueError):
        Settings(_env_file=None, auth_public_url='https://portfolio.example', auth_delivery='local')
    production = dict(
        _env_file=None, app_env='production', auth_public_url='https://portfolio.example',
        auth_delivery='smtp', llm_api_key='test', llm_model='test',
        smtp_host='smtp.example', smtp_username='sender', smtp_password='test-password',
        smtp_from='sender@example.com',
    )
    assert Settings(**production).secure_cookie
    for unsafe in (
        {'auth_public_url': 'https://localhost'},
        {'auth_public_url': 'http://portfolio.example'},
        {'auth_delivery': 'local'},
        {'debug': True},
    ):
        with pytest.raises(ValueError):
            Settings(**{**production, **unsafe})
    settings = make_settings(tmp_path)
    # The local inbox mode is never reachable from a non-loopback client.
    from fastapi.testclient import TestClient
    with TestClient(create_app(settings), base_url=BASE_URL, client=('203.0.113.10', 50000)) as remote:
        assert remote.get('/login').status_code == 403


def test_smtp_mode_sends_magic_link_and_sets_secure_cookie(tmp_path):
    from fastapi.testclient import TestClient
    from unittest.mock import patch
    settings = Settings(
        _env_file=None, auth_db=tmp_path / 'auth.sqlite3',
        feedback_db=tmp_path / 'feedback.sqlite3',
        auth_public_url='https://portfolio.example', auth_delivery='smtp',
        smtp_host='smtp.example',
        smtp_username='sender', smtp_password='test-password',
        smtp_from='sender@example.com',
    )
    with patch('backend.app.auth.smtplib.SMTP') as smtp_class:
        with TestClient(create_app(settings), base_url='https://portfolio.example',
                        client=('203.0.113.10', 50000)) as client:
            assert client.post('/auth/request', json={'email':EMAIL}).status_code == 200
            smtp = smtp_class.return_value.__enter__.return_value
            smtp.starttls.assert_called_once()
            smtp.login.assert_called_once_with('sender', 'test-password')
            sent = smtp.send_message.call_args.args[0]
            assert sent['To'] == EMAIL
            assert sent['Subject'] == 'Demo AI Assistant 登录链接'
            assert '你正在访问 Demo 的 AI 求职助手。' in sent.get_content()
            assert '只能使用一次' in sent.get_content()
            assert '作品集' not in sent.get_content()
            link = next(line for line in sent.get_content().splitlines() if line.startswith('https://'))
            token = parse_qs(urlparse(link).fragment)['token'][0]
            response = client.post('/auth/verify', json={'token':token})
            assert response.status_code == 200
            assert 'Secure' in response.headers['set-cookie']
            assert 'HttpOnly' in response.headers['set-cookie']
            assert 'SameSite=strict' in response.headers['set-cookie']
            assert client.get('/').status_code == 200


def test_qq_smtp_ssl_magic_link_and_redacted_error(tmp_path, caplog):
    import smtplib
    import ssl
    from unittest.mock import patch
    from fastapi.testclient import TestClient

    settings = Settings(
        _env_file=None,
        auth_db=tmp_path / 'auth.sqlite3',
        feedback_db=tmp_path / 'feedback.sqlite3',
        auth_public_url='https://portfolio.example',
        auth_delivery='smtp',
        smtp_host='smtp.qq.com',
        smtp_port=465,
        smtp_security='ssl',
        smtp_username='sender@example.com',
        smtp_password='qq-authorization-code',
        smtp_from='sender@example.com',
    )
    with patch('backend.app.auth.smtplib.SMTP_SSL') as smtp_class:
        with TestClient(create_app(settings), base_url='https://portfolio.example',
                        client=('203.0.113.10', 50000)) as client:
            assert client.post('/auth/request', json={'email': EMAIL}).status_code == 200
            args = smtp_class.call_args.args
            assert args == ('smtp.qq.com', 465)
            assert smtp_class.call_args.kwargs['timeout'] == 15
            assert isinstance(smtp_class.call_args.kwargs['context'], ssl.SSLContext)
            smtp = smtp_class.return_value.__enter__.return_value
            smtp.login.assert_called_once_with('sender@example.com', 'qq-authorization-code')
            sent = smtp.send_message.call_args.args[0]
            assert sent['From'] == 'sender@example.com' and sent['To'] == EMAIL
            assert sent['Subject'] == 'Demo AI Assistant 登录链接'
            assert '你正在访问 Demo 的 AI 求职助手。' in sent.get_content()
            assert '只能使用一次' in sent.get_content()
            assert '作品集' not in sent.get_content()
            link = next(line for line in sent.get_content().splitlines()
                        if line.startswith('https://'))
            token = parse_qs(urlparse(link).fragment)['token'][0]
            assert client.post('/auth/verify', json={'token': token}).status_code == 200
            assert client.post('/auth/verify', json={'token': token}).status_code == 400

    with patch('backend.app.auth.smtplib.SMTP_SSL',
               side_effect=smtplib.SMTPAuthenticationError(
                   535, b'bad qq-authorization-code')):
        with TestClient(create_app(settings), base_url='https://portfolio.example',
                        client=('203.0.113.11', 50000)) as client:
            assert client.post('/auth/request', json={'email': 'other@example.org'}).status_code == 200
    assert 'magic_link_delivery_failed error_type=SMTPAuthenticationError' in caplog.text
    assert 'qq-authorization-code' not in caplog.text
    assert 'token=' not in caplog.text

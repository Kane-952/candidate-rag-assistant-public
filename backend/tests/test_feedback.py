from unittest.mock import Mock
import sqlite3
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.config import Settings
from backend.app.models.schemas import ChatResponse
from backend.tests.auth_helpers import client_for, login, make_settings


def test_feedback_persistence_ownership_and_changes(tmp_path):
    settings = make_settings(tmp_path)
    with client_for(create_app(settings)) as client:
        login(client, settings)
        client.app.state.pipeline = Mock()
        client.app.state.pipeline.answer.return_value = ChatResponse(answer='测试回复', refused=True)
        result = client.post('/chat', json={'session_id': 'test', 'message': '测试问题'}).json()
        body = {'session_id': 'test', 'answer_id': result['answer_id'], 'rating': 'up'}
        assert client.post('/feedback', json={**body, 'session_id': 'other'}).status_code == 404
        assert client.post('/feedback', json={**body, 'rating': 'invalid'}).status_code == 422
        assert client.post('/feedback', json=body).json()['saved']
        assert client.post('/feedback', json=body).status_code == 200
        with sqlite3.connect(settings.feedback_db) as db:
            assert db.execute('SELECT count(*) FROM feedback_events').fetchone()[0] == 1
        session_cookie = client.cookies.get('candidate_session')
    with client_for(create_app(settings)) as client:
        client.cookies.set('candidate_session', session_cookie)
        assert client.get('/app.js').status_code == 200
        assert client.post('/feedback', json={**body, 'rating': 'down', 'user_feedback_reason': '回答不够清楚'}).status_code == 200
        with sqlite3.connect(settings.feedback_db) as db:
            assert db.execute('SELECT rating FROM feedback').fetchone()[0] == 'down'
            assert db.execute('SELECT question,answer FROM answers').fetchone() == ('测试问题', '测试回复')
        assert client.post('/feedback', json={**body, 'rating': 'none'}).status_code == 200
        with sqlite3.connect(settings.feedback_db) as db:
            assert db.execute('SELECT rating FROM feedback').fetchone()[0] == 'none'
            assert db.execute('SELECT count(*) FROM feedback_events').fetchone()[0] == 3

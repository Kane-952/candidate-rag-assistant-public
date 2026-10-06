import json
import logging
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models.schemas import ChatResponse
from backend.app.perf import current_trace
from backend.tests.auth_helpers import EMAIL, login, make_settings


class FakeDense:
    def __init__(self):
        self.index = object()
        self.model = None

    def _load_model(self):
        self.model = object()


class FakeReranker:
    def __init__(self):
        self.model = None

    def _load_model(self):
        self.model = object()


def test_public_health_checks_all_required_resources_in_production(tmp_path):
    index_dir = tmp_path / 'indexes'
    index_dir.mkdir()
    for name in ('chunks.jsonl', 'vectors.faiss', 'manifest.json'):
        (index_dir / name).write_text('test', encoding='utf-8')
    settings = Settings(
        _env_file=None, app_env='production', auth_public_url='https://portfolio.example',
        auth_delivery='smtp',
        smtp_host='smtp.example', smtp_username='sender', smtp_password='test-password',
        smtp_from='sender@example.com', llm_api_key='test', llm_model='test',
        auth_db=tmp_path / 'auth.sqlite3', feedback_db=tmp_path / 'feedback.sqlite3',
        index_dir=index_dir,
    )
    dense = FakeDense()
    reranker = FakeReranker()
    pipeline = SimpleNamespace(chunks=[object()], retriever=SimpleNamespace(dense=dense),
                               reranker=reranker)
    app = create_app(settings)
    with patch('backend.app.main.RAGPipeline', return_value=pipeline):
        with TestClient(app, base_url='https://portfolio.example') as client:
            assert client.get('/health').status_code == 200  # No login cookie.
            assert client.get('/health').json() == {'status': 'ok'}
            assert dense.model is not None and reranker.model is not None
            (index_dir / 'vectors.faiss').unlink()
            assert client.get('/health').status_code == 503
            (index_dir / 'vectors.faiss').write_text('test', encoding='utf-8')
            auth_path = app.state.auth.path
            app.state.auth.path = tmp_path / 'missing-auth.sqlite3'
            assert client.get('/health').status_code == 503
            app.state.auth.path = auth_path
            feedback_path = app.state.feedback.path
            app.state.feedback.path = tmp_path / 'missing-feedback.sqlite3'
            assert client.get('/health').status_code == 503
            app.state.feedback.path = feedback_path
            dense.model = None
            assert client.get('/health').status_code == 503
            dense._load_model()
            app.state.rag_init_status = 'failed'
            assert client.get('/health').status_code == 503


def test_missing_index_keeps_health_unavailable_and_chat_closed(tmp_path, caplog):
    settings = make_settings(tmp_path)
    settings.index_dir = tmp_path / 'missing-index'
    settings.llm_api_key = 'test'
    settings.llm_model = 'test'
    app = create_app(settings)
    with TestClient(app, base_url=settings.auth_public_url,
                    client=('127.0.0.1', 50000)) as client:
        assert app.state.rag_init_status == 'failed'
        assert app.state.rag_init_error == 'IndexUnavailable'
        assert client.get('/health').json() == {'status': 'unavailable'}
        assert client.get('/health').status_code == 503
        login(client, settings)
        result = client.post('/chat', json={'session_id': 'a', 'message': '项目？'})
        assert result.status_code == 503
        assert '尚未就绪' in result.json()['detail']
        assert app.state.pipeline is None
    assert 'rag_prewarm_failed stage=knowledge_base_and_index error_type=IndexUnavailable' in caplog.text


def test_auth_database_init_failure_still_exposes_unavailable_health(tmp_path, caplog):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with patch('backend.app.main.AuthStore', side_effect=sqlite3.OperationalError('unavailable')):
        with TestClient(app, base_url=settings.auth_public_url,
                        client=('127.0.0.1', 50000)) as client:
            response = client.get('/health')
            assert response.status_code == 503
            assert response.json() == {'status': 'unavailable'}
            assert client.get('/login').status_code == 503
    assert 'storage_init_failed resource=auth error_type=OperationalError' in caplog.text


def test_normal_request_log_has_identity_timing_and_no_question(tmp_path, caplog):
    settings = make_settings(tmp_path)
    app = create_app(settings)

    def answer(history, question):
        trace = current_trace()
        trace.add('retrieval_total', .012)
        trace.add('reranker_total', .006)
        trace.add('generation_total', .031)
        return ChatResponse(answer='我做过相关项目。', refused=False)

    with TestClient(app, base_url=settings.auth_public_url,
                    client=('127.0.0.1', 50000)) as client:
        login(client, settings)
        app.state.pipeline = Mock(answer=Mock(side_effect=answer))
        with caplog.at_level(logging.INFO, logger='backend.app.perf'):
            result = client.post('/chat', json={'session_id': 'audit',
                                                'message': '私密问题 token=do-not-log'})
        assert result.status_code == 200
    entries = [json.loads(record.message.removeprefix('perf_trace '))
               for record in caplog.records if record.message.startswith('perf_trace ')
               and '"route": "/chat"' in record.message]
    assert len(entries) == 1
    entry = entries[0]
    assert entry['request_id'] and entry['user_email'] == EMAIL
    assert entry['conversation_id'] == f'{EMAIL}:audit'
    assert entry['answer_status'] == 'ANSWER' and entry['error_type'] is None
    assert entry['durations_ms']['retrieval_total'] == 12
    assert entry['durations_ms']['reranker_total'] == 6
    assert entry['durations_ms']['generation_total'] == 31
    assert entry['total_ms'] >= 0
    assert '私密问题' not in caplog.text and 'do-not-log' not in caplog.text

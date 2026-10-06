from unittest.mock import Mock

from backend.app.errors import LLMUnavailable
from backend.app.main import create_app
from backend.app.models.schemas import ChatResponse
from backend.tests.auth_helpers import client_for, login, make_settings


def test_api_ui_validation_history_and_safe_error(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with client_for(app) as client:
        login(client, settings)
        assert client.get('/').status_code == 200
        assert '你好，我是示例候选人' in client.get('/').text
        assert '虚构候选人资料 · RAG 工程演示' in client.get('/').text
        assert 'data-question' not in client.get('/').text
        assert client.get('/assets/demo-avatar.svg').status_code == 200
        assert client.get('/app.js').status_code == 200
        assert client.get('/health').status_code == 503
        assert client.get('/health').json() == {'status': 'unavailable'}
        assert client.post('/chat', json={'session_id': 'a', 'message': '  '}).status_code == 422
        pipeline = Mock()
        pipeline.answer.return_value = ChatResponse(answer='资料不足', refused=True)
        app.state.pipeline = pipeline
        assert client.post('/chat', json={'session_id': 'a', 'message': '项目？'}).json()['refused']
        client.post('/chat', json={'session_id': 'a', 'message': '为什么？'})
        history, _ = pipeline.answer.call_args.args
        assert len(history) == 2
        client.post('/chat', json={'session_id': 'b', 'message': '新问题'})
        assert pipeline.answer.call_args.args[0] == []
        pipeline.answer.side_effect = LLMUnavailable('LLM API 请求超时，请稍后重试。')
        response = client.post('/chat', json={'session_id': 'a', 'message': '项目？'})
        assert response.status_code == 503 and '超时' in response.json()['detail']
        cors = client.options('/chat', headers={'Origin':'http://localhost:8000', 'Access-Control-Request-Method':'POST'})
        assert cors.headers['access-control-allow-origin'] == 'http://localhost:8000'

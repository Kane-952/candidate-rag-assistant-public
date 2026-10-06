import json
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.errors import LLMUnavailable
from backend.app.llm.client import LLMClient, partial_answer, without_pending_citations
from backend.app.main import create_app
from backend.app.models.schemas import ChatResponse, SearchHit
from backend.tests.auth_helpers import client_for, login, make_settings, EMAIL
from backend.app.auth import scoped_conversation


def stream_response(chunks, finish='stop', done=True):
    lines = [f'data: {json.dumps({"choices":[{"delta":{"content":part},"finish_reason":None}]}, ensure_ascii=False)}\n\n' for part in chunks]
    lines.append(f'data: {json.dumps({"choices":[{"delta":{},"finish_reason":finish}]})}\n\n')
    if done:
        lines.append('data: [DONE]\n\n')
    return httpx.Response(200, stream=httpx.ByteStream(''.join(lines).encode()))


def test_deepseek_stream_and_partial_json():
    requests = []
    answer = '我使用 LoRA。[' + 'a' * 20 + ']'
    raw = json.dumps({'answer':answer,'evidence_ids':['a'*20],'refused':False}, ensure_ascii=False)
    parts = [raw[:12], raw[12:18], raw[18:30], raw[30:]]
    def respond(request):
        requests.append(json.loads(request.content))
        return stream_response(parts)
    client = LLMClient(Settings(_env_file=None, llm_api_key='test', llm_model='deepseek-flash'))
    client.http.close()
    client.http = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        hit = SearchHit(chunk_id='a'*20,source='resume/resume.md',title='项目',text='使用 LoRA',score=1)
        visible = []
        result = client.grounded_answer('用了什么？',[hit],on_text=visible.append)
        assert result.answer == answer
        assert ''.join(visible) == '我使用 LoRA。'
        assert requests[0]['stream'] is True and requests[0]['model'] == 'deepseek-flash'
        assert 'thinking' not in requests[0]
    finally:
        client.close()
    assert partial_answer('{"answer":"我用\\u') == '我用'
    assert without_pending_citations('你好['+'a'*5) == '你好'


def test_stream_requires_normal_finish_and_done():
    for finish, done in [('length',True),('stop',False)]:
        client = LLMClient(Settings(_env_file=None, llm_api_key='test', llm_model='deepseek-flash'))
        client.http.close()
        client.http = httpx.Client(transport=httpx.MockTransport(lambda request: stream_response(['part'],finish,done)))
        try:
            try:
                list(client.stream_generate('prompt',{}))
                assert False, 'incomplete stream was accepted'
            except LLMUnavailable:
                pass
        finally:
            client.close()


def test_stream_endpoint_preserves_existing_chat_contract(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with client_for(app) as client:
        login(client, settings)
        pipeline = Mock()
        def answer(history, question, on_text=None):
            if on_text:
                on_text('我在')
                on_text('项目中用了 LoRA。')
            return ChatResponse(answer='我在项目中用了 LoRA。',refused=False)
        pipeline.answer.side_effect = answer
        app.state.pipeline = pipeline
        with client.stream('POST','/chat/stream',json={'session_id':'stream-one','message':'项目？'}) as response:
            events = [json.loads(line) for line in response.iter_lines() if line]
        assert response.status_code == 200
        assert [e['type'] for e in events] == ['start','delta','done']
        assert events[1]['text'] == events[-1]['answer']
        assert events[-1]['answer_id']
        assert app.state.conversations.get(scoped_conversation(EMAIL, 'stream-one'))[-1].content == '我在项目中用了 LoRA。'
        assert client.post('/feedback',json={'answer_id':events[-1]['answer_id'],'session_id':'stream-one','rating':'up'}).status_code == 200
        assert client.post('/chat',json={'session_id':'other','message':'项目？'}).status_code == 200


def test_stream_error_never_publishes_draft_or_writes_history(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with client_for(app) as client:
        login(client, settings)
        pipeline = Mock()
        def broken(history, question, on_text=None):
            on_text('已收到的文字')
            raise LLMUnavailable('LLM API 请求超时，请稍后重试。')
        pipeline.answer.side_effect = broken
        app.state.pipeline = pipeline
        with client.stream('POST','/chat/stream',json={'session_id':'stream-error','message':'项目？'}) as response:
            events = [json.loads(line) for line in response.iter_lines() if line]
        assert [e['type'] for e in events] == ['start','error']
        assert '已收到的文字' not in json.dumps(events, ensure_ascii=False)
        assert '超时' in events[-1]['message']
        assert not app.state.conversations.get(scoped_conversation(EMAIL, 'stream-error'))


def test_configured_models_prewarm_once_and_new_conversation_reuses_pipeline(tmp_path):
    settings = make_settings(tmp_path)
    settings.llm_api_key = 'test'
    settings.llm_model = 'test'
    pipeline = Mock()
    pipeline.answer.return_value = ChatResponse(answer='有证据的回答', refused=False)
    with patch('backend.app.main.RAGPipeline', return_value=pipeline) as constructor:
        app = create_app(settings)
        with client_for(app) as client:
            login(client, settings)
            for session_id in ('first', 'first', 'new-conversation'):
                with client.stream('POST', '/chat/stream', json={
                    'session_id': session_id, 'message': '介绍项目',
                }) as response:
                    events = [json.loads(line) for line in response.iter_lines() if line]
                assert events[-1]['type'] == 'done'
            constructor.assert_called_once_with(settings, app.state.llm)
            pipeline.retriever.dense._load_model.assert_called_once_with()
            pipeline.reranker._load_model.assert_called_once_with()
            assert pipeline.answer.call_count == 3

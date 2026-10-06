import asyncio
import json
import time
from threading import BoundedSemaphore, Event, Lock
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from fastapi import HTTPException

from backend.app.api.chat import chat, chat_stream
from backend.app.errors import LLMUnavailable, RequestCancelled
from backend.app.main import create_app
from backend.app.models.schemas import ChatRequest
from backend.app.request_control import bind_cancellation
from backend.app.request_control import check_cancelled
from backend.tests.auth_helpers import client_for, make_settings
from backend.tests.test_refusal import make_pipeline
from scripts.evaluate_api import authenticate, check_health


def test_pipeline_callback_waits_for_verification_and_suppresses_failed_draft():
    pipeline, llm = make_pipeline()
    visible = []
    answer = llm.grounded_answer.return_value

    def generate(query, evidence, on_text=None):
        on_text('未经核验的草稿')
        assert visible == []
        return answer

    def verify(query, text, evidence):
        assert visible == []
        return llm.judge_answerability.return_value

    llm.grounded_answer.side_effect = generate
    llm.verify_answer.side_effect = verify
    assert not pipeline.answer([], '项目？', on_text=visible.append).refused
    assert visible == [answer.answer]
    visible.clear()
    llm.verify_answer.side_effect = LLMUnavailable('核验超时。')
    with pytest.raises(LLMUnavailable):
        pipeline.answer([], '项目？', on_text=visible.append)
    assert visible == []


def test_cancellation_after_retrieval_prevents_further_model_calls():
    pipeline, llm = make_pipeline()
    cancelled = Event()
    hits = pipeline.retriever.search.return_value
    def retrieve(query):
        cancelled.set()
        return hits
    pipeline.retriever.search.side_effect = retrieve
    with bind_cancellation(cancelled), pytest.raises(RequestCancelled):
        pipeline.answer([], '项目？')
    llm.judge_answerability.assert_not_called()
    llm.grounded_answer.assert_not_called()


def test_proxy_visitors_have_independent_login_request_limits(tmp_path):
    settings = make_settings(tmp_path)
    settings.auth_ip_max_requests = 1
    with client_for(create_app(settings)) as client:
        for email, ip in [('first@example.com', '203.0.113.10'), ('second@example.com', '203.0.113.11')]:
            assert client.post('/auth/request', json={'email': email},
                               headers={'X-Forwarded-For': ip}).status_code == 200
        assert len(list(settings.auth_outbox_dir.glob('*.txt'))) == 2
        client.post('/auth/request', json={'email': 'third@example.com'},
                    headers={'X-Forwarded-For': '203.0.113.10'})
        assert len(list(settings.auth_outbox_dir.glob('*.txt'))) == 2


def test_evaluation_local_login_and_current_health_contract(tmp_path):
    settings = make_settings(tmp_path)
    requests = []
    def respond(request):
        requests.append(request.url.path)
        if request.url.path == '/auth/request':
            assert json.loads(request.content)['email'] == 'reviewer@example.com'
            settings.auth_outbox_dir.mkdir()
            (settings.auth_outbox_dir / 'link.txt').write_text(
                'To: reviewer@example.com\nhttp://127.0.0.1:8000/login#token=' + 'x' * 32,
                encoding='utf-8')
            return httpx.Response(200, json={'message': 'ok'})
        if request.url.path == '/auth/verify':
            assert json.loads(request.content)['token'] == 'x' * 32
            return httpx.Response(200, json={'authenticated': True})
        return httpx.Response(200, json={'status': 'ok'})
    with patch.dict('os.environ', {}, clear=True), patch('scripts.evaluate_api.get_settings', return_value=settings):
        with httpx.Client(base_url=settings.auth_public_url, transport=httpx.MockTransport(respond)) as client:
            authenticate(client)
            check_health(client)
    assert requests == ['/auth/request', '/auth/verify', '/health']


def test_evaluation_unavailable_health_stops_before_chat():
    with httpx.Client(base_url='http://127.0.0.1:8000', transport=httpx.MockTransport(
        lambda request: httpx.Response(503, json={'status': 'unavailable'}))) as client:
        with pytest.raises(SystemExit, match='not ready'):
            check_health(client)


def fake_stream_request():
    state = SimpleNamespace(chat_slots=BoundedSemaphore(1), chat_lock=Lock(),
                            inspector=None, pipeline=Mock(), feedback=Mock(), conversations=Mock())
    return SimpleNamespace(app=SimpleNamespace(state=state),
                           state=SimpleNamespace(perf_trace=None, auth_email='reviewer@example.com'),
                           is_disconnected=Mock(side_effect=None))


def test_full_admission_rejects_sync_and_stream_without_starting_inference():
    request = fake_stream_request()
    state = request.app.state
    state.chat_slots.acquire()
    body = ChatRequest(session_id='one', message='项目？')
    with pytest.raises(HTTPException) as sync_error:
        chat(body, request)
    assert sync_error.value.status_code == 429
    with pytest.raises(HTTPException) as stream_error:
        asyncio.run(chat_stream(body, request))
    assert stream_error.value.status_code == 429
    state.pipeline.answer.assert_not_called()
    state.chat_slots.release()


def test_disconnected_queued_stream_releases_slot_without_inference():
    request = fake_stream_request()
    state = request.app.state
    state.chat_lock.acquire()
    async def disconnected():
        return True
    request.is_disconnected = disconnected
    async def consume():
        response = await chat_stream(ChatRequest(session_id='one', message='项目？'), request)
        iterator = response.body_iterator
        assert json.loads(await iterator.__anext__())['type'] == 'start'
        with pytest.raises(StopAsyncIteration):
            await iterator.__anext__()
    try:
        asyncio.run(consume())
        assert state.chat_slots.acquire(timeout=2)
        state.chat_slots.release()
        state.pipeline.answer.assert_not_called()
        state.feedback.save_answer.assert_not_called()
    finally:
        state.chat_lock.release()


def test_disconnect_during_inference_does_not_persist_answer():
    request = fake_stream_request()
    state = request.app.state
    started, resume = Event(), Event()
    state.conversations.get.return_value = []
    def answer(history, question, on_text=None):
        started.set()
        assert resume.wait(timeout=2)
        check_cancelled()
        raise AssertionError('cancelled inference continued')
    state.pipeline.answer.side_effect = answer
    async def disconnected():
        return True
    request.is_disconnected = disconnected
    async def consume():
        response = await chat_stream(ChatRequest(session_id='one', message='项目？'), request)
        iterator = response.body_iterator
        await iterator.__anext__()
        assert started.wait(timeout=2)
        with pytest.raises(StopAsyncIteration):
            await iterator.__anext__()
    try:
        asyncio.run(consume())
    finally:
        resume.set()
    assert state.chat_slots.acquire(timeout=2)
    state.chat_slots.release()
    state.feedback.save_answer.assert_not_called()
    state.conversations.append_turn.assert_not_called()

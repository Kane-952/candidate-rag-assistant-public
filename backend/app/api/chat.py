import asyncio
import json
import logging
import time
from queue import Empty, Full, Queue
from threading import Event, Thread

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import scoped_conversation
from ..errors import LLMUnavailable, RequestCancelled, ServiceError
from ..inspector import UNCERTAIN
from ..models.schemas import ChatRequest, ChatResponse
from ..perf import bind_trace
from ..request_control import bind_cancellation, check_cancelled, inference_lock

router = APIRouter()
logger = logging.getLogger(__name__)


def answer_status(result):
    if result.refused:
        return 'REFUSE'
    return 'PARTIAL' if UNCERTAIN.search(result.answer) else 'ANSWER'


def save_inspector(state, trace, email, conversation_id, question, result, error):
    if not state.inspector or not trace:
        return
    try:
        state.inspector.save(trace, email, conversation_id, question, result=result, error=error)
    except Exception as exc:
        logger.warning('inspector_save_failed=%s', type(exc).__name__)


def prepare_request(body, request):
    state, trace = request.app.state, request.state.perf_trace
    email = request.state.auth_email
    if state.inspector and trace:
        trace.inspector = {}
    conversation_id = scoped_conversation(email, body.session_id)
    if trace:
        trace.details.update(user_email=email, conversation_id=conversation_id)
    if not state.chat_slots.acquire(blocking=False):
        raise HTTPException(429, '服务正在处理其他问题，请稍后重试。', headers={'Retry-After': '2'})
    return state, trace, email, conversation_id


def run_answer(state, trace, email, conversation_id, question, cancelled, streaming=False):
    result, error, status = None, None, 'error'
    with bind_trace(trace), bind_cancellation(cancelled):
        try:
            started = time.perf_counter()
            with inference_lock(state.chat_lock):
                if trace:
                    trace.add('lock_wait', time.perf_counter() - started)
                    trace.details['pipeline_reused'] = state.pipeline is not None
                if state.pipeline is None:
                    raise LLMUnavailable('RAG 服务尚未就绪，请稍后重试。')
                history = state.conversations.get(conversation_id)
                if trace:
                    trace.details['history_messages'] = len(history)
                    if trace.inspector is not None:
                        trace.inspector['history'] = [message.model_dump() for message in history]
                # Generation callbacks never reach visitors. Publish only the
                # final citation-checked and fact-verified result.
                result = (state.pipeline.answer(history, question, on_text=lambda _: check_cancelled())
                          if streaming else state.pipeline.answer(history, question))
                check_cancelled()
                result = result.model_copy(update={
                    'answer_id': state.feedback.save_answer(conversation_id, question, result, history,
                                                           trace.request_id if trace else None, email)
                })
                state.conversations.append_turn(conversation_id, question, result.answer)
                if trace:
                    trace.details['answer_status'] = answer_status(result)
                status = 'done'
                return result
        except RequestCancelled:
            status, error, result = 'cancelled', '请求已取消。', None
            if trace:
                trace.details['answer_status'] = 'ERROR'
            raise
        except ServiceError as exc:
            error, result = str(exc), None
            if trace:
                trace.details.update(error_type=type(exc).__name__, answer_status='ERROR')
            raise
        except Exception as exc:
            error, result = '请求处理失败。', None
            if trace:
                trace.details.update(error_type=type(exc).__name__, answer_status='ERROR')
            raise
        finally:
            if trace:
                trace.finish(status)
            save_inspector(state, trace, email, conversation_id, question, result, error)


@router.post('/chat/stream')
async def chat_stream(body: ChatRequest, request: Request):
    state, trace, email, conversation_id = prepare_request(body, request)
    cancelled = Event()
    events = Queue(maxsize=2)

    def emit(kind, **data):
        while not cancelled.is_set():
            try:
                events.put({'type': kind, **data}, timeout=0.1)
                return
            except Full:
                continue
        return False

    def produce():
        try:
            result = run_answer(state, trace, email, conversation_id, body.message, cancelled, streaming=True)
            if not result.refused:
                emit('delta', text=result.answer)
            emit('done', **result.model_dump())
        except RequestCancelled:
            pass
        except ServiceError as exc:
            logger.warning('chat_stream_error=%s', type(exc).__name__)
            if not cancelled.is_set():
                emit('error', message=str(exc))
        except Exception:
            logger.error('chat_stream_error=UnexpectedError')
            if not cancelled.is_set():
                emit('error', message='这次没能完成回复，请稍后重试。')
        finally:
            state.chat_slots.release()

    async def response_body():
        started = False
        try:
            Thread(target=produce, daemon=True).start()
            started = True
            yield json.dumps({'type': 'start'}) + '\n'
            while not cancelled.is_set():
                if await request.is_disconnected():
                    break
                try:
                    event = events.get_nowait()
                except Empty:
                    await asyncio.sleep(0.02)
                    continue
                yield json.dumps(event, ensure_ascii=False) + '\n'
                if event['type'] in ('done', 'error'):
                    break
        finally:
            cancelled.set()
            if not started:
                state.chat_slots.release()

    return StreamingResponse(response_body(), media_type='application/x-ndjson',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@router.post('/chat', response_model=ChatResponse)
def chat(body: ChatRequest, request: Request):
    state, trace, email, conversation_id = prepare_request(body, request)
    try:
        return run_answer(state, trace, email, conversation_id, body.message, Event())
    except ServiceError as exc:
        logger.warning('chat_error=%s', type(exc).__name__)
        raise HTTPException(503, detail=str(exc)) from None
    except Exception:
        logger.error('chat_error=UnexpectedError')
        raise HTTPException(500, detail='服务暂时无法处理请求，请检查本地运行环境。') from None
    finally:
        state.chat_slots.release()

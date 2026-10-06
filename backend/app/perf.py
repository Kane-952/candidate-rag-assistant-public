"""Per-request timing and verified identity, without prompts or tokens in logs."""
import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4


logger = logging.getLogger(__name__)
_trace = ContextVar('chat_perf_trace', default=None)
_phase = ContextVar('chat_perf_llm_phase', default=None)


class RequestTrace:
    def __init__(self, route):
        self.request_id = uuid4().hex[:12]
        self.route = route
        self.started = time.perf_counter()
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.durations = {}
        self.events = {}
        self.details = {}
        self.inspector = None  # Only enabled when an inspector admin is configured.
        self.summary = None

    def add(self, name, seconds):
        self.durations[name] = self.durations.get(name, 0.0) + seconds * 1000

    def mark(self, name):
        self.events[name] = (time.perf_counter() - self.started) * 1000

    @contextmanager
    def stage(self, name):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, time.perf_counter() - started)

    def finish(self, status):
        self.mark('complete_ms')
        payload = {
            'request_id': self.request_id, 'route': self.route, 'status': status,
            'user_email': self.details.get('user_email'),
            'conversation_id': self.details.get('conversation_id'),
            'answer_status': self.details.get('answer_status'),
            'error_type': self.details.get('error_type'),
            'durations_ms': {k: round(v, 2) for k, v in self.durations.items()},
            'events_ms': {k: round(v, 2) for k, v in self.events.items()},
            'details': {k: self.details[k] for k in ('pipeline_reused', 'history_messages')
                        if k in self.details},
            'total_ms': round((time.perf_counter() - self.started) * 1000, 2),
        }
        self.summary = payload
        logger.info('perf_trace %s', json.dumps(payload, ensure_ascii=False))


def current_trace():
    return _trace.get()


def current_phase():
    return _phase.get()


def observe(name, value):
    """Capture bounded RAG diagnostics without writing content to ordinary logs."""
    trace = current_trace()
    if trace is not None and trace.inspector is not None:
        trace.inspector[name] = value() if callable(value) else value


@contextmanager
def bind_trace(trace):
    token = _trace.set(trace)
    try:
        yield
    finally:
        _trace.reset(token)


@contextmanager
def llm_phase(name):
    token = _phase.set(name)
    try:
        yield
    finally:
        _phase.reset(token)


@contextmanager
def timed(name):
    trace = current_trace()
    if trace is None:
        yield
    else:
        with trace.stage(name):
            yield


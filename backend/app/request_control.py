"""Bounded admission and cooperative cancellation for synchronous inference."""
from contextlib import contextmanager
from contextvars import ContextVar

from .errors import RequestCancelled

_cancelled = ContextVar('request_cancelled', default=None)


@contextmanager
def bind_cancellation(event):
    token = _cancelled.set(event)
    try:
        yield
    finally:
        _cancelled.reset(token)


def check_cancelled():
    event = _cancelled.get()
    if event is not None and event.is_set():
        raise RequestCancelled('请求已取消。')


@contextmanager
def inference_lock(lock):
    while not lock.acquire(timeout=0.1):
        check_cancelled()
    try:
        check_cancelled()
        yield
    finally:
        lock.release()

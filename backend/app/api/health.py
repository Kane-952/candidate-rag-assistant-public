"""Minimal public readiness endpoint for the hosting platform."""
import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


router = APIRouter()
INDEX_FILES = ('chunks.jsonl', 'vectors.faiss', 'manifest.json')


def database_ready(path, table):
    if not path.is_file():
        return False
    try:
        # mode=rw avoids silently creating a missing database. A short write
        # transaction also checks the access needed by login and feedback.
        with sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=1) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute(f'SELECT 1 FROM {table} LIMIT 1')
            db.rollback()
        return True
    except (OSError, sqlite3.Error):
        return False


@router.get('/health', include_in_schema=False)
def health(request: Request):
    state = request.app.state
    settings = state.settings
    pipeline = state.pipeline
    retriever = getattr(pipeline, 'retriever', None)
    dense = getattr(retriever, 'dense', None)
    reranker = getattr(pipeline, 'reranker', None)
    rag_ready = (
        state.rag_init_status == 'ready'
        and pipeline is not None
        and bool(getattr(pipeline, 'chunks', None))
        and retriever is not None
        and dense is not None
        and getattr(dense, 'index', None) is not None
        and getattr(dense, 'model', None) is not None
        and (not settings.enable_reranker or
             (reranker is not None and getattr(reranker, 'model', None) is not None))
    )
    ready = (
        state.storage_ready
        and database_ready(state.auth.path, 'sessions')
        and database_ready(state.feedback.path, 'answers')
        and all((settings.index_dir / name).is_file() for name in INDEX_FILES)
        and rag_ready
    )
    return JSONResponse({'status': 'ok' if ready else 'unavailable'},
                        status_code=200 if ready else 503)

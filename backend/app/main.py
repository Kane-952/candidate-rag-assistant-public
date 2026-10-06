import logging
from contextlib import asynccontextmanager
from threading import Lock, BoundedSemaphore

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .feedback_loop import FeedbackLoopStore
from .api.feedback import router as feedback_router
from .api.feedback_admin import router as feedback_admin_router
from .api.chat import router as chat_router
from .api.health import router as health_router
from .api.inspector import router as inspector_router
from .config import ROOT, get_settings
from .conversation import ConversationStore
from .llm.client import LLMClient
from .auth import AuthStore, router as auth_router, authentication_middleware
from .rag.pipeline import RAGPipeline
from .perf import RequestTrace, bind_trace, timed
from .inspector import InspectorStore
from .errors import LLMUnavailable, ServiceError


def create_app(settings=None):
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app):
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
        logging.getLogger('httpx').setLevel(logging.WARNING)
        # Access logs can contain URL query parameters; chat timings are logged separately.
        if settings.app_env == 'production':
            logging.getLogger('uvicorn.access').disabled = True
        app.state.settings = settings
        app.state.llm = LLMClient(settings)
        app.state.pipeline = None
        app.state.rag_init_status = 'initializing'
        app.state.rag_init_error = None
        app.state.conversations = ConversationStore(settings)
        app.state.chat_lock = Lock()
        app.state.chat_slots = BoundedSemaphore(settings.max_pending_chats)
        app.state.feedback = None
        app.state.inspector = None
        app.state.auth = None
        for resource, factory, path in (
            ('feedback', FeedbackLoopStore, settings.feedback_db),
            ('inspector', InspectorStore, settings.feedback_db),
            ('auth', AuthStore, settings.auth_db),
        ):
            try:
                setattr(app.state, resource, factory(path))
            except Exception as exc:
                logging.getLogger(__name__).error(
                    'storage_init_failed resource=%s error_type=%s', resource, type(exc).__name__)
        app.state.storage_ready = all((app.state.auth, app.state.feedback, app.state.inspector))
        startup_trace = RequestTrace('startup')
        status = 'failed'
        stage = 'configuration'
        with bind_trace(startup_trace):
            try:
                if not settings.llm_api_key or not settings.llm_model:
                    raise LLMUnavailable('LLM configuration is incomplete')
                stage = 'knowledge_base_and_index'
                with timed('pipeline_init'):
                    pipeline = RAGPipeline(settings, app.state.llm)
                stage = 'embedding_model'
                pipeline.retriever.dense._load_model()
                if pipeline.reranker:
                    stage = 'reranker_model'
                    pipeline.reranker._load_model()
                app.state.pipeline = pipeline
                app.state.rag_init_status = 'ready'
                status = 'ready'
            except Exception as exc:
                app.state.rag_init_status = 'failed'
                app.state.rag_init_error = type(exc).__name__
                logging.getLogger(__name__).error(
                    'rag_prewarm_failed stage=%s error_type=%s detail=%s',
                    stage, type(exc).__name__, str(exc) if isinstance(exc, ServiceError) else 'internal error')
            finally:
                startup_trace.finish(status)
        yield
        app.state.llm.close()

    app = FastAPI(title='Candidate RAG Assistant', debug=settings.debug, lifespan=lifespan)
    app.middleware('http')(authentication_middleware)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins,
        allow_credentials=False, allow_methods=['GET', 'POST'], allow_headers=['Content-Type'],
    )
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(feedback_router)
    app.include_router(inspector_router)
    app.include_router(feedback_admin_router)
    app.mount('/', StaticFiles(directory=ROOT / 'frontend', html=True), name='frontend')
    return app


app = create_app()


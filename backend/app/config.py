from pathlib import Path
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', env_file_encoding='utf-8', extra='ignore')
    app_env: Literal['local', 'production'] = 'local'
    debug: bool = False
    feedback_db: Path = ROOT / 'data' / 'feedback' / 'feedback.sqlite3'
    index_dir: Path = ROOT / 'data' / 'indexes'
    chunk_size: int = Field(450, ge=32)
    chunk_overlap: int = Field(80, ge=0)
    bm25_top_k: int = Field(12, ge=1)
    dense_top_k: int = Field(12, ge=1)
    hybrid_top_k: int = Field(12, ge=1)
    rerank_top_k: int = Field(4, ge=1)
    rrf_k: int = Field(60, ge=1)
    bm25_k1: float = Field(1.5, gt=0)
    bm25_b: float = Field(0.75, ge=0, le=1)
    enable_reranker: bool = True
    embedding_model: str = 'BAAI/bge-small-zh-v1.5'
    embedding_query_prefix: str = '为这个句子生成表示以用于检索相关文章：'
    reranker_model: str = 'cross-encoder/mmarco-mMiniLMv2-L12-H384-v1'
    model_device: str = 'cpu'
    model_batch_size: int = Field(8, ge=1)
    model_max_length: int = Field(512, ge=32)
    min_dense_score: float = Field(0.45, ge=-1, le=1)
    min_rerank_score: float = Field(0.20, ge=0, le=1)
    min_answerability_confidence: float = Field(0.80, ge=0, le=1)
    llm_api_key: str = ''
    llm_base_url: str = 'https://api.openai.com/v1'
    llm_model: str = ''
    llm_thinking_mode: Literal['default', 'enabled', 'disabled'] = 'default'
    llm_timeout_seconds: float = Field(45, gt=0)
    llm_max_tokens: int = Field(1200, ge=128)
    history_messages: int = Field(12, ge=2)
    max_sessions: int = Field(100, ge=1)
    max_pending_chats: int = Field(4, ge=1, le=32)
    session_ttl_seconds: int = Field(3600, ge=60)
    cors_origins: list[str] = ['http://127.0.0.1:8000', 'http://localhost:8000']
    auth_db: Path = ROOT / 'data' / 'auth' / 'auth.sqlite3'
    auth_outbox_dir: Path = ROOT / '.cache' / 'auth-outbox'
    inspector_admin_emails: list[str] = []
    auth_public_url: str = 'http://127.0.0.1:8000'
    auth_delivery: Literal['local', 'smtp'] = 'local'
    auth_session_ttl_seconds: int = Field(86400, ge=300)
    auth_link_ttl_seconds: int = Field(600, ge=60, le=3600)
    auth_email_cooldown_seconds: int = Field(60, ge=1, le=3600)
    auth_ip_window_seconds: int = Field(900, ge=60)
    auth_ip_max_requests: int = Field(20, ge=1)
    smtp_host: str = ''
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_username: str = ''
    smtp_password: str = ''
    smtp_from: str = ''
    smtp_security: Literal['starttls', 'ssl'] = 'starttls'

    @model_validator(mode='after')
    def validate_overlap(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError('CHUNK_OVERLAP must be smaller than CHUNK_SIZE')
        url = urlparse(self.auth_public_url)
        local = url.hostname in ('127.0.0.1', 'localhost', '::1')
        if not url.scheme or not url.hostname or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
            raise ValueError('AUTH_PUBLIC_URL must be an origin without path or query')
        if url.scheme != 'https' and not (url.scheme == 'http' and local):
            raise ValueError('AUTH_PUBLIC_URL must use HTTPS outside localhost')
        if self.auth_delivery == 'local' and not local:
            raise ValueError('AUTH_DELIVERY=local is only allowed on localhost')
        if self.auth_delivery == 'smtp' and not all((self.smtp_host, self.smtp_username, self.smtp_password, self.smtp_from)):
            raise ValueError('SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD and SMTP_FROM are required')
        self.inspector_admin_emails = sorted({email.strip().lower() for email in self.inspector_admin_emails if email.strip()})
        if self.app_env == 'production':
            if local or url.scheme != 'https':
                raise ValueError('production AUTH_PUBLIC_URL must be a non-local HTTPS origin')
            if self.auth_delivery != 'smtp':
                raise ValueError('production requires AUTH_DELIVERY=smtp')
            if self.debug:
                raise ValueError('production must disable DEBUG')
            if not self.llm_api_key or not self.llm_model:
                raise ValueError('production requires LLM_API_KEY and LLM_MODEL')
        return self

    @property
    def kb_dir(self):
        return ROOT / 'knowledge_base'

    @property
    def secure_cookie(self):
        return self.app_env == 'production' or self.auth_public_url.startswith('https://')


@lru_cache
def get_settings():
    return Settings()


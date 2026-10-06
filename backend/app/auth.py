"""Small, server-validated email authentication layer."""
import hashlib
import ipaddress
import logging
import secrets
import smtplib
import sqlite3
import ssl
import time
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from .config import ROOT
from .perf import RequestTrace

logger = logging.getLogger(__name__)
router = APIRouter()
COOKIE = 'candidate_session'
PUBLIC_PATHS = {'/login', '/login.css', '/login.js', '/auth/request', '/auth/verify', '/health'}
ADMIN_PATHS = {'/inspector', '/inspector.html', '/inspector.css', '/inspector.js',
               '/docs', '/redoc', '/openapi.json'}
LOGIN_REQUEST_MESSAGE = '如果该邮箱可以接收邮件，登录链接会发送到该邮箱；短时间内重复申请不会再次发送。'


def digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def normalize_email(value: str) -> str:
    return value.strip().lower()


def scoped_conversation(email: str, session_id: str) -> str:
    return f'{email}:{session_id}'


def login_ip(request: Request) -> str:
    """Use proxy headers only for a loopback peer; prefer the nearest forwarded client."""
    peer = request.client.host if request.client else ''
    if peer not in ('127.0.0.1', '::1'):
        return peer
    forwarded = request.headers.get('x-forwarded-for', '')
    candidate = forwarded.rsplit(',', 1)[-1].strip() if forwarded else ''
    for value in (candidate, request.headers.get('x-real-ip', '').strip()):
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            pass
    return peer


@contextmanager
def connection(path: Path):
    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class AuthStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with connection(path) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS login_links (
                    token_hash TEXT PRIMARY KEY, email TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL, created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS access_events (
                    id INTEGER PRIMARY KEY, email TEXT NOT NULL,
                    session_id TEXT NOT NULL, event TEXT NOT NULL,
                    occurred_at INTEGER NOT NULL,
                    ip_address TEXT
                );
                CREATE TABLE IF NOT EXISTS login_requests (
                    id INTEGER PRIMARY KEY, email_hash TEXT NOT NULL,
                    ip_hash TEXT NOT NULL, requested_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS login_requests_time ON login_requests(requested_at);
                CREATE TABLE IF NOT EXISTS email_cooldowns (
                    email_hash TEXT PRIMARY KEY, last_issued_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS email_cooldowns_time ON email_cooldowns(last_issued_at);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(access_events)')}
            if 'ip_address' not in columns:
                db.execute('ALTER TABLE access_events ADD COLUMN ip_address TEXT')

    def issue_link(self, email: str, ip: str, ttl: int, cooldown: int,
                   ip_window: int, ip_limit: int) -> str | None:
        now = int(time.time())
        email_hash, ip_hash = digest(email), digest(ip)
        with connection(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM login_requests WHERE requested_at < ?', (now - ip_window,))
            db.execute('DELETE FROM email_cooldowns WHERE last_issued_at < ?', (now - cooldown,))
            ip_count = db.execute(
                'SELECT COUNT(*) FROM login_requests WHERE ip_hash=?', (ip_hash,)
            ).fetchone()[0]
            # Count every valid request, including requests blocked by cooldown.
            db.execute('INSERT INTO login_requests(email_hash,ip_hash,requested_at) VALUES(?,?,?)',
                       (email_hash, ip_hash, now))
            if ip_count >= ip_limit:
                return None
            previous = db.execute(
                'SELECT last_issued_at FROM email_cooldowns WHERE email_hash=?', (email_hash,)
            ).fetchone()
            if previous and now - previous[0] < cooldown:
                return None
            token = secrets.token_urlsafe(32)
            db.execute(
                'INSERT INTO email_cooldowns(email_hash,last_issued_at) VALUES(?,?) '
                'ON CONFLICT(email_hash) DO UPDATE SET last_issued_at=excluded.last_issued_at',
                (email_hash, now))
            db.execute('DELETE FROM login_links WHERE email=? OR expires_at<=?', (email, now))
            db.execute('INSERT INTO login_links VALUES(?,?,?)', (digest(token), email, now + ttl))
        return token

    def consume_link(self, token: str, ttl: int, ip_address: str) -> tuple[str, str, str] | None:
        if len(token) > 200 or len(token) < 20:
            return None
        now = int(time.time())
        with connection(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT email,expires_at FROM login_links WHERE token_hash=?', (digest(token),)).fetchone()
            if not row:
                return None
            db.execute('DELETE FROM login_links WHERE token_hash=?', (digest(token),))
            email, expires_at = row
            if expires_at <= now:
                return None
            session_token = secrets.token_urlsafe(32)
            session_id = str(uuid4())
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)',
                       (digest(session_token), session_id, email, now, now + ttl))
            db.execute('INSERT INTO access_events(email,session_id,event,occurred_at,ip_address) VALUES(?,?,?,?,?)',
                       (email, session_id, 'login', now, ip_address))
        return session_token, session_id, email

    def get_session(self, token: str | None) -> tuple[str, str] | None:
        if not token or len(token) > 200:
            return None
        now = int(time.time())
        with connection(self.path) as db:
            row = db.execute('SELECT email,session_id,expires_at FROM sessions WHERE token_hash=?',
                             (digest(token),)).fetchone()
            if not row:
                return None
            email, session_id, expires_at = row
            if expires_at <= now:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))
                return None
            return email, session_id

    def logout(self, token: str | None):
        if not token or len(token) > 200:
            return
        now = int(time.time())
        with connection(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT email,session_id FROM sessions WHERE token_hash=?',
                             (digest(token),)).fetchone()
            if row:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (digest(token),))
                db.execute('INSERT INTO access_events(email,session_id,event,occurred_at) VALUES(?,?,?,?)',
                           (row[0], row[1], 'logout', now))

    def recent_logins(self, email: str | None = None, limit: int = 50) -> list[dict]:
        limit = max(1, min(limit, 200))
        with connection(self.path) as db:
            query = ('SELECT email,ip_address,occurred_at,session_id FROM access_events '
                     "WHERE event='login'")
            args = []
            if email:
                query += ' AND email=?'
                args.append(email)
            rows = db.execute(query + ' ORDER BY id DESC LIMIT ?', (*args, limit)).fetchall()
        return [dict(user_email=row[0], ip_address=row[1], login_time=row[2],
                     session_id=row[3]) for row in rows]


def deliver_link(settings, email: str, token: str):
    # URL fragments never reach the web server or access logs.
    link = settings.auth_public_url.rstrip('/') + '/login#token=' + quote(token, safe='')
    if settings.auth_delivery == 'local':
        settings.auth_outbox_dir.mkdir(parents=True, exist_ok=True)
        path = settings.auth_outbox_dir / (str(uuid4()) + '.txt')
        path.write_text(f'To: {email}\nLogin link (expires in {settings.auth_link_ttl_seconds // 60} minutes):\n{link}\n', encoding='utf-8')
        path.chmod(0o600)
        return
    message = EmailMessage()
    message['Subject'] = 'Demo AI Assistant 登录链接'
    message['From'] = settings.smtp_from
    message['To'] = email
    message.set_content(
        f'Demo AI Assistant\n\n'
        f'你正在访问 Demo 的 AI 求职助手。\n\n'
        f'点击下面的链接完成登录：\n'
        f'登录 Demo AI Assistant：\n{link}\n\n'
        f'该链接在 {settings.auth_link_ttl_seconds // 60} 分钟内有效，并且只能使用一次。'
    )
    context = ssl.create_default_context()
    if settings.smtp_security == 'ssl':
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=context) as smtp:
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.starttls(context=context)
            smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)


class EmailRequest(BaseModel):
    email: EmailStr = Field(max_length=254)


class TokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


@router.get('/login', include_in_schema=False)
def login_page(request: Request):
    if request.state.auth_email:
        return RedirectResponse('/', status_code=303)
    return FileResponse(ROOT / 'frontend' / 'login.html')


@router.get('/login.css', include_in_schema=False)
def login_style():
    return FileResponse(ROOT / 'frontend' / 'login.css', media_type='text/css')


@router.get('/login.js', include_in_schema=False)
def login_script():
    return FileResponse(ROOT / 'frontend' / 'login.js', media_type='text/javascript')


@router.post('/auth/request')
def request_link(body: EmailRequest, request: Request):
    email = normalize_email(str(body.email))
    settings = request.app.state.settings
    token = request.app.state.auth.issue_link(
        email, login_ip(request),
        settings.auth_link_ttl_seconds, settings.auth_email_cooldown_seconds,
        settings.auth_ip_window_seconds, settings.auth_ip_max_requests)
    if token:
        try:
            deliver_link(settings, email, token)
        except (OSError, smtplib.SMTPException) as exc:
            logger.error('magic_link_delivery_failed error_type=%s', type(exc).__name__)
    return {'message': LOGIN_REQUEST_MESSAGE}


@router.post('/auth/verify')
def verify_link(body: TokenRequest, request: Request):
    settings = request.app.state.settings
    result = request.app.state.auth.consume_link(body.token, settings.auth_session_ttl_seconds, login_ip(request))
    if not result:
        return JSONResponse({'detail': '登录链接无效或已过期，请重新申请。'}, status_code=400)
    token, _, _ = result
    response = JSONResponse({'authenticated': True})
    # The link lands on /login before the same-origin verification POST, so
    # Strict does not block this flow. Production always uses a Secure cookie.
    response.set_cookie(COOKIE, token, max_age=settings.auth_session_ttl_seconds,
                        httponly=True, secure=settings.secure_cookie,
                        samesite='strict', path='/')
    return response


@router.post('/auth/logout')
def logout(request: Request):
    request.app.state.auth.logout(request.cookies.get(COOKIE))
    response = JSONResponse({'logged_out': True})
    response.delete_cookie(COOKIE, path='/')
    return response


async def authentication_middleware(request: Request, call_next):
    path = request.url.path
    admin_path = path in ADMIN_PATHS or path.startswith(('/inspector/', '/docs/'))
    trace = RequestTrace(path) if path in ('/chat', '/chat/stream') else None
    request.state.perf_trace = trace
    auth_started = time.perf_counter()
    settings = request.app.state.settings
    if request.app.state.auth is None and request.url.path != '/health':
        return JSONResponse({'detail': '服务暂时不可用。'}, status_code=503)
    if settings.auth_delivery == 'local' and (not request.client or request.client.host not in ('127.0.0.1', '::1', 'localhost')):
        return JSONResponse({'detail': '本地验证模式仅允许本机访问。'}, status_code=403)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if (origin and origin != settings.auth_public_url.rstrip('/')) or request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': '跨站请求已拒绝。'}, status_code=403)
    # Readiness must work even when the auth database is down, without making
    # health probes depend on a visitor's Session cookie.
    session = (None if request.url.path == '/health' else
               request.app.state.auth.get_session(request.cookies.get(COOKIE)))
    if trace:
        trace.add('auth_session', time.perf_counter() - auth_started)
    request.state.auth_email = session[0] if session else None
    request.state.auth_session_id = session[1] if session else None
    if path not in PUBLIC_PATHS and not session:
        if admin_path:
            return JSONResponse({'detail': '请先登录。'}, status_code=401)
        if request.method in ('GET', 'HEAD') and 'text/html' in request.headers.get('accept', ''):
            return RedirectResponse('/login', status_code=303)
        return JSONResponse({'detail': '请先登录。'}, status_code=401)
    if admin_path and request.state.auth_email not in settings.inspector_admin_emails:
        return JSONResponse({'detail': '仅管理员可访问。'}, status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

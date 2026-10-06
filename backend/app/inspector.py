"""Admin-only, bounded request diagnostics. Never persist credentials or prompts."""
import json
import re
import sqlite3
from contextlib import contextmanager


SENSITIVE_VALUE = re.compile(
    r'(?i)\b(api[_-]?key|password|passwd|candidate_session|session[_-]?token|'
    r'access[_-]?token|refresh[_-]?token|token)\b(\s*[:=]\s*["\']?)[^\s&"\',;]+')
BEARER = re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]+')
API_KEY = re.compile(r'\bsk-[A-Za-z0-9_-]{12,}\b')
CHINESE_SECRET = re.compile(r'(密码|验证码|一次性验证码)(\s*(?:是|为|[:：=])\s*)[^\s，。；;,]{4,}')
UNCERTAIN = re.compile(r'无法确认|不能确认|无法核实|资料不足|没有足够.{0,8}(信息|证据)')


def redact(value):
    if isinstance(value, str):
        value = SENSITIVE_VALUE.sub(lambda m: m.group(1) + m.group(2) + '[已隐藏]', value)
        value = BEARER.sub('Bearer [已隐藏]', value)
        return CHINESE_SECRET.sub(lambda m: m.group(1) + m.group(2) + '[已隐藏]', API_KEY.sub('[已隐藏]', value))
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {key: '[已隐藏]' if key.lower() in {
            'api_key', 'password', 'candidate_session', 'session_token', 'access_token',
            'refresh_token', 'token', 'cookie', 'authorization', 'magic_link', 'verification_code', 'otp'
        } else redact(item) for key, item in value.items()}
    return value


class InspectorStore:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS inspector_records (
                    request_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    email TEXT NOT NULL,
                    answer_id TEXT,
                    status TEXT NOT NULL,
                    question TEXT NOT NULL,
                    total_ms REAL,
                    payload TEXT NOT NULL,
                    user_email TEXT,
                    conversation_id TEXT,
                    timestamp TEXT
                );
                CREATE INDEX IF NOT EXISTS inspector_records_recent
                    ON inspector_records(created_at DESC);
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(inspector_records)')}
            for name in ('user_email', 'conversation_id', 'timestamp'):
                if name not in columns:
                    db.execute(f'ALTER TABLE inspector_records ADD COLUMN {name} TEXT')
            # Backfill records created before identity association was added.
            db.execute('UPDATE inspector_records SET user_email=email WHERE user_email IS NULL')
            db.execute('UPDATE inspector_records SET timestamp=created_at WHERE timestamp IS NULL')
            db.execute('''UPDATE inspector_records SET conversation_id=(
                SELECT session_id FROM answers WHERE answers.id=inspector_records.answer_id
            ) WHERE conversation_id IS NULL AND answer_id IS NOT NULL''')
            db.execute('''CREATE INDEX IF NOT EXISTS inspector_records_email_time
                ON inspector_records(user_email, timestamp DESC)''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def save(self, trace, user_email, conversation_id, question, result=None, error=None):
        details = dict(trace.inspector or {})
        answer = result.answer if result is not None else details.get('visible_answer', '')
        decision = details.get('decision') or {}
        if error:
            status = 'PARTIAL' if answer else 'ERROR'
            reason = '生成中断，保留已输出内容。' if answer else '请求未完成。'
            details['error'] = error
        elif result is not None and result.refused:
            status = 'REFUSE'
            reason = decision.get('reason') or '证据不足或最终校验未通过。'
        elif result is not None and UNCERTAIN.search(answer):
            status = 'PARTIAL'
            reason = '最终回答包含未确认部分（根据回答文本标记）。'
        else:
            status = 'ANSWER'
            reason = decision.get('reason') or '已完成回答。'
        details.pop('visible_answer', None)
        details.update({
            'question': question, 'answer': answer,
            'sources': [source.model_dump() for source in result.sources] if result else [],
            'status': status, 'status_reason': reason,
            'timing': trace.summary or {},
        })
        safe = redact(details)
        created_at = trace.timestamp
        with self.connect() as db:
            db.execute('''INSERT INTO inspector_records
                (request_id,created_at,email,answer_id,status,question,total_ms,payload,
                 user_email,conversation_id,timestamp)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (
                trace.request_id, created_at, user_email,
                result.answer_id if result else None, status, safe['question'],
                (trace.summary or {}).get('total_ms'), json.dumps(safe, ensure_ascii=False),
                user_email, conversation_id, created_at,
            ))
            if result is not None and result.answer_id:
                trace_data = {
                    'retrieved_chunks': safe.get('retrieved', []),
                    'reranked_chunks': safe.get('reranked', []),
                    'final_context': safe.get('final_context', []),
                    'timing': safe.get('timing', {}),
                    'sources': safe.get('sources', []),
                }
                db.execute('''UPDATE feedback_cases SET trace_snapshot=?,request_id=?,
                    answer_status=? WHERE answer_id=?''',
                    (json.dumps(trace_data, ensure_ascii=False), trace.request_id,
                     status, result.answer_id))
                for event in db.execute('''SELECT id,payload FROM feedback_events
                    WHERE answer_id=? AND payload IS NOT NULL''', (result.answer_id,)).fetchall():
                    event_data = json.loads(event['payload'])
                    event_data.update(trace_data)
                    event_data['request_id'] = trace.request_id
                    event_data['answer_status'] = status
                    db.execute('UPDATE feedback_events SET payload=? WHERE id=?',
                               (json.dumps(event_data, ensure_ascii=False), event['id']))
            # Keep the newest ordinary traces and all rated/regression traces.
            db.execute('''DELETE FROM inspector_records WHERE request_id NOT IN
                (SELECT request_id FROM inspector_records ORDER BY created_at DESC LIMIT 200)
                AND NOT EXISTS (SELECT 1 FROM feedback_cases f
                                WHERE f.answer_id=inspector_records.answer_id)
                AND NOT EXISTS (SELECT 1 FROM regression_cases g
                                WHERE g.answer_id=inspector_records.answer_id)''')

    def recent(self, email=None):
        limit = 200 if email else 50
        with self.connect() as db:
            rows = db.execute('''SELECT r.request_id,r.user_email,r.conversation_id,r.timestamp,
                r.status,r.question,r.total_ms FROM inspector_records r
                WHERE (? IS NULL OR r.user_email=?)
                ORDER BY r.timestamp DESC LIMIT ?''', (email, email, limit)).fetchall()
        return [dict(row) for row in rows]

    def get(self, request_id):
        with self.connect() as db:
            row = db.execute('''SELECT r.request_id,r.user_email,r.conversation_id,
                r.timestamp,r.answer_id,
                r.payload,f.rating FROM inspector_records r
                LEFT JOIN feedback f ON f.answer_id=r.answer_id
                WHERE r.request_id=?''', (request_id,)).fetchone()
        if row is None:
            return None
        return {
            'request_id': row['request_id'], 'user_email': row['user_email'],
            'conversation_id': row['conversation_id'], 'timestamp': row['timestamp'],
            'answer_id': row['answer_id'],
            'feedback': row['rating'], **json.loads(row['payload']),
        }

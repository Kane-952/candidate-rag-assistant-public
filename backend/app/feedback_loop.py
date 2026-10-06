"""Trace-linked feedback and administrator triage, preserving legacy ratings."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from .feedback import FeedbackStore
from .inspector import UNCERTAIN, redact


FEEDBACK_REASONS = (
    '没有回答我的问题', '回答不够清楚', '回答太长', '回答太简略',
    '没有覆盖我关心的重点', '其他',
)
ATTRIBUTIONS = (
    'Retrieval 问题', 'Generation / Prompt 问题', 'Refusal 问题',
    'Citation 问题', '回答风格问题', '知识库资料不足', '其他', '待人工判断',
)
WORKFLOW_STATUSES = ('待处理', '已分析', '已修复', '已加入回归测试')
ANSWER_STATUSES = ('ANSWER', 'PARTIAL', 'REFUSE')


def now():
    return datetime.now(timezone.utc).isoformat()


def snapshot(payload):
    if not payload:
        return {}
    data = json.loads(payload)
    return {
        'retrieved_chunks': data.get('retrieved', []),
        'reranked_chunks': data.get('reranked', []),
        'final_context': data.get('final_context', []),
        'timing': data.get('timing', {}),
        'sources': data.get('sources', []),
    }


class FeedbackLoopStore(FeedbackStore):
    def __init__(self, path):
        super().__init__(path)
        with self.connect() as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(answers)')}
            if 'request_id' not in columns:
                db.execute('ALTER TABLE answers ADD COLUMN request_id TEXT')
            if 'user_email' not in columns:
                db.execute('ALTER TABLE answers ADD COLUMN user_email TEXT')
            db.execute("UPDATE answers SET user_email=substr(session_id,1,instr(session_id,':')-1) WHERE user_email IS NULL AND instr(session_id,':')>0")
            columns = {row['name'] for row in db.execute('PRAGMA table_info(feedback_events)')}
            if 'payload' not in columns:
                db.execute('ALTER TABLE feedback_events ADD COLUMN payload TEXT')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS feedback_cases (
                    answer_id TEXT PRIMARY KEY REFERENCES answers(id),
                    user_email TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    request_id TEXT, question TEXT NOT NULL, answer TEXT NOT NULL,
                    sources TEXT NOT NULL, answer_status TEXT NOT NULL,
                    feedback TEXT NOT NULL CHECK(feedback IN ('like','dislike')),
                    feedback_reason TEXT, note TEXT, trace_snapshot TEXT NOT NULL,
                    attribution TEXT NOT NULL, workflow_status TEXT NOT NULL,
                    timestamp TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS regression_cases (
                    answer_id TEXT PRIMARY KEY REFERENCES answers(id),
                    question TEXT NOT NULL, expected_status TEXT NOT NULL,
                    expected_source TEXT, expected_text TEXT, expected_behavior TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS feedback_cases_filter
                    ON feedback_cases(feedback,user_email,feedback_reason,answer_status);
            ''')
            # Keep the old columns for historical feedback; new entries separate
            # the visitor's experience from the administrator's diagnosis.
            columns = {row['name'] for row in db.execute('PRAGMA table_info(feedback_cases)')}
            for name in ('user_feedback_reason', 'user_comment', 'admin_root_cause'):
                if name not in columns:
                    db.execute(f'ALTER TABLE feedback_cases ADD COLUMN {name} TEXT')
            db.execute('''CREATE INDEX IF NOT EXISTS feedback_cases_user_reason
                ON feedback_cases(feedback,user_email,user_feedback_reason,answer_status)''')
            # Existing active ratings remain visible after migration.
            db.execute('''INSERT OR IGNORE INTO feedback_cases
                (answer_id,user_email,conversation_id,request_id,question,answer,sources,
                 answer_status,feedback,feedback_reason,note,trace_snapshot,
                 attribution,workflow_status,timestamp,updated_at)
                SELECT a.id,a.user_email,a.session_id,a.request_id,a.question,a.answer,a.sources,
                 CASE WHEN a.refused=1 THEN 'REFUSE' ELSE 'ANSWER' END,
                 CASE WHEN f.rating='up' THEN 'like' ELSE 'dislike' END,
                 NULL,NULL,'{}','待人工判断','待处理',f.updated_at,f.updated_at
                FROM answers a JOIN feedback f ON f.answer_id=a.id
                WHERE f.rating IN ('up','down') AND a.user_email IS NOT NULL''')
            # Preserve the few legacy options that describe user experience.
            # Technical guesses made with the previous UI are kept only in the
            # old column, never presented as current user feedback.
            db.execute('''UPDATE feedback_cases SET user_feedback_reason=CASE feedback_reason
                WHEN '回答太长' THEN '回答太长'
                WHEN '回答太短' THEN '回答太简略'
                WHEN '回答没有覆盖重点' THEN '没有覆盖我关心的重点'
                WHEN '其他' THEN '其他' END
                WHERE user_feedback_reason IS NULL AND feedback_reason IS NOT NULL''')
            db.execute('''UPDATE feedback_cases SET user_comment=note
                WHERE user_comment IS NULL AND user_feedback_reason='其他' AND note IS NOT NULL''')
            db.execute('''UPDATE feedback_cases SET admin_root_cause=CASE attribution
                WHEN 'UX / 长度问题' THEN '回答风格问题'
                WHEN 'Retrieval 问题' THEN 'Retrieval 问题'
                WHEN 'Generation / Prompt 问题' THEN 'Generation / Prompt 问题'
                WHEN 'Refusal 问题' THEN 'Refusal 问题'
                WHEN 'Citation 问题' THEN 'Citation 问题'
                ELSE '待人工判断' END WHERE admin_root_cause IS NULL''')
            has_inspector = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inspector_records'").fetchone()
            if has_inspector:
                db.execute('''UPDATE feedback_cases SET request_id=(
                    SELECT r.request_id FROM inspector_records r WHERE r.answer_id=feedback_cases.answer_id
                ) WHERE request_id IS NULL AND EXISTS (
                    SELECT 1 FROM inspector_records r WHERE r.answer_id=feedback_cases.answer_id)''')
                for row in db.execute('''SELECT c.answer_id,r.payload,r.status,r.request_id
                    FROM feedback_cases c JOIN inspector_records r ON r.answer_id=c.answer_id
                    WHERE c.trace_snapshot='{}' ''').fetchall():
                    db.execute('''UPDATE feedback_cases SET trace_snapshot=?,answer_status=?,request_id=?
                        WHERE answer_id=?''', (json.dumps(snapshot(row['payload']), ensure_ascii=False),
                                             row['status'], row['request_id'], row['answer_id']))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def save_answer(self, session_id, question, result, history, request_id, user_email):
        answer_id = str(uuid4())
        with self.connect() as db:
            db.execute('''INSERT INTO answers
                (id,session_id,question,answer,sources,refused,history,created_at,request_id,user_email)
                VALUES (?,?,?,?,?,?,?,?,?,?)''', (
                answer_id, session_id, redact(question), redact(result.answer),
                json.dumps(redact([source.model_dump() for source in result.sources]), ensure_ascii=False),
                int(result.refused), json.dumps(redact([message.model_dump() for message in history]), ensure_ascii=False),
                now(), request_id, user_email,
            ))
        return answer_id

    def rate(self, answer_id, session_id, rating, user_email, reason=None, comment=None):
        canonical = {'up': 'like', 'down': 'dislike', 'like': 'like', 'dislike': 'dislike', 'none': 'none'}[rating]
        legacy = {'like': 'up', 'dislike': 'down', 'none': 'none'}[canonical]
        safe_comment = redact(comment)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            answer = db.execute('SELECT * FROM answers WHERE id=? AND session_id=? AND user_email=?',
                                (answer_id, session_id, user_email)).fetchone()
            if answer is None:
                return False
            previous = db.execute('SELECT rating FROM feedback WHERE answer_id=?', (answer_id,)).fetchone()
            existing = db.execute('SELECT * FROM feedback_cases WHERE answer_id=?', (answer_id,)).fetchone()
            if previous and previous['rating'] == legacy and (
                canonical == 'none' or (existing and existing['feedback'] == canonical
                                        and existing['user_feedback_reason'] == reason
                                        and existing['user_comment'] == safe_comment)
            ):
                return True
            timestamp = now()
            trace = db.execute('SELECT payload,status,request_id FROM inspector_records WHERE answer_id=?',
                               (answer_id,)).fetchone()
            answer_status = (trace['status'] if trace else 'REFUSE' if answer['refused'] else
                             'PARTIAL' if UNCERTAIN.search(answer['answer']) else 'ANSWER')
            trace_data = snapshot(trace['payload']) if trace else {}
            db.execute('''INSERT INTO feedback(answer_id,rating,updated_at) VALUES(?,?,?)
                ON CONFLICT(answer_id) DO UPDATE SET rating=excluded.rating,updated_at=excluded.updated_at''',
                       (answer_id, legacy, timestamp))
            event = {
                'user_email': user_email, 'conversation_id': session_id,
                'request_id': answer['request_id'] or (trace['request_id'] if trace else None),
                'question': answer['question'], 'answer': answer['answer'],
                'sources': json.loads(answer['sources']),
                'retrieved_chunks': trace_data.get('retrieved_chunks', []),
                'reranked_chunks': trace_data.get('reranked_chunks', []),
                'final_context': trace_data.get('final_context', []),
                'timing': trace_data.get('timing', {}),
                'answer_status': answer_status, 'feedback': canonical,
                'user_feedback_reason': reason, 'user_comment': safe_comment,
                'timestamp': timestamp,
            }
            db.execute('INSERT INTO feedback_events(answer_id,rating,created_at,payload) VALUES(?,?,?,?)',
                       (answer_id, legacy, timestamp, json.dumps(event, ensure_ascii=False)))
            if canonical == 'none':
                db.execute('DELETE FROM feedback_cases WHERE answer_id=?', (answer_id,))
            else:
                keep_triage = existing and existing['feedback'] == 'dislike' and canonical == 'dislike'
                root_cause = existing['admin_root_cause'] if keep_triage else '待人工判断'
                workflow = existing['workflow_status'] if keep_triage else '待处理'
                created = existing['timestamp'] if existing else timestamp
                db.execute('''INSERT INTO feedback_cases
                    (answer_id,user_email,conversation_id,request_id,question,answer,sources,
                     answer_status,feedback,feedback_reason,note,trace_snapshot,
                     attribution,workflow_status,timestamp,updated_at,
                     user_feedback_reason,user_comment,admin_root_cause)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(answer_id) DO UPDATE SET
                    feedback=excluded.feedback,user_feedback_reason=excluded.user_feedback_reason,
                    user_comment=excluded.user_comment,
                    request_id=excluded.request_id,answer_status=excluded.answer_status,
                    trace_snapshot=excluded.trace_snapshot,admin_root_cause=excluded.admin_root_cause,
                    workflow_status=excluded.workflow_status,updated_at=excluded.updated_at''', (
                    answer_id, user_email, session_id,
                    answer['request_id'] or (trace['request_id'] if trace else None),
                    answer['question'], answer['answer'], answer['sources'], answer_status,
                    canonical, None, None, json.dumps(trace_data, ensure_ascii=False),
                    '待人工判断', workflow, created, timestamp,
                    reason, safe_comment, root_cause,
                ))
        return True

    def list_dislikes(self, email=None, reason=None, answer_status=None):
        with self.connect() as db:
            rows = db.execute('''SELECT answer_id,user_email,conversation_id,request_id,
                question,answer_status,user_feedback_reason,admin_root_cause,workflow_status,timestamp
                FROM feedback_cases WHERE feedback='dislike'
                AND (? IS NULL OR user_email=?) AND (? IS NULL OR user_feedback_reason=?)
                AND (? IS NULL OR answer_status=?) ORDER BY timestamp DESC''',
                (email, email, reason, reason, answer_status, answer_status)).fetchall()
        return [dict(row) for row in rows]

    def get_case(self, answer_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM feedback_cases WHERE answer_id=?', (answer_id,)).fetchone()
            regression = db.execute('SELECT * FROM regression_cases WHERE answer_id=?', (answer_id,)).fetchone()
        if row is None:
            return None
        case = dict(row)
        case['sources'] = json.loads(case['sources'])
        case['trace_snapshot'] = json.loads(case['trace_snapshot'])
        case['regression'] = dict(regression) if regression else None
        return case

    def update_analysis(self, answer_id, admin_root_cause, workflow_status):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM feedback_cases WHERE answer_id=? AND feedback='dislike'", (answer_id,)).fetchone():
                return False
            if workflow_status == '已加入回归测试' and not db.execute(
                'SELECT 1 FROM regression_cases WHERE answer_id=?', (answer_id,)).fetchone():
                raise ValueError('请先将该问题加入回归测试集。')
            db.execute('''UPDATE feedback_cases SET admin_root_cause=?,workflow_status=?,updated_at=?
                WHERE answer_id=?''', (admin_root_cause, workflow_status, now(), answer_id))
        return True

    def add_regression(self, answer_id, expected_status, expected_source, expected_text, expected_behavior):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            case = db.execute("SELECT question,workflow_status FROM feedback_cases WHERE answer_id=? AND feedback='dislike'",
                              (answer_id,)).fetchone()
            if case is None:
                return False
            if case['workflow_status'] not in ('已修复', '已加入回归测试'):
                raise ValueError('请先将问题标记为“已修复”。')
            timestamp = now()
            db.execute('''INSERT INTO regression_cases
                (answer_id,question,expected_status,expected_source,expected_text,expected_behavior,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(answer_id) DO UPDATE SET
                expected_status=excluded.expected_status,expected_source=excluded.expected_source,
                expected_text=excluded.expected_text,expected_behavior=excluded.expected_behavior,
                updated_at=excluded.updated_at''', (
                answer_id, case['question'], expected_status, expected_source or None,
                expected_text or None, expected_behavior or None, timestamp, timestamp,
            ))
            db.execute("UPDATE feedback_cases SET workflow_status='已加入回归测试',updated_at=? WHERE answer_id=?",
                       (timestamp, answer_id))
        return True

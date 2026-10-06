import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


class FeedbackStore:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS answers (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                    question TEXT NOT NULL, answer TEXT NOT NULL,
                    sources TEXT NOT NULL, refused INTEGER NOT NULL,
                    history TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    answer_id TEXT PRIMARY KEY REFERENCES answers(id),
                    rating TEXT NOT NULL CHECK(rating IN ('up','down','none')),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feedback_events (
                    id INTEGER PRIMARY KEY, answer_id TEXT NOT NULL REFERENCES answers(id),
                    rating TEXT NOT NULL, created_at TEXT NOT NULL
                );
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def save_answer(self, session_id, question, result, history):
        answer_id = str(uuid4())
        with self.connect() as db:
            db.execute('INSERT INTO answers VALUES (?,?,?,?,?,?,?,?)', (
                answer_id, session_id, question, result.answer,
                json.dumps([s.model_dump() for s in result.sources], ensure_ascii=False),
                int(result.refused), json.dumps([m.model_dump() for m in history], ensure_ascii=False), now()))
        return answer_id

    def rate(self, answer_id, session_id, rating):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM answers WHERE id=? AND session_id=?', (answer_id, session_id)).fetchone():
                return False
            previous = db.execute('SELECT rating FROM feedback WHERE answer_id=?', (answer_id,)).fetchone()
            if previous and previous[0] == rating:
                return True
            timestamp = now()
            db.execute('INSERT INTO feedback VALUES (?,?,?) ON CONFLICT(answer_id) DO UPDATE SET rating=excluded.rating, updated_at=excluded.updated_at', (answer_id, rating, timestamp))
            db.execute('INSERT INTO feedback_events(answer_id,rating,created_at) VALUES (?,?,?)', (answer_id, rating, timestamp))
        return True

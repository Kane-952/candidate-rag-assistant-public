"""Export current like/dislike cases with their preserved RAG trace."""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.config import get_settings


def main():
    path = get_settings().feedback_db
    if not path.exists():
        print('No feedback database yet. Start the app first.')
        return
    destination = ROOT / 'data' / 'feedback' / ('export-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.jsonl')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('''SELECT answer_id,user_email,conversation_id,request_id,
            question,answer,sources,answer_status,feedback,user_feedback_reason,
            user_comment,admin_root_cause,workflow_status,timestamp,updated_at,trace_snapshot
            FROM feedback_cases ORDER BY timestamp''').fetchall()
        records = []
        for row in rows:
            record = dict(row)
            record['sources'] = json.loads(record['sources'])
            record.update(json.loads(record.pop('trace_snapshot')))
            records.append(json.dumps(record, ensure_ascii=False))
    destination.write_text(''.join(line + '\n' for line in records), encoding='utf-8')
    print(f'Exported {len(records)} feedback cases to {destination}')


if __name__ == '__main__':
    main()

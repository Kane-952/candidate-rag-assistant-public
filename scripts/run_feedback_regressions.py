"""Run locally curated feedback cases against the running chat API. Uses LLM credits."""
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.config import get_settings
from backend.app.inspector import UNCERTAIN
from scripts.evaluate_api import authenticate, check_health


def check_case(case, response):
    if response.status_code != 200:
        return False, 'HTTP ' + str(response.status_code)
    data = response.json()
    answer = data.get('answer', '')
    status = 'REFUSE' if data.get('refused') else 'PARTIAL' if UNCERTAIN.search(answer) else 'ANSWER'
    sources = data.get('sources', [])
    ok = status == case['expected_status']
    if case['expected_source']:
        ok = ok and any(item.get('source') == case['expected_source'] for item in sources)
    if case['expected_text']:
        ok = ok and case['expected_text'] in answer
    return bool(ok), status


def main():
    settings = get_settings()
    with sqlite3.connect(settings.feedback_db) as db:
        db.row_factory = sqlite3.Row
        cases = [dict(row) for row in db.execute('''SELECT answer_id,question,expected_status,
            expected_source,expected_text,expected_behavior FROM regression_cases ORDER BY created_at''')]
    if not cases:
        print('No feedback regression cases have been added yet.')
        return 0
    results = []
    with httpx.Client(base_url=settings.auth_public_url, timeout=300) as client:
        authenticate(client)
        check_health(client)
        for case in cases:
            response = client.post('/chat', json={
                'session_id': 'regression-' + uuid.uuid4().hex,
                'message': case['question'],
            })
            passed, actual_status = check_case(case, response)
            results.append({
                'answer_id': case['answer_id'], 'question': case['question'],
                'expected_status': case['expected_status'], 'actual_status': actual_status,
                'expected_behavior': case['expected_behavior'], 'passed': passed,
            })
            print(json.dumps({'case': case['answer_id'], 'passed': passed,
                              'expected': case['expected_status'], 'actual': actual_status}, ensure_ascii=False))
            if response.status_code != 200:
                break
    output = ROOT / 'data' / 'feedback' / ('regression-results-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
    output.write_text(json.dumps({
        'tested_at': datetime.now(timezone.utc).isoformat(), 'results': results,
        'all_passed': len(results) == len(cases) and all(item['passed'] for item in results),
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Report:', output)
    return 0 if len(results) == len(cases) and all(item['passed'] for item in results) else 1


if __name__ == '__main__':
    sys.exit(main())

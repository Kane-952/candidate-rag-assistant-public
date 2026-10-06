"""Exercise the running API with real configured models. May incur LLM API costs."""
import json
import sys
import time
import uuid
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.config import get_settings


def api_base_url():
    return get_settings().auth_public_url.rstrip('/')


def authenticate(client):
    cookie = os.environ.get('RAG_EVAL_SESSION_COOKIE')
    if cookie:
        client.cookies.set('candidate_session', cookie)
        return
    settings = get_settings()
    if settings.auth_delivery != 'local' or client.base_url.host not in ('localhost', '127.0.0.1', '::1'):
        raise SystemExit('Log in first, then set RAG_EVAL_SESSION_COOKIE for API evaluation.')
    email = os.environ.get('RAG_EVAL_EMAIL', 'reviewer@example.com').strip().lower()
    before = set(settings.auth_outbox_dir.glob('*.txt'))
    response = client.post('/auth/request', json={'email': email})
    response.raise_for_status()
    created = set(settings.auth_outbox_dir.glob('*.txt')) - before
    if len(created) != 1:
        raise SystemExit('No new local login link. Wait for the configured cooldown/IP window and retry.')
    link = created.pop().read_text(encoding='utf-8').splitlines()[-1]
    token = parse_qs(urlparse(link).fragment)['token'][0]
    response = client.post('/auth/verify', json={'token': token})
    response.raise_for_status()


def check_health(client):
    response = client.get('/health')
    if response.status_code != 200 or response.json().get('status') != 'ok':
        raise SystemExit('Service is not ready; check configuration, indexes and startup logs.')


def main():
    cases = [json.loads(line) for line in (ROOT / 'evaluation/questions.jsonl').read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    cases.insert(1, {
        'question': '为什么这么做？', 'expected_answerable': True,
        'expected_source': 'projects/demo_rag.md', 'follow_up': True,
    })
    chunks = {c['chunk_id']: c for c in (
        json.loads(line) for line in (ROOT / 'data/indexes/chunks.jsonl').read_text(encoding='utf-8-sig').splitlines()
    )}
    results = []
    session = 'eval-' + uuid.uuid4().hex
    output = ROOT / 'data/processed/live_evaluation.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=api_base_url(), timeout=300) as client:
        authenticate(client)
        check_health(client)
        for i, case in enumerate(cases):
            if i > 1:
                session = 'eval-' + uuid.uuid4().hex
            started = time.perf_counter()
            response = client.post('/chat', json={'session_id': session, 'message': case['question']})
            payload = response.json()
            sources = payload.get('sources', [])
            citations_valid = bool(sources) and all(
                s.get('chunk_id') in chunks and all(s.get(k) == chunks[s['chunk_id']][k] for k in ('source', 'title', 'text'))
                for s in sources
            )
            passed = response.status_code == 200 and payload.get('refused') is (not case['expected_answerable'])
            if case['expected_answerable']:
                passed = passed and citations_valid and any(s.get('source') == case['expected_source'] for s in sources)
            else:
                passed = passed and sources == []
            result = {
                **case, 'status_code': response.status_code, 'passed': bool(passed),
                'latency_seconds': round(time.perf_counter() - started, 2), 'response': payload,
            }
            results.append(result)
            output.write_text(json.dumps({
                'tested_at': datetime.now(timezone.utc).isoformat(), 'cases': results,
                'all_passed': all(r['passed'] for r in results), 'complete': len(results) == len(cases),
            }, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({
                'question': case['question'], 'passed': result['passed'], 'http': response.status_code,
                'answer': payload.get('answer', payload.get('detail')), 'latency': result['latency_seconds'],
            }, ensure_ascii=False), flush=True)
            if response.status_code != 200:
                break  # 配置/服务异常不重复消耗 API 请求。
    print('Report:', output)
    return 0 if len(results) == len(cases) and all(r['passed'] for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())


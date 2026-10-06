"""Measure four real streaming chats against a freshly started local API.

Uses the configured local invitation flow and configured LLM; incurs API calls.
Prints timings and status only, not answers, credentials, or login links.
"""
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate_api import authenticate, api_base_url, check_health


def main():
    session = 'latency-' + uuid.uuid4().hex
    new_session = 'latency-' + uuid.uuid4().hex
    cases = [
        ('服务启动后第1问', session, '介绍一下你的 RAG 项目。'),
        ('同一会话第2问', session, '你在这个项目中如何做检索？'),
        ('同一会话第3问', session, '你如何评估这个 RAG 系统？'),
        ('新对话第1问', new_session, '介绍一下你的 RAG 项目。'),
    ]
    with httpx.Client(base_url=api_base_url(), timeout=300) as client:
        authenticate(client)
        check_health(client)
        for label, session_id, question in cases:
            started = time.perf_counter()
            first_delta = None
            outcome = None
            with client.stream('POST', '/chat/stream', json={
                'session_id': session_id, 'message': question,
            }) as response:
                for line in response.iter_lines():
                    if not line:
                        continue
                    event = json.loads(line)
                    if event['type'] == 'delta' and first_delta is None:
                        first_delta = time.perf_counter() - started
                    if event['type'] in ('done', 'error'):
                        outcome = event['type']
            print(json.dumps({
                'case': label, 'http_status': response.status_code, 'outcome': outcome,
                'first_visible_ms': round(first_delta * 1000, 2) if first_delta is not None else None,
                'total_ms': round((time.perf_counter() - started) * 1000, 2),
            }, ensure_ascii=False), flush=True)
            if response.status_code != 200 or outcome != 'done':
                return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

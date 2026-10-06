import json
import sqlite3
from unittest.mock import Mock

from backend.app.main import create_app
from backend.app.errors import LLMUnavailable
from backend.app.feedback import FeedbackStore
from backend.app.inspector import InspectorStore
from backend.app.models.schemas import Answerability, ChatResponse, Chunk, GroundedAnswer, SearchHit
from backend.app.perf import RequestTrace, bind_trace, observe
from backend.app.rag.pipeline import RAGPipeline
from backend.tests.auth_helpers import EMAIL, client_for, login, make_settings


CHUNK_ID = '1234567890abcdef1234'


def test_inspector_admin_only_records_stages_feedback_and_redacts(tmp_path):
    settings = make_settings(tmp_path)
    settings.inspector_admin_emails = [EMAIL]
    app = create_app(settings)
    with client_for(app) as viewer, client_for(app) as admin:
        for path in ('/inspector', '/inspector/records', '/inspector/feedback',
                     '/inspector.html', '/inspector.css', '/inspector.js',
                     '/docs', '/openapi.json'):
            assert viewer.get(path, headers={'Accept': 'text/html'}).status_code == 401
        assert viewer.post('/inspector/feedback/00000000-0000-0000-0000-000000000000/analysis',
                           json={}).status_code == 401
        login(viewer, settings, 'viewer@example.com')
        for path in ('/inspector', '/inspector/style.css', '/inspector/app.js', '/inspector/records',
                     '/inspector.html', '/inspector.css', '/inspector.js',
                     '/inspector/feedback', '/docs', '/openapi.json'):
            assert viewer.get(path).status_code == 403
        assert viewer.post('/inspector/feedback/00000000-0000-0000-0000-000000000000/analysis',
                           json={}).status_code == 403
        login(admin, settings)
        assert admin.get('/inspector').status_code == 200
        assert admin.get('/inspector/app.js').status_code == 200
        assert admin.get('/docs').status_code == 200

        hit = SearchHit(source='resume.md', title='RAG 项目', chunk_id=CHUNK_ID,
                        text='使用 BM25 和 Dense 检索。', score=.04, rerank_score=.91)
        def answer(history, question, on_text=None):
            observe('rewritten_query', question)
            observe('retrieved', [hit.model_dump()])
            observe('reranked', [hit.model_dump()])
            observe('context_candidates', [hit.model_dump()])
            observe('final_context', [hit.model_dump()])
            observe('answerability', {'answerable': True, 'reason': '证据充分', 'confidence': .9, 'evidence_ids': [CHUNK_ID]})
            observe('decision', {'status': 'ANSWER', 'reason': '核验通过'})
            return ChatResponse(answer=f'我使用了 BM25 和 Dense。[ {CHUNK_ID} ]',
                                refused=False, sources=[Chunk(**hit.model_dump(include=set(Chunk.model_fields)))])
        app.state.pipeline = Mock(answer=Mock(side_effect=answer))
        response = admin.post('/chat', json={'session_id':'a', 'message':'RAG 项目？ password=secret123'})
        assert response.status_code == 200
        answer_id = response.json()['answer_id']
        assert admin.post('/feedback', json={'answer_id':answer_id, 'session_id':'a', 'rating':'down', 'user_feedback_reason':'回答不够清楚'}).status_code == 200
        records = admin.get('/inspector/records').json()['records']
        assert len(records) == 1
        assert records[0]['user_email'] == EMAIL
        assert records[0]['conversation_id'] == f'{EMAIL}:a'
        assert records[0]['timestamp']
        assert 'secret123' not in records[0]['question']
        request_id = records[0]['request_id']
        assert viewer.get('/inspector/records/' + request_id).status_code == 403
        detail = admin.get('/inspector/records/' + request_id).json()
        assert detail['retrieved'][0]['chunk_id'] == CHUNK_ID
        assert detail['final_context'][0]['rerank_score'] == .91
        assert detail['status'] == 'ANSWER' and detail['feedback'] == 'down'
        assert detail['user_email'] == EMAIL and detail['conversation_id'] == f'{EMAIL}:a'
        assert detail['timing']['total_ms'] >= 0
        assert 'secret123' not in json.dumps(detail)
        assert 'api_key' not in json.dumps(detail).lower()


def test_pipeline_observes_refusal_and_selected_context(tmp_path):
    settings = make_settings(tmp_path)
    pipeline = object.__new__(RAGPipeline)
    pipeline.settings = settings
    hit = SearchHit(source='project.md', title='项目', chunk_id=CHUNK_ID,
                    text='项目证据', score=.05)
    reranked = hit.model_copy(update={'rerank_score': .8})
    pipeline.chunks = []
    pipeline.retriever = Mock(search=Mock(return_value=[hit]))
    pipeline.reranker = Mock(rerank=Mock(return_value=[reranked]))
    pipeline.gate = Mock(check_answerability=Mock(return_value=Answerability(
        answerable=True, reason='有证据', confidence=.95, evidence_ids=[CHUNK_ID])))
    pipeline.llm = Mock()
    pipeline.llm.grounded_answer.return_value = GroundedAnswer(
        answer=f'我做过这个项目。[ {CHUNK_ID} ]', evidence_ids=[CHUNK_ID], refused=False)
    trace = RequestTrace('/chat')
    trace.inspector = {}
    with bind_trace(trace):
        result = pipeline.answer([], '项目？')
    assert result.refused  # Deliberately malformed citation is rejected by the existing gate.
    assert trace.inspector['retrieved'][0]['score'] == .05
    assert trace.inspector['reranked'][0]['rerank_score'] == .8
    assert trace.inspector['final_context'][0]['chunk_id'] == CHUNK_ID
    assert trace.inspector['decision']['status'] == 'REFUSE'


def test_stream_error_records_failure_without_visible_draft(tmp_path):
    settings = make_settings(tmp_path)
    with client_for(create_app(settings)) as client:
        login(client, settings)
        assert client.get('/inspector').status_code == 403
        assert client.get('/inspector.html').status_code == 403

    settings.inspector_admin_emails = [EMAIL]
    with sqlite3.connect(settings.auth_db) as db:
        db.execute('UPDATE email_cooldowns SET last_issued_at=0')
    app = create_app(settings)
    with client_for(app) as client:
        login(client, settings)
        def broken_answer(history, question, on_text=None):
            observe('rewritten_query', question)
            on_text('我先解释已确认的部分。')
            raise LLMUnavailable('生成中断。')
        app.state.pipeline = Mock(answer=Mock(side_effect=broken_answer))
        events = [json.loads(line) for line in client.post(
            '/chat/stream', json={'session_id':'a', 'message':'请介绍项目'}).text.splitlines()]
        assert [event['type'] for event in events] == ['start', 'error']
        detail_id = client.get('/inspector/records').json()['records'][0]['request_id']
        detail = client.get('/inspector/records/' + detail_id).json()
        assert detail['status'] == 'ERROR'
        assert detail['answer'] != '我先解释已确认的部分。'
        assert detail['status_reason'] == '请求未完成。'
        assert detail['error'] == '生成中断。'
        assert detail['timing']['durations_ms']['auth_session'] >= 0
        assert detail['user_email'] == EMAIL and detail['conversation_id'] == f'{EMAIL}:a'


def test_email_filter_and_multiple_conversations_use_verified_identity(tmp_path):
    settings = make_settings(tmp_path)
    other = 'interviewer@example.com'
    settings.inspector_admin_emails = [EMAIL]
    app = create_app(settings)
    with client_for(app) as admin, client_for(app) as interviewer:
        login(admin, settings)
        login(interviewer, settings, other)
        app.state.pipeline = Mock(answer=Mock(return_value=ChatResponse(answer='我做过 RAG。', refused=False)))
        for session_id, question in [('first', '第一问'), ('first', '第二问'), ('new', '新对话第一问')]:
            assert admin.post('/chat', json={'session_id': session_id, 'message': question}).status_code == 200
        # A client-supplied identity field must not override the verified Session email.
        assert interviewer.post('/chat', json={
            'session_id': 'first', 'message': '面试官提问', 'user_email': EMAIL,
        }).status_code == 200
        own = admin.get('/inspector/records', params={'email': EMAIL.upper()}).json()['records']
        assert len(own) == 3
        assert {item['conversation_id'] for item in own} == {f'{EMAIL}:first', f'{EMAIL}:new'}
        assert [item['question'] for item in own] == ['新对话第一问', '第二问', '第一问']
        theirs = admin.get('/inspector/records', params={'email': other}).json()['records']
        assert len(theirs) == 1 and theirs[0]['user_email'] == other
        assert theirs[0]['conversation_id'] == f'{other}:first'
        assert interviewer.get('/inspector/records', params={'email': EMAIL}).status_code == 403


def test_existing_inspector_records_migrate_identity_fields(tmp_path):
    settings = make_settings(tmp_path)
    FeedbackStore(settings.feedback_db)
    with sqlite3.connect(settings.feedback_db) as db:
        db.execute('''INSERT INTO answers VALUES (?,?,?,?,?,?,?,?)''', (
            'answer-old', f'{EMAIL}:old-chat', '旧问题', '旧回答', '[]', 0, '[]',
            '2026-01-01T00:00:00+00:00'))
        db.execute('''CREATE TABLE inspector_records (
            request_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, email TEXT NOT NULL,
            answer_id TEXT, status TEXT NOT NULL, question TEXT NOT NULL,
            total_ms REAL, payload TEXT NOT NULL)''')
        db.execute('''INSERT INTO inspector_records VALUES (?,?,?,?,?,?,?,?)''', (
            'abcdef123456', '2026-01-01T00:00:00+00:00', EMAIL, 'answer-old',
            'ANSWER', '旧问题', 100.0, '{}'))
    store = InspectorStore(settings.feedback_db)
    row = store.recent(email=EMAIL)[0]
    assert row['user_email'] == EMAIL
    assert row['timestamp'] == '2026-01-01T00:00:00+00:00'
    assert row['conversation_id'] == f'{EMAIL}:old-chat'

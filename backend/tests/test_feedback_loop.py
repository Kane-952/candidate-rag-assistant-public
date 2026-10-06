import json
import sqlite3
from unittest.mock import Mock

from backend.app.main import create_app
from backend.app.models.schemas import ChatResponse, Chunk, SearchHit
from backend.app.perf import observe
from backend.app.perf import RequestTrace
from backend.app.feedback_loop import FeedbackLoopStore
from backend.app.feedback import FeedbackStore
from backend.app.inspector import InspectorStore
from backend.tests.auth_helpers import EMAIL, client_for, login, make_settings
from scripts.run_feedback_regressions import check_case


CHUNK_ID = '1234567890abcdef1234'


def test_feedback_trace_filters_triage_and_regression(tmp_path):
    settings = make_settings(tmp_path)
    other = 'interviewer@example.com'
    settings.inspector_admin_emails = [EMAIL]
    app = create_app(settings)
    hit = SearchHit(source='projects/rag-system.md', title='RAG 项目', chunk_id=CHUNK_ID,
                    text='使用 BM25 和 Dense 检索。', score=.04, rerank_score=.91)

    def answer(history, question, on_text=None):
        observe('rewritten_query', question)
        observe('retrieved', [hit.model_dump()])
        observe('reranked', [hit.model_dump()])
        observe('final_context', [hit.model_dump()])
        return ChatResponse(answer='我使用了 BM25 和 Dense。', refused=False,
                            sources=[Chunk(**hit.model_dump(include=set(Chunk.model_fields)))])

    with client_for(app) as admin, client_for(app) as viewer:
        login(admin, settings)
        login(viewer, settings, other)
        app.state.pipeline = Mock(answer=Mock(side_effect=answer))
        result = viewer.post('/chat', json={
            'session_id': 'interview-a', 'message': 'RAG 怎么做的？', 'user_email': EMAIL,
        }).json()
        answer_id = result['answer_id']
        base = {'answer_id': answer_id, 'session_id': 'interview-a'}
        assert viewer.post('/feedback', json={**base, 'rating': 'dislike'}).status_code == 422
        assert viewer.post('/feedback', json={**base, 'rating': 'like'}).json()['feedback'] == 'like'
        assert viewer.post('/feedback', json={**base, 'rating': 'dislike',
            'user_feedback_reason': '其他', 'user_comment': '  具体步骤没有说明  '}).json()['feedback'] == 'dislike'
        assert viewer.get('/inspector/feedback').status_code == 403
        rows = admin.get('/inspector/feedback', params={
            'email': other.upper(), 'reason': '其他', 'answer_status': 'ANSWER',
        }).json()['records']
        assert len(rows) == 1
        assert rows[0]['user_email'] == other
        assert rows[0]['conversation_id'] == f'{other}:interview-a'
        assert rows[0]['user_feedback_reason'] == '其他'
        assert rows[0]['admin_root_cause'] == '待人工判断'
        assert admin.get('/inspector/feedback', params={'reason':'回答太简略'}).json()['records'] == []
        record = admin.get('/inspector/feedback/' + answer_id).json()
        case = record['feedback_case']
        assert case['feedback'] == 'dislike' and case['user_comment'] == '具体步骤没有说明'
        assert case['user_feedback_reason'] == '其他'
        assert case['admin_root_cause'] == '待人工判断'
        assert case['request_id'] == record['request_id']
        assert case['trace_snapshot']['retrieved_chunks'][0]['chunk_id'] == CHUNK_ID
        assert case['trace_snapshot']['reranked_chunks'][0]['rerank_score'] == .91
        assert case['trace_snapshot']['final_context'][0]['chunk_id'] == CHUNK_ID
        assert case['trace_snapshot']['timing']['total_ms'] >= 0
        assert case['sources'][0]['source'] == 'projects/rag-system.md'
        assert case['answer_status'] == 'ANSWER'
        assert admin.post('/inspector/feedback/' + answer_id + '/analysis', json={
            'admin_root_cause': 'Retrieval 问题', 'workflow_status': '已加入回归测试',
        }).status_code == 409
        assert admin.post('/inspector/feedback/' + answer_id + '/analysis', json={
            'admin_root_cause': '知识库资料不足', 'workflow_status': '已修复',
        }).status_code == 200
        analyzed = admin.get('/inspector/feedback/' + answer_id).json()['feedback_case']
        assert analyzed['user_feedback_reason'] == '其他'
        assert analyzed['admin_root_cause'] == '知识库资料不足'
        assert admin.post('/inspector/feedback/' + answer_id + '/regression', json={
            'expected_status': 'ANSWER', 'expected_source': 'projects/rag-system.md',
            'expected_text': 'BM25', 'expected_behavior': '说明检索步骤',
        }).status_code == 200
        updated = admin.get('/inspector/feedback/' + answer_id).json()['feedback_case']
        assert updated['workflow_status'] == '已加入回归测试'
        assert updated['regression']['expected_text'] == 'BM25'
        with sqlite3.connect(settings.feedback_db) as db:
            events = [json.loads(row[0]) for row in db.execute(
                'SELECT payload FROM feedback_events ORDER BY id')]
            assert [event['feedback'] for event in events] == ['like', 'dislike']
            assert events[-1]['user_email'] == other
            assert events[-1]['request_id'] == case['request_id']
            assert events[-1]['retrieved_chunks'][0]['chunk_id'] == CHUNK_ID
            assert events[-1]['user_feedback_reason'] == '其他'
            assert events[-1]['user_comment'] == '具体步骤没有说明'
            assert db.execute('SELECT COUNT(*) FROM regression_cases').fetchone()[0] == 1
        assert check_case(updated['regression'], Mock(status_code=200, json=Mock(return_value={
            'answer':'我使用了 BM25。', 'refused':False,
            'sources':[{'source':'projects/rag-system.md'}],
        })))[0]
        assert viewer.post('/feedback', json={**base, 'rating':'none'}).status_code == 200
        assert admin.get('/inspector/feedback').json()['records'] == []
        with sqlite3.connect(settings.feedback_db) as db:
            assert db.execute('SELECT COUNT(*) FROM regression_cases').fetchone()[0] == 1


def test_dislike_reason_validation_and_private_email(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with client_for(app) as client:
        login(client, settings)
        app.state.pipeline = Mock(answer=Mock(return_value=ChatResponse(answer='资料不足', refused=True)))
        answer_id = client.post('/chat', json={'session_id':'one', 'message':'问题'}).json()['answer_id']
        base = {'answer_id':answer_id, 'session_id':'one'}
        for technical_reason in ('回答不准确', '资料里有，但没有找到', '引用来源不正确', '不应该拒答'):
            assert client.post('/feedback', json={**base, 'rating':'dislike',
                'user_feedback_reason': technical_reason}).status_code == 422
        assert client.post('/feedback', json={**base, 'rating':'dislike',
            'user_feedback_reason':'没有回答我的问题', 'user_email':'other@example.com'}).status_code == 200
        with sqlite3.connect(settings.feedback_db) as db:
            email, status, reason, cause = db.execute('''SELECT user_email,answer_status,
                user_feedback_reason,admin_root_cause FROM feedback_cases''').fetchone()
            assert email == EMAIL and status == 'REFUSE'
            assert reason == '没有回答我的问题' and cause == '待人工判断'
        assert client.post('/feedback', json={**base, 'rating':'dislike',
            'user_feedback_reason':'回答太长', 'user_comment':'不能写备注'}).status_code == 422


def test_feedback_submitted_before_trace_is_backfilled(tmp_path):
    settings = make_settings(tmp_path)
    feedback = FeedbackLoopStore(settings.feedback_db)
    inspector = InspectorStore(settings.feedback_db)
    trace = RequestTrace('/chat/stream')
    trace.inspector = {
        'retrieved': [{'chunk_id': CHUNK_ID, 'text': '证据'}],
        'reranked': [{'chunk_id': CHUNK_ID, 'rerank_score': .91}],
        'final_context': [{'chunk_id': CHUNK_ID, 'text': '证据'}],
    }
    trace.finish('done')
    answer = ChatResponse(answer='我使用了证据。', refused=False)
    conversation_id = f'{EMAIL}:race'
    answer_id = feedback.save_answer(conversation_id, '问题', answer, [], trace.request_id, EMAIL)
    answer = answer.model_copy(update={'answer_id': answer_id})
    assert feedback.rate(answer_id, conversation_id, 'dislike', EMAIL, '其他', '验证码为123456')
    assert feedback.get_case(answer_id)['trace_snapshot'] == {}
    inspector.save(trace, EMAIL, conversation_id, '问题', result=answer)
    case = feedback.get_case(answer_id)
    assert case['trace_snapshot']['retrieved_chunks'][0]['chunk_id'] == CHUNK_ID
    assert case['trace_snapshot']['reranked_chunks'][0]['rerank_score'] == .91
    assert '123456' not in case['user_comment']
    with sqlite3.connect(settings.feedback_db) as db:
        event = json.loads(db.execute('SELECT payload FROM feedback_events').fetchone()[0])
    assert event['retrieved_chunks'][0]['chunk_id'] == CHUNK_ID
    assert event['request_id'] == trace.request_id


def test_old_technical_reasons_are_not_relabelled_as_user_experience(tmp_path):
    path = tmp_path / 'feedback.sqlite3'
    FeedbackStore(path)
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE feedback_cases (
            answer_id TEXT PRIMARY KEY REFERENCES answers(id),
            user_email TEXT NOT NULL, conversation_id TEXT NOT NULL,
            request_id TEXT, question TEXT NOT NULL, answer TEXT NOT NULL,
            sources TEXT NOT NULL, answer_status TEXT NOT NULL,
            feedback TEXT NOT NULL, feedback_reason TEXT, note TEXT,
            trace_snapshot TEXT NOT NULL, attribution TEXT NOT NULL,
            workflow_status TEXT NOT NULL, timestamp TEXT NOT NULL, updated_at TEXT NOT NULL
        )''')
        for i, reason in enumerate(('回答不准确', '回答太短')):
            answer_id = f'old-{i}'
            db.execute('INSERT INTO answers VALUES (?,?,?,?,?,?,?,?)', (
                answer_id, f'{EMAIL}:old', '旧问题', '旧回答', '[]', 0, '[]',
                '2026-01-01T00:00:00+00:00'))
            db.execute('''INSERT INTO feedback_cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                answer_id, EMAIL, f'{EMAIL}:old', None, '旧问题', '旧回答', '[]',
                'ANSWER', 'dislike', reason, None, '{}', 'Retrieval 问题',
                '已分析', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'))
    store = FeedbackLoopStore(path)
    technical = store.get_case('old-0')
    concise = store.get_case('old-1')
    assert technical['user_feedback_reason'] is None
    assert technical['feedback_reason'] == '回答不准确'
    assert technical['admin_root_cause'] == 'Retrieval 问题'
    assert concise['user_feedback_reason'] == '回答太简略'
    assert len(store.list_dislikes()) == 2
    FeedbackLoopStore(path)  # Restart does not overwrite the separate fields.
    assert store.get_case('old-0')['user_feedback_reason'] is None

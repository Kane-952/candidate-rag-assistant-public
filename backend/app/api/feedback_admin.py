"""Administrator-only feedback triage and regression endpoints."""
import sqlite3
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from ..feedback_loop import ANSWER_STATUSES, ATTRIBUTIONS, FEEDBACK_REASONS, WORKFLOW_STATUSES
from .inspector import require_admin


router = APIRouter()


def admin_feedback(request):
    require_admin(request)
    return request.app.state.feedback


class AnalysisRequest(BaseModel):
    admin_root_cause: str
    workflow_status: str

    @model_validator(mode='after')
    def valid_values(self):
        if self.admin_root_cause not in ATTRIBUTIONS or self.workflow_status not in WORKFLOW_STATUSES:
            raise ValueError('归因或处理状态不合法。')
        return self


class RegressionRequest(BaseModel):
    expected_status: str
    expected_source: str | None = Field(default=None, max_length=300)
    expected_text: str | None = Field(default=None, max_length=200)
    expected_behavior: str | None = Field(default=None, max_length=500)

    @model_validator(mode='after')
    def valid_values(self):
        if self.expected_status not in ANSWER_STATUSES:
            raise ValueError('期望回答状态不合法。')
        for name in ('expected_source', 'expected_text', 'expected_behavior'):
            value = getattr(self, name)
            setattr(self, name, value.strip() or None if value else None)
        return self


@router.get('/inspector/feedback')
def feedback_list(
    request: Request, email: str | None = Query(default=None, max_length=254),
    reason: str | None = Query(default=None, max_length=40),
    answer_status: str | None = Query(default=None, max_length=10),
):
    store = admin_feedback(request)
    if reason and reason not in FEEDBACK_REASONS:
        raise HTTPException(422, '点踩原因不合法。')
    if answer_status and answer_status not in ANSWER_STATUSES:
        raise HTTPException(422, '回答状态不合法。')
    normalized = email.strip().lower() if email else None
    return {'records': store.list_dislikes(normalized or None, reason or None, answer_status or None)}


@router.get('/inspector/feedback/{answer_id}')
def feedback_detail(answer_id: UUID, request: Request):
    store = admin_feedback(request)
    case = store.get_case(str(answer_id))
    if case is None or case['feedback'] != 'dislike':
        raise HTTPException(404, '点踩记录不存在。')
    trace = request.app.state.inspector.get(case['request_id']) if case['request_id'] else None
    if trace is None:
        snapshot = case['trace_snapshot']
        trace = {
            'request_id': case['request_id'], 'user_email': case['user_email'],
            'conversation_id': case['conversation_id'], 'timestamp': case['timestamp'],
            'question': case['question'], 'answer': case['answer'],
            'sources': case['sources'], 'status': case['answer_status'],
            'status_reason': '历史记录未保存完整调试链路。',
            'retrieved': snapshot.get('retrieved_chunks', []),
            'reranked': snapshot.get('reranked_chunks', []),
            'final_context': snapshot.get('final_context', []),
            'timing': snapshot.get('timing', {}), 'feedback': 'down',
        }
    trace['feedback_case'] = case
    return trace


@router.post('/inspector/feedback/{answer_id}/analysis')
def feedback_analysis(answer_id: UUID, body: AnalysisRequest, request: Request):
    store = admin_feedback(request)
    try:
        found = store.update_analysis(str(answer_id), body.admin_root_cause, body.workflow_status)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except sqlite3.Error:
        raise HTTPException(503, '分析状态暂时无法保存。') from None
    if not found:
        raise HTTPException(404, '点踩记录不存在。')
    return {'saved': True, 'admin_root_cause': body.admin_root_cause, 'workflow_status': body.workflow_status}


@router.post('/inspector/feedback/{answer_id}/regression')
def feedback_regression(answer_id: UUID, body: RegressionRequest, request: Request):
    store = admin_feedback(request)
    try:
        found = store.add_regression(
            str(answer_id), body.expected_status, body.expected_source,
            body.expected_text, body.expected_behavior,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except sqlite3.Error:
        raise HTTPException(503, '回归案例暂时无法保存。') from None
    if not found:
        raise HTTPException(404, '点踩记录不存在。')
    return {'saved': True, 'workflow_status': '已加入回归测试'}

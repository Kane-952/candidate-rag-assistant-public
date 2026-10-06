import sqlite3
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from ..auth import scoped_conversation
from ..feedback_loop import FEEDBACK_REASONS


router = APIRouter()


class FeedbackRequest(BaseModel):
    answer_id: UUID
    session_id: str = Field(min_length=1, max_length=100, pattern=r'^[\w-]+$')
    rating: Literal['up', 'down', 'none', 'like', 'dislike']
    user_feedback_reason: str | None = Field(default=None, max_length=40)
    user_comment: str | None = Field(default=None, max_length=300)

    @model_validator(mode='after')
    def validate_reason(self):
        is_dislike = self.rating in ('down', 'dislike')
        if is_dislike and self.user_feedback_reason not in FEEDBACK_REASONS:
            raise ValueError('点踩时请选择原因。')
        if not is_dislike and (self.user_feedback_reason or self.user_comment):
            raise ValueError('点赞或取消评价不需要点踩原因。')
        if self.user_feedback_reason != '其他' and self.user_comment:
            raise ValueError('仅“其他”原因可以填写备注。')
        self.user_comment = self.user_comment.strip() or None if self.user_comment else None
        return self


@router.post('/feedback')
def feedback(body: FeedbackRequest, request: Request):
    # The owner comes only from the verified Session, never from the request body.
    user_email = request.state.auth_email
    conversation_id = scoped_conversation(user_email, body.session_id)
    try:
        found = request.app.state.feedback.rate(
            str(body.answer_id), conversation_id, body.rating, user_email,
            body.user_feedback_reason, body.user_comment,
        )
    except sqlite3.Error:
        raise HTTPException(503, '反馈暂时无法保存，请稍后重试。') from None
    if not found:
        raise HTTPException(404, '未找到当前会话的回答。')
    canonical = {'up': 'like', 'down': 'dislike'}.get(body.rating, body.rating)
    return {'saved': True, 'feedback': canonical, 'rating': body.rating}

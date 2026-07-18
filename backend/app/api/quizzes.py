"""quizzes API（plan/03 §一 POST /quizzes/{id}/submit，P2 iter 42）。

提交作答：记录 quiz_attempts 行并判分（choice 服务器比对 payload.answer；
fill/short 自评题 correct=None）。进度掌握度回写仍走 POST /progress（前端聚合），
两端点解耦（submit 只管作答记录与判分）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth
from app.api.errors import ApiError
from app.core.ratelimit import limiter
from app.db import models
from app.db.models import User

router = APIRouter(prefix="/quizzes", tags=["quizzes"], dependencies=[Depends(require_auth)])

# 提交限流（err.md A23：此前可无限刷提交记录，quiz_attempts 行无限增长）
_SUBMIT_RATE_PER_MIN = 60


class SubmitIn(BaseModel):
    user_answer: dict  # choice: {"selected": "..."}；fill/short: {"text": "..."}


@router.post("/{quiz_id}/submit")
def submit_quiz(
    quiz_id: int,
    body: SubmitIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """提交作答 -> { correct, answer, explanation }（plan/03 §一）。"""
    quiz = db.get(models.Quiz, quiz_id)
    # P4 隔离：他人方案的题 404（不泄露存在性）
    if quiz is None or quiz.plan is None or quiz.plan.session is None or quiz.plan.session.user_id != user.id:
        raise ApiError("not_found", f"测验题 {quiz_id} 不存在")
    if not limiter.check(f"quiz_submit:{user.id}", _SUBMIT_RATE_PER_MIN):
        raise ApiError("rate_limited", "提交过于频繁，请稍后再试", status=429)
    payload = quiz.payload or {}
    # choice 服务器判分；fill/short 自评（correct=None）
    correct: bool | None = None
    if quiz.type == models.QuizType.choice:
        # payload 缺 answer（异常/降级数据）时判 None——否则 None==None 误判正确，
        # 空提交即可刷「答对」虚增掌握度
        answer = payload.get("answer")
        if answer is not None:
            correct = body.user_answer.get("selected") == answer
    db.add(models.QuizAttempt(quiz_id=quiz.id, user_answer=body.user_answer, correct=correct))
    db.commit()
    return {
        "correct": correct,
        "answer": payload.get("answer", ""),
        "explanation": payload.get("explanation", ""),
    }

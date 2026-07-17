"""quizzes API（plan/03 §一 POST /quizzes/{id}/submit，P2 iter 42）。

提交作答：记录 quiz_attempts 行并判分（choice 服务器比对 payload.answer；
fill/short 自评题 correct=None）。进度掌握度回写仍走 POST /progress（前端聚合），
两端点解耦（submit 只管作答记录与判分）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_auth
from app.api.errors import ApiError
from app.db import models

router = APIRouter(prefix="/quizzes", tags=["quizzes"], dependencies=[Depends(require_auth)])


class SubmitIn(BaseModel):
    user_answer: dict  # choice: {"selected": "..."}；fill/short: {"text": "..."}


@router.post("/{quiz_id}/submit")
def submit_quiz(quiz_id: int, body: SubmitIn, db: Session = Depends(get_db)) -> dict:
    """提交作答 -> { correct, answer, explanation }（plan/03 §一）。"""
    quiz = db.get(models.Quiz, quiz_id)
    if quiz is None:
        raise ApiError("not_found", f"测验题 {quiz_id} 不存在")
    payload = quiz.payload or {}
    # choice 服务器判分；fill/short 自评（correct=None）
    correct: bool | None = None
    if quiz.type == models.QuizType.choice:
        correct = body.user_answer.get("selected") == payload.get("answer")
    db.add(models.QuizAttempt(quiz_id=quiz.id, user_answer=body.user_answer, correct=correct))
    db.commit()
    return {
        "correct": correct,
        "answer": payload.get("answer", ""),
        "explanation": payload.get("explanation", ""),
    }

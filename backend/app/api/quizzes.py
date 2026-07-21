"""quizzes API（plan/03 §一 POST /quizzes/{id}/submit，P2 iter 42）。

提交作答：记录 quiz_attempts 行并判分（choice 服务器比对 payload.answer；
fill/short 自评题 correct=None）。进度掌握度回写仍走 POST /progress（前端聚合），
两端点解耦（submit 只管作答记录与判分）。

GET /quizzes/wrong-overview（iter 156）：跨方案错题总览——每方案当前错题数（Wiki 全局视图）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth
from app.api.errors import ApiError
from app.core.ratelimit import limiter
from app.db import models
from app.db.models import User

router = APIRouter(prefix="/quizzes", tags=["quizzes"], dependencies=[Depends(require_auth)])

# 提交限流（err.md A23：此前可无限刷提交记录，quiz_attempts 行无限增长）
_SUBMIT_RATE_PER_MIN = 60
# 作答内容大小上限（防配合 body 上限写入数十 MB JSON 造成 DB 膨胀；choice 仅几字节，
# fill/short 人工作答 1 万字符绰绰有余）
_MAX_ANSWER_CHARS = 10_000


class SubmitIn(BaseModel):
    user_answer: dict  # choice: {"selected": "..."}；fill/short: {"text": "..."}

    @field_validator("user_answer")
    @classmethod
    def _cap_size(cls, v: dict) -> dict:
        if len(json.dumps(v, ensure_ascii=False)) > _MAX_ANSWER_CHARS:
            raise ValueError(f"作答内容超过 {_MAX_ANSWER_CHARS} 字符上限")
        return v


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
    # 后端插件事件（API.md「后端插件 API」§事件清单）：监听器异常隔离，不影响响应
    from app.plugins import bus as plugin_bus

    plugin_bus.emit(
        "quiz_submitted",
        {
            "user_id": user.id,
            "quiz_id": quiz.id,
            "plan_id": quiz.plan_id,
            "type": quiz.type.value if quiz.type is not None else None,
            "correct": correct,
        },
    )
    return {
        "correct": correct,
        "answer": payload.get("answer", ""),
        "explanation": payload.get("explanation", ""),
    }


@router.get("/wrong-overview")
def wrong_overview(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """跨方案错题总览（iter 156）：每方案当前错题数 + 合计（Wiki 页全局错题卡）。

    判定与 GET /plans/{id}/quiz/wrong 同口径：每题最新作答 correct IS False。
    无错题 -> items=[] / total=0（前端不显示卡片）。
    """
    from app.api.plans import _load_plan_content

    attempts = (
        db.query(models.QuizAttempt)
        .join(models.Quiz, models.QuizAttempt.quiz_id == models.Quiz.id)
        .join(models.Plan, models.Quiz.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id)
        .order_by(
            models.QuizAttempt.quiz_id,
            models.QuizAttempt.created_at.desc(),
            models.QuizAttempt.id.desc(),
        )
        .all()
    )
    latest_by_quiz: dict[int, models.QuizAttempt] = {}
    for a in attempts:
        latest_by_quiz.setdefault(a.quiz_id, a)
    wrong_ids = [qid for qid, a in latest_by_quiz.items() if a.correct is False]
    if not wrong_ids:
        return {"items": [], "total": 0}

    per_plan: dict[int, int] = {}
    for q in db.query(models.Quiz).filter(models.Quiz.id.in_(wrong_ids)).all():
        per_plan[q.plan_id] = per_plan.get(q.plan_id, 0) + 1

    topics: dict[int, str] = {}
    for pid in per_plan:
        plan = db.get(models.Plan, pid)
        if plan is None:
            continue
        content = _load_plan_content(plan)
        topics[pid] = str((content.get("intent") or {}).get("topic") or content.get("title") or "")

    items = [
        {"plan_id": pid, "topic": topics.get(pid, ""), "wrong_count": cnt}
        for pid, cnt in sorted(per_plan.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return {"items": items, "total": sum(per_plan.values())}

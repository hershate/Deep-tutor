"""quizzes API（plan/03 §一 POST /quizzes/{id}/submit，P2 iter 42）。

提交作答：记录 quiz_attempts 行并判分（choice 服务器比对 payload.answer；
fill/short 自评题 correct=None）。进度掌握度回写仍走 POST /progress（前端聚合），
两端点解耦（submit 只管作答记录与判分）。

GET /quizzes/wrong-overview（iter 156）：跨方案错题总览——每方案当前错题数（Wiki 全局视图）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth
from app.api.errors import ApiError
from app.core.ratelimit import limiter
from app.db import models
from app.db.models import User

router = APIRouter(prefix="/quizzes", tags=["quizzes"], dependencies=[Depends(require_auth)])


def latest_attempts_by_quiz(db: Session, base) -> dict[int, tuple[int, bool | None]]:
    """每题最新 attempt（created_at 倒序、同刻 id 大者优先）-> {quiz_id: (attempt_id, correct)}。

    iter 372 R12：错题族三端点此前把**全部** attempt 物化为 ORM 对象再 dict 累积——
    quiz_attempts 是全库增速最快表（每次提交一行，永不清理），用户级查询随历史作答
    线性劣化（万级作答 = 每次打开复习页载入万级完整 ORM 对象）。窗口函数下推后
    SQL 引擎内分组取首，仅物化 ≤题数 行。排序语义与旧 dict 累积逐字一致。
    """
    rn = func.row_number().over(
        partition_by=models.QuizAttempt.quiz_id,
        order_by=(models.QuizAttempt.created_at.desc(), models.QuizAttempt.id.desc()),
    ).label("rn")
    sub = base.with_entities(
        models.QuizAttempt.id.label("aid"),
        models.QuizAttempt.quiz_id.label("qid"),
        models.QuizAttempt.correct.label("correct"),
        rn,
    ).subquery()
    rows = db.query(sub.c.qid, sub.c.aid, sub.c.correct).filter(sub.c.rn == 1).all()
    return {qid: (aid, correct) for qid, aid, correct in rows}

# 提交限流（err.md A23：此前可无限刷提交记录，quiz_attempts 行无限增长）
_SUBMIT_RATE_PER_MIN = 60
# 错题族读限流（iter 395）：两端点每次都要对**本用户全部**作答历史跑窗口函数
# （latest_attempts_by_quiz 按 quiz_id 分组取首）——历史越久越慢，且此前无限流，
# 单客户端可连发打满 DB CPU。30/min 远宽于前端翻页/复习页轮询。
_WRONG_READ_RATE_PER_MIN = 30
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
    # iter 364：限流前置——防恶意用不存在的 quiz_id 刷 404 绕过限流桶（每次仍触发主键查询）
    if not limiter.check(f"quiz_submit:{user.id}", _SUBMIT_RATE_PER_MIN):
        raise ApiError("rate_limited", "提交过于频繁，请稍后再试", status=429)
    quiz = db.get(models.Quiz, quiz_id)
    # P4 隔离：他人方案的题 404（不泄露存在性）
    if quiz is None or quiz.plan is None or quiz.plan.session is None or quiz.plan.session.user_id != user.id:
        raise ApiError("not_found", f"测验题 {quiz_id} 不存在")
    payload = quiz.payload or {}
    # choice 服务器判分；fill/short 自评（correct=None）
    correct: bool | None = None
    if quiz.type == models.QuizType.choice:
        # payload 缺/空 answer（异常/降级数据）时判 None——否则 None==None 或 ""==""（空选）
        # 均会误判「答对」，空提交即可刷掌握度（iter 368：空串 answer 同样视为不可判分）
        answer = payload.get("answer")
        if answer:
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

    if not limiter.check(f"wrong_read:{user.id}", _WRONG_READ_RATE_PER_MIN):
        raise ApiError("rate_limited", "请求过于频繁，请稍后再试", status=429)
    latest = latest_attempts_by_quiz(
        db,
        db.query(models.QuizAttempt)
        .join(models.Quiz, models.QuizAttempt.quiz_id == models.Quiz.id)
        .join(models.Plan, models.Quiz.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id),
    )
    wrong_ids = [qid for qid, (_aid, correct) in latest.items() if correct is False]
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


@router.get("/wrong-queue")
def wrong_queue(
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """错题复习队列（iter 285，/review 翻卡复习页数据源）：跨方案最新作答判错的题（choice），
    返回完整 payload 供重答判分。判分口径同 GET /plans/{id}/quiz/wrong 与 /wrong-overview
    （每题最新 attempt correct IS False）。

    fill/short 自评题 correct 恒为 None（submit 不判分），不进错题集——故本队列天然仅含 choice，
    无自评歧义（重答走 /quizzes/{id}/submit 服务器判分）。
    排序：最近判错的在前（按最新 attempt 的 id 倒序，id 单调递增即时间序代理）；
    total 为全部错题数（截断前，与 /wrong-overview 同口径），items 截断 limit（≤200）。
    """
    from app.api.plans import _load_plan_content

    if not limiter.check(f"wrong_read:{user.id}", _WRONG_READ_RATE_PER_MIN):
        raise ApiError("rate_limited", "请求过于频繁，请稍后再试", status=429)
    latest = latest_attempts_by_quiz(
        db,
        db.query(models.QuizAttempt)
        .join(models.Quiz, models.QuizAttempt.quiz_id == models.Quiz.id)
        .join(models.Plan, models.Quiz.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id),
    )
    # iter 368：按最新 attempt id 倒序**后再**截断——此前 dict 插入序（quiz_id 升序）
    # 截断，limit 外的最近判错被无差别丢弃，与文档「最近判错在前」相悖
    wrong_pairs = sorted(
        ((aid, qid) for qid, (aid, correct) in latest.items() if correct is False),
        reverse=True,
    )
    wrong_ids = [qid for _aid, qid in wrong_pairs]
    if not wrong_ids:
        return {"items": [], "total": 0}
    total = len(wrong_ids)
    capped = wrong_ids[:limit]
    quizzes = db.query(models.Quiz).filter(models.Quiz.id.in_(capped)).all()
    order = {qid: latest[qid][0] for qid in capped}  # 最近判错在前
    quizzes.sort(key=lambda q: order.get(q.id, 0), reverse=True)
    topics: dict[int, str] = {}
    items = []
    for q in quizzes:
        if q.plan_id not in topics:
            plan = db.get(models.Plan, q.plan_id)
            content = _load_plan_content(plan) if plan is not None else {}
            topics[q.plan_id] = str((content.get("intent") or {}).get("topic") or content.get("title") or "")
        payload = q.payload or {}
        items.append(
            {
                "id": q.id,
                "plan_id": q.plan_id,
                "plan_topic": topics[q.plan_id],
                "type": q.type.value if q.type is not None else "choice",
                "question": payload.get("question", ""),
                "options": payload.get("options", []) or [],
                "answer": payload.get("answer", ""),
                "explanation": payload.get("explanation", ""),
            }
        )
    return {"items": items, "total": total}

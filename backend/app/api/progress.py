"""进度 API（plan/03 §一 progress，P2 iter 39）。

POST /api/progress           { plan_id, node_id, status, quiz_score? } -> 更新进度（覆盖写，幂等）
GET  /api/progress/{plan_id} -> 进度总览（nodes + mastery_avg）

掌握度/复习间隔计算在 app/tools/record_progress.py（纯 Python 确定性，plan/02 §9），
本层只做 DB upsert。node_id 为 plan 内容 knowledge_map 的字符串 id（knowledge_nodes.sid）。
plan/02 §9 errors：节点不存在 -> 忽略并返回 warning（不报错）。
next_review 不入库：由 mastery + updated_at（UTC，日粒度）即时推导（SM-2-lite，见
record_progress docstring）；非 UTC 时区在日界最多差 1 天，对 1-14 天复习间隔可接受。
"""

from __future__ import annotations

from datetime import date as dt_date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_owned_plan, require_auth
from app.api.errors import ApiError
from app.db import models
from app.db.models import User
from app.tools.record_progress import compute_mastery, next_review_date

router = APIRouter(prefix="/progress", tags=["progress"], dependencies=[Depends(require_auth)])


class ProgressUpdate(BaseModel):
    plan_id: int
    node_id: str = Field(min_length=1, max_length=64)  # knowledge_nodes.sid
    status: models.ProgressStatus
    quiz_score: float | None = Field(default=None, ge=0.0, le=1.0)  # 本次答题正确率 0-1


def _node_view(node: models.KnowledgeNode, row: models.Progress | None) -> dict:
    """节点进度视图：无 progress 行 -> not_started/0.0/无复习日。"""
    if row is None:
        return {
            "node_id": node.sid,
            "name": node.name,
            "status": models.ProgressStatus.not_started.value,
            "mastery": 0.0,
            "next_review": None,
        }
    mastery = round(row.mastery, 3)
    return {
        "node_id": node.sid,
        "name": node.name,
        "status": row.status.value,
        "mastery": mastery,
        "next_review": next_review_date(mastery, today=row.updated_at.date()).isoformat(),
    }


@router.post("")
def update_progress(
    body: ProgressUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """更新单节点进度（覆盖写，幂等）：掌握度加权 + 间隔重复（record_progress）。"""
    get_owned_plan(db, body.plan_id, user)  # P4 隔离：他人方案 404
    node = (
        db.query(models.KnowledgeNode)
        .filter_by(plan_id=body.plan_id, sid=body.node_id)
        .one_or_none()
    )
    if node is None:
        # plan/02 §9 errors：节点不存在 -> 忽略并记录 warning
        return {"updated": False, "warning": f"node_not_found: {body.node_id}"}

    row = (
        db.query(models.Progress)
        .filter_by(plan_id=body.plan_id, node_id=node.id)
        .one_or_none()
    )
    if row is not None:
        mastery = compute_mastery(row.mastery, body.status.value, body.quiz_score)
        row.status = body.status
        row.mastery = mastery
        db.commit()
    else:
        row = models.Progress(
            plan_id=body.plan_id, node_id=node.id, status=body.status,
            mastery=compute_mastery(0.0, body.status.value, body.quiz_score),
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            # err.md A11：并发请求先插了同 (plan_id, node_id) 行（唯一约束兜底）-> 转为更新
            db.rollback()
            row = (
                db.query(models.Progress)
                .filter_by(plan_id=body.plan_id, node_id=node.id)
                .one()
            )
            row.status = body.status
            row.mastery = compute_mastery(row.mastery, body.status.value, body.quiz_score)
            db.commit()
    db.refresh(row)
    mastery = row.mastery
    # 后端插件事件（API.md「后端插件 API」§事件清单）：监听器异常隔离，不影响响应
    from app.plugins import bus as plugin_bus

    plugin_bus.emit(
        "progress_updated",
        {
            "user_id": user.id,
            "plan_id": body.plan_id,
            "node_id": node.sid,
            "status": row.status.value,
            "mastery": round(mastery, 3),
        },
    )
    return {
        "updated": True,
        "node_id": node.sid,
        "status": row.status.value,
        "mastery": round(mastery, 3),
        "next_review": next_review_date(mastery, today=row.updated_at.date()).isoformat(),
    }


@router.get("/{plan_id}")
def get_progress(
    plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """进度总览（plan/03 §一）：全部 knowledge_nodes 合并 progress 行 + mastery_avg。"""
    get_owned_plan(db, plan_id, user)  # P4 隔离：他人方案 404
    nodes = db.query(models.KnowledgeNode).filter_by(plan_id=plan_id).all()
    rows = {r.node_id: r for r in db.query(models.Progress).filter_by(plan_id=plan_id).all()}
    views = [_node_view(n, rows.get(n.id)) for n in nodes]
    mastery_avg = round(sum(v["mastery"] for v in views) / len(views), 3) if views else 0.0
    return {"plan_id": plan_id, "nodes": views, "mastery_avg": mastery_avg}


# --- 全局今日待复习（iter 119）：跨方案聚合到期节点，Wiki 页顶部提醒 ---


@router.get("/review/due")
def review_due(
    limit: int = 100,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """当前用户全部方案的到期复习节点（next_review <= 今天），按复习日升序（最逾期在前）。

    next_review 同 _node_view 语义：mastery + updated_at 即时推导（SM-2-lite），不入库。
    """
    from app.api.plans import _load_plan_content

    rows = (
        db.query(models.Progress, models.KnowledgeNode)
        .join(models.KnowledgeNode, models.Progress.node_id == models.KnowledgeNode.id)
        .join(models.Plan, models.Progress.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id)
        .all()
    )
    topics: dict[int, str] = {}
    today = dt_date.today()
    items = []
    for prog, node in rows:
        due = next_review_date(round(prog.mastery, 3), today=prog.updated_at.date())
        if due > today:
            continue
        if node.plan_id not in topics:
            plan = db.get(models.Plan, node.plan_id)
            content = _load_plan_content(plan) if plan is not None else {}
            topics[node.plan_id] = str(
                (content.get("intent") or {}).get("topic") or content.get("title") or ""
            )
        items.append(
            {
                "plan_id": node.plan_id,
                "plan_topic": topics[node.plan_id],
                "node_id": node.sid,
                "name": node.name,
                "status": prog.status.value,
                "mastery": round(prog.mastery, 3),
                "next_review": due.isoformat(),
            }
        )
    items.sort(key=lambda i: (i["next_review"], i["mastery"]))
    return {"items": items[: max(1, min(limit, 500))], "total": len(items)}


# --- 全局学习统计（iter 122）：跨方案累计/本周分钟数 + 连续打卡天数 ---
# iter 139：/stats/calendar 近 N 日按日分钟数（Wiki 打卡热力图，无打卡日补 0）


@router.get("/stats/summary")
def stats_summary(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """学习统计：累计/近 7 日分钟数、连续打卡天数（streak）。

    streak 语义：有打卡的日期倒序连成链，链尾须为今天或昨天（今天还没打卡不清零）。
    日界按服务器本地日（与 LearningLogIn.date 默认值同口径）。
    """
    from datetime import timedelta

    rows = (
        db.query(models.LearningLog)
        .join(models.Plan, models.LearningLog.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id)
        .all()
    )
    today = dt_date.today()
    week_start = today - timedelta(days=6)  # 含今天共 7 天
    total_minutes = sum(r.minutes for r in rows)
    week_minutes = sum(r.minutes for r in rows if r.date >= week_start)

    days = sorted({r.date for r in rows}, reverse=True)
    streak = 0
    if days and days[0] >= today - timedelta(days=1):
        expected = days[0]
        for d in days:
            if d != expected:
                break
            streak += 1
            expected -= timedelta(days=1)

    return {
        "total_minutes": total_minutes,
        "week_minutes": week_minutes,
        "streak_days": streak,
        "total_logs": len(rows),
    }


@router.get("/stats/calendar")
def stats_calendar(
    days: int = Query(default=84, ge=7, le=366),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """学习打卡日历（iter 139，Wiki 热力图）：近 N 日按日学习分钟数，无打卡日补 0。

    SQL 聚合（同日跨方案多条打卡求和）；日界按服务器本地日（与 stats/summary 同口径）。
    """
    from datetime import timedelta

    start = dt_date.today() - timedelta(days=days - 1)
    rows = (
        db.query(
            models.LearningLog.date,
            func.coalesce(func.sum(models.LearningLog.minutes), 0),
        )
        .join(models.Plan, models.LearningLog.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id, models.LearningLog.date >= start)
        .group_by(models.LearningLog.date)
        .all()
    )
    by_date = {r[0]: int(r[1]) for r in rows}
    items = [
        {"date": (start + timedelta(days=i)).isoformat(), "minutes": by_date.get(start + timedelta(days=i), 0)}
        for i in range(days)
    ]
    return {"items": items}


# --- learning_logs 打卡（plan/01 §四 learning_logs，P2 iter 45；学习中心页用）---


class LearningLogIn(BaseModel):
    plan_id: int
    minutes: int = Field(ge=1, le=1440)  # 学习分钟数（err.md D14 对齐前端：0 分钟无意义且污染统计）
    note: str | None = Field(default=None, max_length=2000)
    date: dt_date | None = None  # 默认今天（本地单用户，服务器本地日即用户本地日）


@router.post("/logs")
def add_learning_log(
    body: LearningLogIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """打卡：记一条学习日志（plan_id, date, minutes, note）。"""
    get_owned_plan(db, body.plan_id, user)  # P4 隔离：他人方案 404
    row = models.LearningLog(
        plan_id=body.plan_id,
        date=body.date or dt_date.today(),
        minutes=body.minutes,
        note=body.note,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    # 后端插件事件（API.md §8.5）：学习打卡（监听器异常隔离，不影响响应）
    from app.plugins import bus as plugin_bus

    plugin_bus.emit(
        "learning_log_added",
        {
            "user_id": user.id,
            "plan_id": body.plan_id,
            "log_id": row.id,
            "date": row.date.isoformat(),
            "minutes": row.minutes,
            "note": row.note,
        },
    )
    return {"id": row.id, "date": row.date.isoformat(), "minutes": row.minutes, "note": row.note}


@router.get("/logs/{plan_id}")
def list_learning_logs(
    plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """学习日志列表（日期倒序，上限 100 条）+ 累计分钟数。"""
    get_owned_plan(db, plan_id, user)  # P4 隔离：他人方案 404
    rows = (
        db.query(models.LearningLog)
        .filter_by(plan_id=plan_id)
        .order_by(models.LearningLog.date.desc(), models.LearningLog.id.desc())
        .limit(100)
        .all()
    )
    total = (
        db.query(func.coalesce(func.sum(models.LearningLog.minutes), 0))
        .filter_by(plan_id=plan_id)
        .scalar()
    )
    return {
        "logs": [
            {"id": r.id, "date": r.date.isoformat(), "minutes": r.minutes, "note": r.note}
            for r in rows
        ],
        "total_minutes": int(total or 0),
    }


@router.delete("/logs/{log_id}")
def delete_learning_log(
    log_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """删除一条学习日志（iter 150：打错卡可撤回）。

    P4 隔离：经 Plan->Session 校验归属，他人日志 404（不泄露存在性）。
    """
    row = (
        db.query(models.LearningLog)
        .join(models.Plan, models.LearningLog.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.LearningLog.id == log_id, models.Session.user_id == user.id)
        .one_or_none()
    )
    if row is None:
        raise ApiError("not_found", "学习日志不存在")
    db.delete(row)
    db.commit()
    return {"deleted": True, "id": log_id}

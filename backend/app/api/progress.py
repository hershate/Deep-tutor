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

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_owned_plan, require_auth
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
    old = row.mastery if row is not None else 0.0
    mastery = compute_mastery(old, body.status.value, body.quiz_score)
    if row is None:
        row = models.Progress(
            plan_id=body.plan_id, node_id=node.id, status=body.status, mastery=mastery
        )
        db.add(row)
    else:
        row.status = body.status
        row.mastery = mastery
    db.commit()
    db.refresh(row)
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


# --- learning_logs 打卡（plan/01 §四 learning_logs，P2 iter 45；学习中心页用）---


class LearningLogIn(BaseModel):
    plan_id: int
    minutes: int = Field(ge=0, le=1440)  # 学习分钟数（上限取一天 1440）
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

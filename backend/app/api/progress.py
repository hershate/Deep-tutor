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

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_auth
from app.api.errors import ApiError
from app.db import models
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
def update_progress(body: ProgressUpdate, db: Session = Depends(get_db)) -> dict:
    """更新单节点进度（覆盖写，幂等）：掌握度加权 + 间隔重复（record_progress）。"""
    if db.get(models.Plan, body.plan_id) is None:
        raise ApiError("not_found", f"方案 {body.plan_id} 不存在")
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
def get_progress(plan_id: int, db: Session = Depends(get_db)) -> dict:
    """进度总览（plan/03 §一）：全部 knowledge_nodes 合并 progress 行 + mastery_avg。"""
    if db.get(models.Plan, plan_id) is None:
        raise ApiError("not_found", f"方案 {plan_id} 不存在")
    nodes = db.query(models.KnowledgeNode).filter_by(plan_id=plan_id).all()
    rows = {r.node_id: r for r in db.query(models.Progress).filter_by(plan_id=plan_id).all()}
    views = [_node_view(n, rows.get(n.id)) for n in nodes]
    mastery_avg = round(sum(v["mastery"] for v in views) / len(views), 3) if views else 0.0
    return {"plan_id": plan_id, "nodes": views, "mastery_avg": mastery_avg}

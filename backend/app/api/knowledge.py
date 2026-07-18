"""个人知识库聚合端点（iter 100，用户需求：个人 LLM wiki）：

跨方案聚合当前用户的知识节点，附学习状态/掌握度，供 Wiki 页浏览检索。
只读；节点由生成管线落库，无独立 CRUD。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth
from app.db import models
from app.db.models import User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(require_auth)])

_VALID_STATUS = {"not_started", "learning", "done"}


@router.get("")
def list_knowledge(
    q: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default=None),
    plan_id: int | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """当前用户全部方案的知识节点（新方案在前），含进度状态。

    - q：节点名子串过滤（大小写不敏感由 SQLite LIKE 语义保证）
    - status：not_started（含无进度行）/ learning / done
    - 截断：limit 防重度用户一次拉爆（前端提示总数）
    """
    from app.api.plans import _load_plan_content

    query = (
        db.query(models.KnowledgeNode)
        .join(models.Plan, models.KnowledgeNode.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id)
    )
    if plan_id is not None:
        query = query.filter(models.KnowledgeNode.plan_id == plan_id)
    if q and q.strip():
        query = query.filter(models.KnowledgeNode.name.contains(q.strip()))
    nodes = (
        query.order_by(models.KnowledgeNode.plan_id.desc(), models.KnowledgeNode.id)
        .limit(limit)
        .all()
    )

    # 进度映射：(plan_id, node_id) -> Progress（仅本批节点）
    keys = {(n.plan_id, n.id) for n in nodes}
    progress_map: dict[tuple[int, int], models.Progress] = {}
    if keys:
        plan_ids = {k[0] for k in keys}
        node_ids = {k[1] for k in keys}
        for p in (
            db.query(models.Progress)
            .filter(models.Progress.plan_id.in_(plan_ids), models.Progress.node_id.in_(node_ids))
            .all()
        ):
            if (p.plan_id, p.node_id) in keys:
                progress_map[(p.plan_id, p.node_id)] = p

    # 方案标题（intent.topic；大方案 content_path 回读，同 _plan_topic 语义）
    topics: dict[int, str] = {}
    for pid in {n.plan_id for n in nodes}:
        plan = db.get(models.Plan, pid)
        if plan is None:
            continue
        content = _load_plan_content(plan)
        topics[pid] = str((content.get("intent") or {}).get("topic") or content.get("title") or "")

    items = []
    stats = {"not_started": 0, "learning": 0, "done": 0}
    for n in nodes:
        p = progress_map.get((n.plan_id, n.id))
        st = p.status.value if p else "not_started"
        stats[st] += 1
        items.append(
            {
                "node_id": n.id,
                "sid": n.sid,
                "name": n.name,
                "difficulty": n.difficulty,
                "must_learn": bool(n.must_learn),
                "plan_id": n.plan_id,
                "plan_topic": topics.get(n.plan_id, ""),
                "status": st,
                "mastery": float(p.mastery) if p else 0.0,
            }
        )

    if status in _VALID_STATUS:
        items = [i for i in items if i["status"] == status]

    return {"items": items, "total": len(items), "stats": stats}

"""个人知识库聚合端点（iter 100，用户需求：个人 LLM wiki）：

跨方案聚合当前用户的知识节点，附学习状态/掌握度，供 Wiki 页浏览检索。
iter 105 增强：知识点笔记（node_notes）——GET/PUT 单节点笔记 + 列表 has_note 标记。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth
from app.api.errors import ApiError
from app.db import models
from app.db.models import User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(require_auth)])

_VALID_STATUS = {"not_started", "learning", "done"}

# 笔记内容上限（err.md A5 同款思路：防无界 Text 写入）
_MAX_NOTE_CHARS = 20_000


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

    # 笔记标记：本批节点中当前用户有笔记的 node_id 集（Wiki 显示 📝）
    noted: set[int] = set()
    if nodes:
        noted = {
            row.node_id
            for row in db.query(models.NodeNote.node_id)
            .filter(
                models.NodeNote.user_id == user.id,
                models.NodeNote.node_id.in_({n.id for n in nodes}),
            )
            .all()
        }

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
                "has_note": n.id in noted,
            }
        )

    if status in _VALID_STATUS:
        items = [i for i in items if i["status"] == status]

    return {"items": items, "total": len(items), "stats": stats}


# --- 知识点笔记（iter 105）：每用户每节点一条，空内容保存即删除 ---


def _owned_node_or_404(db: Session, node_id: int, user: User) -> models.KnowledgeNode:
    """当前用户拥有的知识节点（node -> plan -> session.user_id；不存在/属他人 -> 404）。"""
    node = db.get(models.KnowledgeNode, node_id)
    if node is None or node.plan is None or node.plan.session is None or node.plan.session.user_id != user.id:
        raise ApiError("not_found", f"知识节点 {node_id} 不存在")
    return node


def _note_out(note: models.NodeNote | None, node_id: int) -> dict:
    return {
        "node_id": node_id,
        "content": note.content if note else "",
        "updated_at": note.updated_at.isoformat() if note and note.updated_at else None,
    }


@router.get("/notes/{node_id}")
def get_node_note(
    node_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """读单节点笔记；无笔记返回空内容（不 404，前端编辑体验一致）。"""
    _owned_node_or_404(db, node_id, user)
    note = db.query(models.NodeNote).filter_by(user_id=user.id, node_id=node_id).first()
    return _note_out(note, node_id)


class NoteIn(BaseModel):
    content: str = Field(default="", max_length=_MAX_NOTE_CHARS)


@router.put("/notes/{node_id}")
def put_node_note(
    node_id: int,
    body: NoteIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """写单节点笔记（upsert）：内容去空白后为空 -> 删行（空笔记不留库）。

    并发：先查后插竞态由 uq_node_notes_user_node 唯一约束兜底（同 progress A11 模式），
    撞约束时回滚后按更新重放。
    """
    _owned_node_or_404(db, node_id, user)
    content = body.content.strip()
    note = db.query(models.NodeNote).filter_by(user_id=user.id, node_id=node_id).first()
    if not content:
        if note is not None:
            db.delete(note)
            db.commit()
        return _note_out(None, node_id)
    if note is None:
        note = models.NodeNote(user_id=user.id, node_id=node_id, content=content)
        db.add(note)
        try:
            db.commit()
        except Exception:
            # 并发双写撞唯一约束：回滚后按更新重放（已存在即更新内容）
            db.rollback()
            note = db.query(models.NodeNote).filter_by(user_id=user.id, node_id=node_id).first()
            if note is None:
                raise
            note.content = content
            db.commit()
    else:
        note.content = content
        db.commit()
    db.refresh(note)
    return _note_out(note, node_id)

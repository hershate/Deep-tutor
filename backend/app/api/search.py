"""全局统一搜索（iter 249）：跨方案/笔记/知识库文档/知识节点的单入口子串搜索。

背景：plans / knowledge / kb 各域列表已各有 ?q= 子串过滤（iter 128/148 等），但分散在
4 个页面——用户须先记得内容归属哪一类才能检索。本端点把 4 个域聚合为一个入口，
供前端搜索页/命令面板共用；匹配口径与各域内搜索一致（.contains 子串、参数化、用户隔离）。

设计约束：
- 全部查询按当前用户过滤（plans/nodes 经 Session join 取归属），他人数据不进结果与计数；
- q 必填；空白 q 直接返回空分组（不触发全表扫描，防误触全量列举）；
- 每域独立 count + items 截断（limit 缺省 5、≤20），8 条有界短查询；
- 每用户 30/min 限流（LIKE 子串逐行扫，防刷接口拖库）；
- 无 LLM、无上传/写盘；仅节点 plan_topic 可能回读溢出方案文件（≤limit 条，同 knowledge 口径）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth
from app.api.errors import ApiError
from app.api.kb import content_search_docs
from app.api.plans import _plan_topic
from app.core.ratelimit import limiter
from app.db import models
from app.db.models import User

router = APIRouter(prefix="/search", tags=["search"], dependencies=[Depends(require_auth)])

_SEARCH_RATE_PER_MIN = 30
_SNIPPET_WIDTH = 120


def _snippet(text: str, qs: str, width: int = _SNIPPET_WIDTH) -> str:
    """围绕首个命中（大小写不敏感）截取上下文窗口；未命中取前缀；省略号标记截断。"""
    if not text:
        return ""
    if len(text) <= width:
        return text
    idx = text.lower().find(qs.lower()) if qs else -1
    if idx < 0:
        return text[:width] + "…"
    start = max(0, idx - width // 3)
    end = min(len(text), start + width)
    start = max(0, end - width)  # 尾部触顶时向前补齐，保持窗口长度
    return ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")


@router.get("")
def global_search(
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    if not limiter.check(f"search:{user.id}", _SEARCH_RATE_PER_MIN):
        raise ApiError("rate_limited", f"搜索限每分钟 {_SEARCH_RATE_PER_MIN} 次，请稍后再试")
    qs = q.strip()
    if not qs:
        empty = {"total": 0, "items": []}
        return {"q": "", "plans": dict(empty), "notes": dict(empty), "kb_docs": dict(empty), "nodes": dict(empty)}

    # 方案：intent.topic 子串（同 GET /plans?q= 口径；溢出落盘大方案 content={} -> NULL
    # 不匹配，与列表搜索同一已知限制）
    plans_q = (
        db.query(models.Plan)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id)
        .filter(models.Plan.content["intent"]["topic"].as_string().contains(qs))
    )
    plan_rows = plans_q.order_by(models.Plan.created_at.desc()).limit(limit).all()

    # 独立笔记：标题或正文子串（notes 列表端点本身无 q，此处首创标题+正文双匹配）
    notes_q = (
        db.query(models.Note)
        .filter(models.Note.user_id == user.id)
        .filter(or_(models.Note.title.contains(qs), models.Note.content.contains(qs)))
    )
    note_rows = notes_q.order_by(models.Note.updated_at.desc()).limit(limit).all()

    # 知识库文档：标题子串 + 解析文本内容（iter 255 扩展——有界扫描最近 N 篇已解析
    # 文档，content_search_docs 同 GET /kb/documents?q= 口径；内容命中带命中片段）
    kb_q = (
        db.query(models.KbDocument)
        .filter(models.KbDocument.user_id == user.id)
        .filter(models.KbDocument.title.contains(qs))
    )
    kb_rows = kb_q.order_by(models.KbDocument.id.desc()).limit(limit).all()
    kb_content_hits = content_search_docs(db, user.id, qs, limit - len(kb_rows), {d.id for d in kb_rows})

    # 知识节点：名称子串（join Plan->Session 取归属，同 GET /knowledge 口径）
    nodes_q = (
        db.query(models.KnowledgeNode)
        .join(models.Plan, models.KnowledgeNode.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id)
        .filter(models.KnowledgeNode.name.contains(qs))
    )
    node_rows = nodes_q.order_by(models.KnowledgeNode.plan_id.desc(), models.KnowledgeNode.id).limit(limit).all()

    # 节点所属方案主题（仅本批 ≤limit 条；溢出方案文件回读同 knowledge 列表口径）
    topics: dict[int, str] = {}
    for pid in {n.plan_id for n in node_rows}:
        plan = db.get(models.Plan, pid)
        if plan is not None:
            topics[pid] = _plan_topic(plan)

    return {
        "q": qs,
        "plans": {
            "total": plans_q.count(),
            "items": [
                {
                    "id": p.id,
                    "session_id": p.session_id,
                    "version": p.version,
                    "topic": _plan_topic(p),
                    "created_at": str(p.created_at),
                }
                for p in plan_rows
            ],
        },
        "notes": {
            "total": notes_q.count(),
            "items": [
                {
                    "id": n.id,
                    "title": n.title,
                    "snippet": _snippet(n.content, qs),
                    "updated_at": str(n.updated_at),
                }
                for n in note_rows
            ],
        },
        "kb_docs": {
            # total = 标题精确计数 + 内容命中数（内容仅覆盖扫描范围，iter 255 诚实边界）
            "total": kb_q.count() + len(kb_content_hits),
            "items": [
                {
                    "id": d.id,
                    "title": d.title,
                    "kind": d.kind.value if d.kind else None,
                    "created_at": str(d.created_at),
                    "match": "title",
                    "snippet": None,
                }
                for d in kb_rows
            ]
            + [
                {
                    "id": d.id,
                    "title": d.title,
                    "kind": d.kind.value if d.kind else None,
                    "created_at": str(d.created_at),
                    "match": "content",
                    "snippet": snip,
                }
                for d, snip in kb_content_hits
            ],
        },
        "nodes": {
            "total": nodes_q.count(),
            "items": [
                {
                    "id": n.id,
                    "plan_id": n.plan_id,
                    "name": n.name,
                    "plan_topic": topics.get(n.plan_id, ""),
                }
                for n in node_rows
            ],
        },
    }

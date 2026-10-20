"""个人知识库聚合端点（iter 100，用户需求：个人 LLM wiki）：

跨方案聚合当前用户的知识节点，附学习状态/掌握度，供 Wiki 页浏览检索。
iter 105 增强：知识点笔记（node_notes）——GET/PUT 单节点笔记 + 列表 has_note 标记。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError

from app.api.deps import get_current_user, get_db, require_auth
from app.api.errors import ApiError
from app.core.ratelimit import limiter
from app.db import models
from app.db.models import User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(require_auth)])

_VALID_STATUS = {"not_started", "learning", "done"}

# 笔记内容上限（err.md A5 同款思路：防无界 Text 写入）
_MAX_NOTE_CHARS = 20_000

# iter 368：读路径限流（聚合端点逐方案读 plan JSON 文件，重度用户秒级连点可打满文件 IO；
# 30/min 仍宽于前端轮询与正常翻页）；导出为整页 Markdown 渲染（CPU 密集）10/min
_KNOWLEDGE_LIST_PER_MIN = 30
_KNOWLEDGE_EXPORT_PER_MIN = 10
_NODE_NOTE_WRITE_PER_MIN = 60


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

    - q：节点名**或本人笔记内容**子串过滤（iter 148 扩展笔记内容；大小写不敏感由 SQLite LIKE 语义保证）
    - status：not_started（含无进度行）/ learning / done
    - 截断：limit 防重度用户一次拉爆（前端提示总数）

    iter 395：**status 过滤下推到 SQL**（此前在 LIMIT 之后于 Python 侧过滤——
    节点数 > limit 的用户按状态筛选只会看到「最新 limit 个节点」里的匹配项，结果
    严重偏少甚至为空，chip 计数同样失真）。stats 改为按**全量匹配节点**统计
    （此前统计的是 LIMIT 窗口内的批次）。total 语义不变（= 本页返回条数）。
    """
    from app.api.plans import _load_plan_content

    if not limiter.check(f"knowledge_list:{user.id}", _KNOWLEDGE_LIST_PER_MIN):
        raise ApiError("rate_limited", "请求过于频繁，请稍后再试", status=429)

    def _base_query():
        """用户可见节点的基础查询（q / plan_id 过滤；不含 status 与 limit）。"""
        query = (
            db.query(models.KnowledgeNode)
            .join(models.Plan, models.KnowledgeNode.plan_id == models.Plan.id)
            .join(models.Session, models.Plan.session_id == models.Session.id)
            .filter(models.Session.user_id == user.id)
        )
        if plan_id is not None:
            query = query.filter(models.KnowledgeNode.plan_id == plan_id)
        if q and q.strip():
            qs = q.strip()
            # iter 148：名或笔记内容匹配。outerjoin 不复制行——uq_node_notes_user_node 保证每节点至多一条
            query = query.outerjoin(
                models.NodeNote,
                and_(
                    models.NodeNote.node_id == models.KnowledgeNode.id,
                    models.NodeNote.user_id == user.id,
                ),
            ).filter(
                or_(
                    models.KnowledgeNode.name.contains(qs),
                    models.NodeNote.content.contains(qs),
                )
            )
        return query

    # 进度行与节点的关联（Progress 对 (plan_id, node_id) 唯一 -> 不产生行放大）
    progress_join = and_(
        models.Progress.plan_id == models.KnowledgeNode.plan_id,
        models.Progress.node_id == models.KnowledgeNode.id,
    )
    query = _base_query().outerjoin(models.Progress, progress_join)
    if status in _VALID_STATUS:
        if status == "not_started":
            # 「未开始」= 无进度行 或 进度行状态为 not_started（同 Python 侧口径）
            query = query.filter(
                or_(
                    models.Progress.id.is_(None),
                    models.Progress.status == models.ProgressStatus.not_started,
                )
            )
        else:
            query = query.filter(models.Progress.status == status)
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
    for n in nodes:
        p = progress_map.get((n.plan_id, n.id))
        st = p.status.value if p else "not_started"
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

    # iter 395：状态统计改由 SQL 按**全量匹配节点**分组计数（此前统计的是 LIMIT
    # 窗口内的批次——重度用户的 chip 计数与真实值不符）。无进度行归入 not_started。
    stats = {"not_started": 0, "learning": 0, "done": 0}
    for st, cnt in (
        _base_query()
        .outerjoin(models.Progress, progress_join)
        .with_entities(models.Progress.status, func.count(models.KnowledgeNode.id))
        .group_by(models.Progress.status)
        .all()
    ):
        key = st.value if st is not None else "not_started"
        if key in stats:
            stats[key] = int(cnt)

    return {"items": items, "total": len(items), "stats": stats}


@router.get("/notes")
def list_notes(
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """个人笔记总览（iter 161，Wiki「笔记」视图）：当前用户全部节点笔记，更新近者在前。

    content 截断为 300 字符预览（完整内容走 GET /notes/{node_id} 按需取）。
    """
    from app.api.plans import _load_plan_content

    if not limiter.check(f"knowledge_list:{user.id}", _KNOWLEDGE_LIST_PER_MIN):
        raise ApiError("rate_limited", "请求过于频繁，请稍后再试", status=429)
    rows = (
        db.query(models.NodeNote, models.KnowledgeNode)
        .join(models.KnowledgeNode, models.NodeNote.node_id == models.KnowledgeNode.id)
        .filter(models.NodeNote.user_id == user.id, models.NodeNote.content != "")
        .order_by(models.NodeNote.updated_at.desc(), models.NodeNote.node_id)
        .limit(limit)
        .all()
    )
    topics: dict[int, str] = {}
    items = []
    for note, node in rows:
        if node.plan_id not in topics:
            plan = db.get(models.Plan, node.plan_id)
            content = _load_plan_content(plan) if plan else {}
            topics[node.plan_id] = str(
                (content.get("intent") or {}).get("topic") or content.get("title") or ""
            )
        text = note.content or ""
        items.append(
            {
                "node_id": node.id,
                "node_name": node.name,
                "plan_id": node.plan_id,
                "plan_topic": topics[node.plan_id],
                "content_preview": text[:300],
                "truncated": len(text) > 300,
                "updated_at": note.updated_at.isoformat() if note.updated_at else None,
            }
        )
    return {"items": items, "total": len(items)}


# --- 知识手册导出（iter 116）：跨方案知识节点 + 笔记 -> 单个 Markdown 下载 ---

_STATUS_LABEL = {"not_started": "未开始", "learning": "学习中", "done": "已掌握"}
_DIFFICULTY_LABEL = {1: "入门", 2: "基础", 3: "中等", 4: "进阶", 5: "挑战"}
# iter 248：导出行数截断（与列表端点 limit≤2000 口径一致）——此前全量拉取，
# 重度用户（数千节点 + 每条 ≤2 万字符笔记）单请求内存峰值数十 MB
_EXPORT_MAX_NODES = 2000


@router.get("/export")
def export_knowledge_handbook(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> Response:
    """导出个人知识手册（Markdown）：按方案分组列出全部知识节点（状态/掌握度/难度/必学），
    附用户笔记原文。下载文件名经 RFC 5987 filename*=UTF-8'' 编码（中文名）。
    """
    from app.api.plans import _load_plan_content

    if not limiter.check(f"knowledge_export:{user.id}", _KNOWLEDGE_EXPORT_PER_MIN):
        raise ApiError("rate_limited", "导出过于频繁，请稍后再试", status=429)
    nodes = (
        db.query(models.KnowledgeNode)
        .join(models.Plan, models.KnowledgeNode.plan_id == models.Plan.id)
        .join(models.Session, models.Plan.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id)
        .order_by(models.KnowledgeNode.plan_id.desc(), models.KnowledgeNode.id)
        .limit(_EXPORT_MAX_NODES + 1)  # iter 248：+1 探测截断（此前全量拉取无上限）
        .all()
    )
    truncated = len(nodes) > _EXPORT_MAX_NODES
    if truncated:
        nodes = nodes[:_EXPORT_MAX_NODES]
    progress_map: dict[tuple[int, int], models.Progress] = {}
    note_map: dict[int, str] = {}
    if nodes:
        node_ids = {n.id for n in nodes}
        plan_ids = {n.plan_id for n in nodes}
        for p in (
            db.query(models.Progress)
            .filter(models.Progress.plan_id.in_(plan_ids), models.Progress.node_id.in_(node_ids))
            .all()
        ):
            progress_map[(p.plan_id, p.node_id)] = p
        note_map = {
            row.node_id: row.content
            for row in db.query(models.NodeNote)
            .filter(models.NodeNote.user_id == user.id, models.NodeNote.node_id.in_(node_ids))
            .all()
            if row.content
        }

    topics: dict[int, str] = {}
    for pid in {n.plan_id for n in nodes}:
        plan = db.get(models.Plan, pid)
        if plan is None:
            continue
        content = _load_plan_content(plan)
        topics[pid] = str((content.get("intent") or {}).get("topic") or content.get("title") or "")

    now = datetime.now(timezone.utc).astimezone()
    lines = [
        "# 个人知识手册",
        "",
        f"> 导出于 {now:%Y-%m-%d %H:%M} · {len(nodes)} 个知识点 · {len(topics)} 个方案（Deep-tutor）",
        "",
    ]
    if truncated:
        lines += [
            f"> ⚠️ 知识点超过 {_EXPORT_MAX_NODES} 个，本手册仅含最新 {_EXPORT_MAX_NODES} 个（按方案倒序）",
            "",
        ]
    current_plan: int | None = None
    for n in nodes:
        if n.plan_id != current_plan:
            current_plan = n.plan_id
            lines += ["", f"## {topics.get(n.plan_id) or f'方案 #{n.plan_id}'}", ""]
        p = progress_map.get((n.plan_id, n.id))
        st = p.status.value if p else "not_started"
        mastery = f"{round(float(p.mastery) * 100)}%" if p else "0%"
        star = " ★必学" if n.must_learn else ""
        lines.append(f"### {n.name}{star}")
        lines.append(
            f"- 状态：{_STATUS_LABEL.get(st, st)} · 掌握度 {mastery} · 难度：{_DIFFICULTY_LABEL.get(n.difficulty, n.difficulty)}"
        )
        note = note_map.get(n.id)
        if note:
            lines.append("- 笔记：")
            # 笔记原文按引用块嵌入（用户自有内容，可能是多行 markdown）
            lines += [f"  > {ln}" if ln.strip() else "  >" for ln in note.splitlines()]
        lines.append("")

    md = "\n".join(lines).strip() + "\n"
    filename = f"知识手册-{now:%Y%m%d}.md"
    return Response(
        content=md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


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
    # iter 378 R21：乐观并发期望值（同自由笔记 /notes 语义），不匹配 -> 409
    expected_updated_at: str | None = Field(default=None, max_length=64)


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
    if not limiter.check(f"node_note_write:{user.id}", _NODE_NOTE_WRITE_PER_MIN):
        raise ApiError("rate_limited", "操作过于频繁，请稍后再试", status=429)
    _owned_node_or_404(db, node_id, user)
    content = body.content.strip()
    note = db.query(models.NodeNote).filter_by(user_id=user.id, node_id=node_id).first()
    # iter 378 R21：期望时间戳不匹配（含他窗口已清空/删除 -> note=None 而 cur=None）
    # -> 409 拒绝盲覆盖；不传 = 旧行为
    if body.expected_updated_at is not None:
        cur = note.updated_at.isoformat() if (note is not None and note.updated_at) else None
        if cur != body.expected_updated_at:
            raise ApiError("conflict", "笔记已在其他窗口被修改，请刷新后重试", status=409)
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
    # iter 395：提交后行可能已被并发删方案/会话的级联删掉 -> 访问过期 ORM 实例
    # 抛 ObjectDeletedError -> 500（同 iter 394 progress 同型修法）。
    try:
        db.refresh(note)
    except ObjectDeletedError:
        return {"node_id": node_id, "content": content, "updated_at": None}
    return _note_out(note, node_id)

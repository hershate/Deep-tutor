"""会话 API（plan/03 §一 sessions）。

POST /api/sessions  创建会话
DELETE /api/sessions/{id}  删除会话（级联 DB + 文件）
GET /api/sessions/{id}/materials  会话源资料清单（iter 104 增强，plan/03 未定义）
DELETE /api/sessions/{id}/materials/{mid}  删除单个源资料（iter 104 增强：传错资料可移除后重生成）

流程微调（记录于 stage.md）：先建 session 再上传材料（materials.session_id NOT NULL，
免 schema 迁移且更稳健）；plan/03 原 POST /sessions {material_ids} 简化为 {title?}。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_owned_session, require_auth
from app.api.errors import ApiError
from app.api.plans import (
    _STREAMS,
    _clarify_questions_from_state,
    _plan_topic,
    cancel_running_generation,
    wait_generation_settled_async,
)
from app.core.dbwrite import run_db_write
from app.core.quota import quota_slot
from app.core.ratelimit import limiter
from app.core.storage import delete_material_files, delete_session_files
from app.db import models
from app.db.models import User

log = logging.getLogger("deep_tutor.api.sessions")

router = APIRouter(prefix="/sessions", tags=["sessions"], dependencies=[Depends(require_auth)])

# 每用户会话数上限（err.md A24：此前会话创建无配额，DB 行/文件目录无界增长）
_MAX_SESSIONS_PER_USER = 200
# iter 367（批次五）：创建限流（次/分钟/用户）——配额只限总量；脚本快速建删循环
# 仍可高频打 DB + 会话目录增删
_CREATE_RATE_PER_MIN = 30


class SessionCreate(BaseModel):
    title: str = Field(default="", max_length=256)


def check_session_quota(db: Session, user_id: int) -> None:
    """每用户会话数上限守卫（iter 397：三条建会话路径共用）。

    配额键统一为 ``session_quota:{user_id}``——**所有**建会话路径
    （POST /sessions、POST /materials/clip、POST /kb/generate）都必须在
    **同一把锁**内完成「计数 + 插入 + 提交」，否则并发请求可同时通过
    「未达上限」检查，实际会话数按并发数突破 200 上限（DB 行 + 会话目录
    无界增长）。

    iter 397 前：只有 POST /sessions 加锁；materials.clip 与 kb.generate 只
    count 不加锁——sessions.py 的注释「键与 materials/kb 的会话数配额同命名
    空间 -> 跨端点互斥」并不成立。
    """
    count = db.query(models.Session).filter_by(user_id=user_id).count()
    if count >= _MAX_SESSIONS_PER_USER:
        raise ApiError(
            "quota_exceeded",
            f"会话数已达上限 {_MAX_SESSIONS_PER_USER} 个，请先删除不再需要的会话",
        )


class SessionOut(BaseModel):
    session_id: int
    title: str


@router.post("", response_model=SessionOut)
def create_session(
    body: SessionCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> SessionOut:
    if not limiter.check(f"sess_create:{user.id}", _CREATE_RATE_PER_MIN):
        raise ApiError("rate_limited", f"创建会话限每分钟 {_CREATE_RATE_PER_MIN} 次，请稍后再试")
    # iter 394：配额判定是「先查后写」——并发创建可同时通过「未达上限」检查，
    # 实际写入数按并发数突破（app/core/quota.py：单进程部署下进程内互斥）。
    # iter 397：键 `session_quota:{uid}` 现由**所有**建会话路径共用
    # （sessions / materials.clip / kb.generate）——此前只有本端点加锁，另两处
    # 只 count 不加锁，跨端点互斥并不成立（见 check_session_quota 注释）。
    with quota_slot(f"session_quota:{user.id}"):
        check_session_quota(db, user.id)
        sess = models.Session(user_id=user.id, title=body.title)
        db.add(sess)
        db.commit()
        db.refresh(sess)
    return SessionOut(session_id=sess.id, title=sess.title)


@router.delete("/{session_id}")
async def delete_session(
    session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """删除会话（级联 DB + 文件目录）。

    iter 396 改造为 async 端点：此前为同步端点，等在途生成收敛（最多 15s）期间
    **独占一个 anyio 工作线程**——并发删除 N 个会话即可占满线程池（默认 40 槽），
    其余同步端点（含登录）排队。现在三段式：

    - 准备（写线程）：归属校验 + 资料解析锁检查 + 取消在途生成；
    - 等待（事件循环内异步轮询）：**不占线程**（``wait_generation_settled_async``）；
    - 删除（写线程）：删行 + 提交（写锁争用等 busy_timeout 不会冻结事件循环，
      iter 393 口径）；文件目录清理另走线程池。
    """
    from app.api.materials import _PARSE_LOCKS

    def _prepare() -> str | None:
        """准备阶段（写线程内）：返回错误码；None 表示可继续删除。"""
        sess = get_owned_session(db, session_id, user)  # P4 隔离：他人会话 404（不泄露存在性）
        # iter 248：会话内任一资料解析进行中（窗口最长 900s）禁止整删——删除会让在途
        # 解析结果错绑（rowid 复用）/ 写出孤儿解析文件并重建已删会话目录（同 kb 语义）
        mat_ids = [m.id for m in db.query(models.Material.id).filter_by(session_id=sess.id)]
        if any(
            (lock := _PARSE_LOCKS.get(mid)) is not None and lock.locked() for mid in mat_ids
        ):
            return "conflict"
        # running 态：先 cancel 编排器任务再删（plan/10 §十一），防僵尸任务 _finalize 撞 FK
        cancel_running_generation(session_id)
        return None

    err = await run_db_write(_prepare)
    if err == "conflict":
        raise ApiError("conflict", "会话内有资料正在解析中，请解析完成后再删除会话", status=409)
    # iter 380：等任务收敛（_finalize 落账完成）再删——cancel 只是调度，finalize 与
    # 本端点删除事务并发交错时 tool_calls 批量写撞已删行整批回滚（在途计费丢失，
    # 只剩降级兜底）。取消传播毫秒级，15s 超时容忍（兜底仍在）。
    await wait_generation_settled_async(session_id)

    def _delete() -> None:
        sess = get_owned_session(db, session_id, user)
        db.delete(sess)
        db.commit()

    await run_db_write(_delete)
    try:
        await asyncio.to_thread(delete_session_files, session_id)
    except Exception:  # noqa: BLE001  文件清理失败不阻塞 DB 已删
        # iter 396：此前静默吞掉（Windows 文件占用导致的残留目录运维无从发现）
        log.warning("会话目录清理失败 session=%s", session_id, exc_info=True)
    return {"ok": True}


# --- 会话列表与状态只读查询（iter 184 B1：方案列表占位行/任务管理器/Clarify 深链的数据源）---
# 此前唯一的状态查询途径是 POST /plans/generate（副作用：idle 态会误触发新生成）。


class LatestPlanOut(BaseModel):
    id: int
    version: int
    topic: str
    created_at: str


class SessionStatusOut(BaseModel):
    session_id: int
    title: str
    created_at: str
    status: str  # OrchestratorStatus 值；无状态行 -> "idle"
    updated_at: str | None  # 状态行更新时间（无状态行为 None）
    has_materials: bool
    latest_plan: LatestPlanOut | None
    error: dict | None = None  # 终态错误详情 {code, message}（iter 184 B4 last_error；无/空为 None）
    live: dict | None = None  # 运行中实时进度（iter 185 B5 填；非 running 恒 None）


def _latest_plans_map(db: Session, session_ids: list[int]) -> dict[int, models.Plan]:
    """每会话最新一版方案（避免 N+1：一次 IN 查询后 Python 侧取最新）。

    iter 368：MAX(id) 子查询投影取代全版本加载——旧行把每会话全部历史版本的
    content JSON 列（每版 ≤512KB）整批载入只为挑最新一版，多版本重度用户打满
    GET /sessions 热路径内存/IO。id 单调递增，MAX(id) 即最新插入（版本随插入递增）。
    """
    if not session_ids:
        return {}
    rows = (
        db.query(models.Plan)
        .where(
            models.Plan.id.in_(
                select(func.max(models.Plan.id))
                .where(models.Plan.session_id.in_(session_ids))
                .group_by(models.Plan.session_id)
            )
        )
        .all()
    )
    return {p.session_id: p for p in rows}


def _materials_count_map(db: Session, session_ids: list[int]) -> dict[int, int]:
    if not session_ids:
        return {}
    rows = (
        db.query(models.Material.session_id, func.count())
        .filter(models.Material.session_id.in_(session_ids))
        .group_by(models.Material.session_id)
        .all()
    )
    return {sid: cnt for sid, cnt in rows}


def _session_status_out(
    sess: models.Session,
    state: models.OrchestratorState | None,
    latest: models.Plan | None,
    has_materials: bool,
) -> SessionStatusOut:
    last_error = (state.last_error or {}) if state is not None else {}
    # iter 185 B5：running 且有活动流 -> 暴露实时进度（终态流已标记的不报，状态即将翻转）
    live: dict | None = None
    if state is not None and state.status == models.OrchestratorStatus.running:
        stream = _STREAMS.get(sess.id)
        if stream is not None and stream.terminal_id is None:
            live = {
                "current_tool": stream.current_tool or None,
                "step_index": stream.step_index,
                "total_steps": stream.total_steps,
                "cost": round(stream.cost, 6),
                "last_event_id": stream.seq,
            }
    return SessionStatusOut(
        session_id=sess.id,
        title=sess.title or "",
        created_at=str(sess.created_at),
        status=(state.status.value if state is not None else "idle"),
        updated_at=(str(state.updated_at) if state is not None and state.updated_at else None),
        has_materials=has_materials,
        latest_plan=(
            LatestPlanOut(id=latest.id, version=latest.version, topic=_plan_topic(latest), created_at=str(latest.created_at))
            if latest is not None
            else None
        ),
        error=(last_error or None),
        live=live,
    )


@router.get("")
def list_sessions(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    q: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """会话列表（含生成状态）。排序：running 置顶，其余按最近活动倒序。q 按标题子串。"""
    qy = (
        db.query(models.Session, models.OrchestratorState)
        .outerjoin(models.OrchestratorState, models.OrchestratorState.session_id == models.Session.id)
        .filter(models.Session.user_id == user.id)
    )
    if q and q.strip():
        qy = qy.filter(models.Session.title.contains(q.strip()))
    total = qy.count()
    rows = (
        qy.order_by(
            # running 置顶（活跃优先，占位行诉求）
            case((models.OrchestratorState.status == models.OrchestratorStatus.running, 1), else_=0).desc(),
            func.coalesce(models.OrchestratorState.updated_at, models.Session.created_at).desc(),
        )
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    ids = [sess.id for sess, _ in rows]
    latest = _latest_plans_map(db, ids)
    counts = _materials_count_map(db, ids)
    return {
        "items": [
            _session_status_out(sess, state, latest.get(sess.id), counts.get(sess.id, 0) > 0).model_dump()
            for sess, state in rows
        ],
        "total": total,
    }


@router.get("/{session_id}")
def get_session(
    session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """单会话状态详情（Clarify 深链恢复 / 任务引擎 attach 判态）。他人会话 404。"""
    sess = get_owned_session(db, session_id, user)
    state = db.get(models.OrchestratorState, session_id)
    latest = _latest_plans_map(db, [session_id]).get(session_id)
    counts = _materials_count_map(db, [session_id])
    out = _session_status_out(sess, state, latest, counts.get(session_id, 0) > 0).model_dump()
    out["clarify_questions"] = (
        _clarify_questions_from_state(state)
        if state is not None and state.status == models.OrchestratorStatus.paused_clarify
        else []
    )
    return out


# --- 会话源资料管理（iter 104 增强）：清单 + 单个删除 ---
# 场景：上传/粘贴错资料后此前只能整个会话删掉重来；现可单独移除，再重生成方案。
# 删除不影响已生成的方案/自学单元（源资料仅是生成的输入，无 FK 被引用）。


class SessionMaterialOut(BaseModel):
    material_id: int
    kind: str
    source: str  # 可读摘要：文件名 / URL / 文本前 200 字
    parsed_chars: int
    meta: dict
    created_at: datetime | None
    # 处理状态（iter 225）：pending 待确认 / confirmed 已添加未解析 / parsed 已解析
    status: str = "parsed"


@router.get("/{session_id}/materials")
def list_session_materials(
    session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """会话源资料清单（按 id 升序）。P4 隔离：他人会话 404。"""
    sess = get_owned_session(db, session_id, user)
    rows = (
        db.query(models.Material)
        .filter_by(session_id=sess.id)
        .order_by(models.Material.id)
        .all()
    )
    items = [
        SessionMaterialOut(
            material_id=m.id,
            kind=m.kind.value if hasattr(m.kind, "value") else str(m.kind),
            source=m.source,
            parsed_chars=m.parsed_chars,
            meta=m.meta or {},
            created_at=m.created_at,
            status=m.status,
        ).model_dump()
        for m in rows
    ]
    return {"items": items, "total": len(items)}


@router.delete("/{session_id}/materials/{material_id}")
def delete_material(
    session_id: int,
    material_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """删除单个源资料（DB 行 + 原件/解析文本文件）。

    - 运行态 409：生成中删资料会与编排器读取竞争（同 plans.py R33 模式）。
    - 资料不存在/不属于该会话 -> 404（不泄露存在性）。
    - 文件清理失败不阻塞（DB 已删，与 delete_session 同策略）。
    """
    sess = get_owned_session(db, session_id, user)
    state = db.get(models.OrchestratorState, session_id)
    if state is not None and state.status == models.OrchestratorStatus.running:
        raise ApiError("conflict", "该会话正在生成中，请等待完成或取消后再删除资料", status=409)
    mat = db.get(models.Material, material_id)
    if mat is None or mat.session_id != sess.id:
        raise ApiError("not_found", f"资料 {material_id} 不存在")
    # iter 248：解析进行中禁止删除（窗口最长 900s）——删除会让在途解析的结果
    # 错绑（rowid 复用）或写出孤儿解析文件；409 给用户明确信号（同 iter 247 kb）
    from app.api.materials import _PARSE_LOCKS

    lock = _PARSE_LOCKS.get(material_id)
    if lock is not None and lock.locked():
        raise ApiError("conflict", "该资料正在解析中，请解析完成后再删除", status=409)
    db.delete(mat)
    db.commit()
    try:
        delete_material_files(session_id, material_id)
    except Exception:  # noqa: BLE001  文件清理失败不阻塞 DB 已删
        pass
    return {"ok": True}

"""plans API（plan/03 §一 plans；SSE 生成 + 列表 + 详情）。

POST /api/plans/generate  SSE 流式生成（编排器 + 事件回调 -> SSE）
GET  /api/plans           列表（分页）
GET  /api/plans/{id}      详情
POST /api/plans/{id}/materials  生成自学材料（P2：同步 JSON / SSE 逐单元流，iter 46 内容协商）

稳定性：运行态冲突 409（R33）、预算守卫、错误/取消传播、plan 持久化（大方案溢出文件）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.agent import PIPELINE, BudgetTracker, ExecContext, Orchestrator, run_tool
from app.agent.cost import record_metadata_usage
from app.api.deps import check_daily_budget, get_current_user, get_db, get_owned_plan, get_owned_session, require_auth
from app.api.errors import ApiError
from app.core.credentials import resolve_bing_api_key
from app.core.settings import settings
from app.db import models
from app.db.base import SessionLocal
from app.db.models import User
from app.llm import get_provider
from app.skills.loader import get_skill_registry
from app.tools import default_registry
from app.tools.base import ToolInput, ToolOutput
from app.tools.generate_quiz import QuizSpec, default_quiz_from_plan, extract_check_questions
from app.tools.generate_study_materials import (
    StudyMaterialsSpec,
    StudyUnitSpec,
    UnitContentSpec,
    brief_unit_from_stage,
    build_materials_index,
    default_materials_from_plan,
    render_materials_md,
)
from app.tools.search_resources import search_unit_resources
from app.core.storage import (
    delete_unit_content,
    materials_dir,
    read_unit_content,
    write_unit_content,
)

log = logging.getLogger("deep_tutor.api.plans")

router = APIRouter(prefix="/plans", tags=["plans"], dependencies=[Depends(require_auth)])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # nginx 禁缓冲（plan/09 §十九）
}

PLAN_OVERFLOW_BYTES = 512 * 1024  # plan/01 §五：>512KB 溢出文件

# 心跳间隔：长 LLM 调用期间防 nginx/代理/浏览器空闲断连（plan/09 §十九）。
# SSE 注释行 `: keepalive` 不触发前端事件，仅保活。
_HEARTBEAT_SECONDS = 15.0

# --- SSE 会话缓冲（plan/10 §十二 断线续接游标）---
# 单会话事件缓冲 + 订阅扇出：编排器经 publish 发事件（递增 id）；每个连接持独立队列，
# 订阅时先重放历史 (id > last_event_id) 再接实时。终态后保留可配置时长供迟到重连。
# 与流客户端解耦：断连时编排器任务仍完成并持久化（防 stale running / 丢进度，R24）。
_STREAMS: dict[int, "_SessionStream"] = {}
# err.md A1：状态检查与占位（注册流 + 置 running）必须在同一临界区。
# 端点为同步 def（线程池执行），并发请求可交错通过检查再各自占位
# （双编排器并行 / 绕过每用户并发配额）；单进程内部署用线程锁即可保证原子。
_OCCUPY_LOCK = threading.Lock()
_MAX_BUFFERED_EVENTS = 500  # 单会话最大缓冲；超限丢最旧（降级重放）
# 生成并发配额（plan/09 §十八 每用户并发 1；§二十 R31 全局同时生成 ≤2）
_MAX_GENERATE_PER_USER = 1
_MAX_GENERATE_GLOBAL = 2
# raw_text 截断（plan/09 §十 understand_intent.raw_text ≤10k）
_MAX_RAW_TEXT_CHARS = 10_000
# 注入 prompt 的资料总量上限（err.md A14/B2）：20 条 × 大文件极端下拼接可近 GB 级，
# 内存与 LLM 成本双重风险；超出截断并在内容中标注
_MAX_MATERIALS_TOTAL_CHARS = 100_000
# 单条 parsed 文件读取上限（parse_material 本身已按 MAX_PARSED_CHARS 截断，此处再兜底）
_MAX_PARSED_FILE_CHARS = 200_000


def _check_generate_quota(db: Session, user: User, session_id: int) -> None:
    """生成并发配额：每用户并发 1（plan/09 §十八）+ 全局同时生成 ≤2（plan/09 §二十 R31）。

    仅在「新起一次生成」时检查；续接/终态回放不受限。
    """
    running = db.query(models.OrchestratorState).join(
        models.Session, models.Session.id == models.OrchestratorState.session_id
    ).filter(
        models.Session.user_id == user.id,
        models.OrchestratorState.session_id != session_id,
        models.OrchestratorState.status == models.OrchestratorStatus.running,
    ).count()
    if running >= _MAX_GENERATE_PER_USER:
        raise ApiError("conflict", "您已有另一个生成任务运行中（每用户并发 1）", status=409)
    active = sum(1 for s in _STREAMS.values() if s.terminal_id is None)
    if active >= _MAX_GENERATE_GLOBAL:
        raise ApiError("server_busy", "当前生成任务较多，请稍后重试（全局并发上限）", status=503)
_RETENTION_SECONDS = 300.0  # 终态后保留时长（秒），供迟到重连；超时自动清理


class _SessionStream:
    """单会话 SSE 事件缓冲 + 订阅扇出（plan/10 §十二）。

    单一事件源：编排器 publish -> 各订阅者独立队列（订阅时先重放历史再接实时）。
    terminal_id 标记终态事件 id，投递后流即结束。
    """

    def __init__(self, session_id: int, trace_id: str) -> None:
        self.session_id = session_id
        self.trace_id = trace_id
        self.seq = 0
        self._events: list[tuple[int, str, dict]] = []  # (id, event, data)，有界
        self._subs: list[asyncio.Queue] = []
        self.terminal_id: int | None = None  # 终态事件 id；非空表示流已结束
        self.task: asyncio.Task | None = None
        self.raw_text: str = ""
        self.budget: "BudgetTracker | None" = None
        # iter 185 B5：运行中实时进度——_run_start 的 event_cb 闭包随事件更新，
        # 供 GET /sessions 只读端点 live 字段暴露（任务管理器/列表占位行进度条数据源）
        self.current_tool: str = ""
        self.step_index: int = 0
        self.total_steps: int = 0
        self.cost: float = 0.0

    def publish(self, event: str, data: dict) -> int:
        """发一事件：分配递增 id，入历史 + 扇出到所有订阅者队列。返回事件 id。"""
        self.seq += 1
        eid = self.seq
        item = (eid, event, data)
        self._events.append(item)
        if len(self._events) > _MAX_BUFFERED_EVENTS:
            del self._events[: len(self._events) - _MAX_BUFFERED_EVENTS]
        for q in self._subs:
            q.put_nowait(item)
        return eid

    def mark_terminal(self, eid: int) -> None:
        """标记终态事件 id（投递后流结束）。"""
        self.terminal_id = eid

    def subscribe(self, last_event_id: int) -> asyncio.Queue:
        """订阅：新建队列，先重放历史 (id > last_event_id)，再接实时。"""
        q: asyncio.Queue = asyncio.Queue()
        start = max(last_event_id, 0)
        for item in self._events:
            if item[0] > start:
                q.put_nowait(item)
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subs.remove(q)
        except ValueError:
            pass


class GenerateIn(BaseModel):
    session_id: int
    raw_text: str = ""


class ClarifyIn(BaseModel):
    answers: dict = Field(default_factory=dict)


class RegenIn(BaseModel):
    feedback: str = ""
    scope: str = ""


def _sse(event: str, data: dict, event_id: int | None = None) -> str:
    """格式化一条 SSE 事件。event_id 非空时附 `id:` 行（plan/10 §十二 续接游标，递增）。"""
    parts: list[str] = []
    if event_id is not None:
        parts.append(f"id: {event_id}")
    parts.append(f"event: {event}")
    parts.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    return "\n".join(parts) + "\n\n"


def _load_parsed_materials(db: Session, session_id: int) -> list[dict]:
    mats = db.query(models.Material).filter_by(session_id=session_id).all()
    parsed = []
    for m in mats:
        text = ""
        if m.parsed_text_path:
            try:
                p = Path(m.parsed_text_path)
                if p.exists():
                    text = p.read_text(encoding="utf-8")[:_MAX_PARSED_FILE_CHARS]
            except Exception:  # noqa: BLE001
                log.warning("读取 parsed_text 失败: %s", m.parsed_text_path)
        parsed.append({"text": text, "meta": m.meta or {}})
    return parsed


def _reconstruct_upstream(db: Session, session_id: int, start_tool: str) -> dict[str, ToolOutput]:
    """从 tool_calls 重建 start_tool 之前各工具的最新输出（plan/10 §十三 局部重生成）。

    取每工具 id 最大（最新）的一条 tool_call；缺失工具不注入（字段覆盖检查会处理）。
    """
    start_idx = PIPELINE.index(start_tool)
    rows = db.query(models.ToolCall).filter_by(session_id=session_id).all()
    latest: dict[str, models.ToolCall] = {}
    for r in sorted(rows, key=lambda x: x.id):
        latest[r.tool] = r  # id 递增 -> 后者覆盖为最新
    upstream: dict[str, ToolOutput] = {}
    for name in PIPELINE[:start_idx]:
        tc = latest.get(name)
        if tc is None:
            continue
        upstream[name] = ToolOutput(tool=name, status=tc.status or "ok", data=tc.output or {})
    return upstream


def _clarify_questions_from_state(state: models.OrchestratorState) -> list:
    """从 paused_clarify 的 partial_plan 提取澄清问题（understand_intent.data.clarify_questions）。"""
    partial = state.partial_plan or {}
    intent = partial.get("outputs", {}).get("understand_intent", {}).get("data", {})
    return list(intent.get("clarify_questions") or [])


def _terminal_event(
    db: Session, session_id: int, state: models.OrchestratorState
) -> tuple[str, dict] | None:
    """终态快路径（plan/10 §十二）：返回 (event, data) 或 None（需正常生成）。

    - done -> 查最新 plan 回放 done {plan_id}；无 plan（状态不一致）-> None 重新生成。
    - paused_clarify -> 回放 clarify_needed {questions}；无问题 -> None。
    - error/interrupted -> 回放终态 error（不自动重跑：硬刷新会丢失 raw_text）。
    - idle -> None（正常生成）。

    稳定性：页面刷新或断线重连时不触发重复生成（防重复计费/重复方案）。
    """
    if state.status == models.OrchestratorStatus.done:
        plan = (
            db.query(models.Plan)
            .filter_by(session_id=session_id)
            .order_by(models.Plan.created_at.desc())
            .first()
        )
        if plan is not None:
            return "done", {"plan_id": plan.id}
        return None
    if state.status == models.OrchestratorStatus.paused_clarify:
        questions = _clarify_questions_from_state(state)
        if questions:
            return "clarify_needed", {"questions": questions}
        return None
    if state.status in (models.OrchestratorStatus.error, models.OrchestratorStatus.interrupted):
        # iter 184 B4：回放持久化的真实错误详情（此前笼统 previous_failed）；retryable 供前端显示重试入口
        le = state.last_error or {}
        return "error", {
            "code": le.get("code") or "previous_failed",
            "message": le.get("message") or "上次生成未完成，可重试",
            "stage": "",
            "retryable": True,
        }
    return None


async def _terminal_stream(event: str, data: dict) -> str:
    """终态快路径：仅发一个事件即关闭（不重放历史，plan/10 §十二 done/error 重连直接返回终态）。"""
    yield _sse(event, data, event_id=1)


def _occupy_generation(db: Session, session_id: int, raw_text: str) -> "_SessionStream":
    """同步占位：注册会话流 + 置 running。调用方须持 _OCCUPY_LOCK（err.md A1）。

    占位在端点内、返回 StreamingResponse 之前完成，而非等到流式生成器首次迭代，
    使「状态检查 -> 占位」无窗口期。
    """
    trace_id = uuid.uuid4().hex
    stream = _SessionStream(session_id, trace_id)
    stream.raw_text = raw_text
    _STREAMS[session_id] = stream
    _set_state(db, session_id, models.OrchestratorStatus.running, trace_id)
    return stream


@router.post("/generate")
def generate(
    body: GenerateIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    get_owned_session(db, body.session_id, user)  # P4 隔离：他人会话 404

    parsed = _load_parsed_materials(db, body.session_id)
    # raw_text ≤10k 截断（plan/09 §十）
    raw_text = body.raw_text[:_MAX_RAW_TEXT_CHARS]
    with _OCCUPY_LOCK:  # err.md A1：检查与占位同临界区
        state = db.get(models.OrchestratorState, body.session_id)
        # 运行中：有活动流 -> 续接（重放 + 实时，带 Last-Event-ID）；无活动流（进程重启等 stale）-> 409
        if state is not None and state.status == models.OrchestratorStatus.running:
            stream = _STREAMS.get(body.session_id)
            if stream is not None:
                # 注意：不再要求 terminal_id is None——_finalize 先 mark_terminal
                # 后 _set_state(done)，窗口期内 terminal 流 + running 态并存；
                # _relay 对终态流会重放缓冲事件（含终态）后正常退出，
                # 此前此窗口误判 409「生成已中断」，用户重试才自愈
                return StreamingResponse(
                    _relay(stream, _parse_last_event_id(request)),
                    media_type="text/event-stream",
                    headers=_SSE_HEADERS,
                )
            raise ApiError("conflict", "该会话生成已中断，请返回首页重新生成", status=409)

        # 终态快路径（plan/10 §十二）：done/paused_clarify/error 不重跑，直接回放终态事件
        if state is not None:
            terminal = _terminal_event(db, body.session_id, state)
            if terminal is not None:
                return StreamingResponse(
                    _terminal_stream(terminal[0], terminal[1]),
                    media_type="text/event-stream",
                    headers=_SSE_HEADERS,
                )

        _check_generate_quota(db, user, body.session_id)  # 新起生成前的并发配额（plan/09 §十八/§二十）
        check_daily_budget(db, user.id)  # err.md A6：日预算守卫
        stream = _occupy_generation(db, body.session_id, raw_text)

    return _start_full_generation(stream, body.session_id, raw_text, parsed, request, user)


def _start_full_generation(
    stream: "_SessionStream",
    session_id: int,
    raw_text: str,
    parsed: list[dict],
    request: Request,
    user: User,
) -> StreamingResponse:
    """全量生成启动（iter 184 B4 抽取，generate 与 retry 复用）：
    拼接已解析资料 -> 组装 args/initial_upstream -> 返回生成 SSE 流。"""
    concatenated = "\n\n".join(p["text"] for p in parsed if p["text"])
    if len(concatenated) > _MAX_MATERIALS_TOTAL_CHARS:
        concatenated = concatenated[:_MAX_MATERIALS_TOTAL_CHARS] + "\n…[资料总量超限，已截断]"
    initial_upstream: dict[str, ToolOutput] = {}
    if concatenated:
        # err.md B2：不再附 chunks（与 text 同内容的第二份拷贝，下游无人消费，
        # 全量广播等于逐 LLM 工具重复计费；loop._sanitize_for_prompt 亦有兜底）
        initial_upstream["parse_material"] = ToolOutput(
            tool="parse_material",
            status="ok",
            data={"text": concatenated, "meta": {"source_type": "materials", "pages": len(parsed)}},
        )
    # err.md B2：args.parsed 只传 meta（全文已由 upstream.parse_material.data.text
    # 提供，此前同一份内容在 user 消息里出现 2-3 次）
    parsed_metas = [{"text": "", "meta": p["meta"]} for p in parsed]
    args_by_tool = {"understand_intent": {"raw_text": raw_text, "parsed": parsed_metas}}
    return StreamingResponse(
        _run_start(
            stream, session_id, args_by_tool, initial_upstream, _parse_last_event_id(request), user_id=user.id
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/{session_id}/retry")
def retry(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """失败/中断后重试（iter 184 B4）：以持久化的原始输入（partial_plan.raw_text）重新全量生成。

    - 仅 error/interrupted 终态可重试（running/done/paused_clarify -> 409）；
    - 原始需求与材料均丢失（B4 前的旧失败会话）-> 409 引导回首页；
    - 响应即标准生成 SSE 流（事件与 /generate 完全一致，前端同一消费路径）。
    """
    get_owned_session(db, session_id, user)  # P4 隔离：他人会话 404
    parsed = _load_parsed_materials(db, session_id)
    with _OCCUPY_LOCK:  # err.md A1：检查与占位同临界区
        state = db.get(models.OrchestratorState, session_id)
        if state is None or state.status not in (
            models.OrchestratorStatus.error,
            models.OrchestratorStatus.interrupted,
        ):
            raise ApiError("conflict", "仅失败或中断的会话可重试", status=409)
        raw_text = ((state.partial_plan or {}).get("raw_text") or "")[:_MAX_RAW_TEXT_CHARS]
        if not raw_text and not any(p["text"] for p in parsed):
            raise ApiError("conflict", "无法重试：原始需求已丢失，请返回首页重新生成", status=409)
        _check_generate_quota(db, user, session_id)
        check_daily_budget(db, user.id)
        stream = _occupy_generation(db, session_id, raw_text)
    return _start_full_generation(stream, session_id, raw_text, parsed, request, user)


@router.post("/{session_id}/clarify")
def clarify(
    session_id: int,
    body: ClarifyIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """澄清续接（plan/10 §四）：paused_clarify 状态下以 answers 重跑 understand_intent -> 继续 pipeline。"""
    get_owned_session(db, session_id, user)  # P4 隔离：他人会话 404
    parsed = _load_parsed_materials(db, session_id)
    with _OCCUPY_LOCK:  # err.md A1：检查与占位同临界区
        state = db.get(models.OrchestratorState, session_id)
        if state is not None and state.status == models.OrchestratorStatus.running:
            raise ApiError("conflict", "该会话已有生成任务运行中", status=409)
        if state is None or state.status != models.OrchestratorStatus.paused_clarify:
            raise ApiError("conflict", "该会话无待澄清任务", status=409)
        partial = state.partial_plan or {}
        outputs_dump = partial.get("outputs", {})
        initial_upstream = {
            tool: ToolOutput(tool=tool, status=d.get("status", "ok"), data=d.get("data", {}))
            for tool, d in outputs_dump.items()
        }
        raw_text = partial.get("raw_text", "")[:_MAX_RAW_TEXT_CHARS]
        _check_generate_quota(db, user, session_id)
        check_daily_budget(db, user.id)  # err.md A6：日预算守卫
        stream = _occupy_generation(db, session_id, raw_text)
    args_by_tool = {"understand_intent": {"raw_text": raw_text, "parsed": [{"text": "", "meta": p["meta"]} for p in parsed], "answers": body.answers}}
    return StreamingResponse(
        _run_start(stream, session_id, args_by_tool, initial_upstream, 0, user_id=user.id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


def cancel_running_generation(session_id: int) -> bool:
    """取消会话运行中的生成任务（线程安全；供 cancel 端点与 DELETE /sessions 复用）。

    经 `call_soon_threadsafe` 在任务所属事件循环上调度 cancel，故同步/异步调用方均安全。
    返回是否已调度取消（无活动任务 -> False）。
    """
    stream = _STREAMS.get(session_id)
    if stream is None or stream.task is None or stream.terminal_id is not None:
        return False
    try:
        stream.task.get_loop().call_soon_threadsafe(stream.task.cancel)
    except Exception:  # noqa: BLE001
        log.warning("取消任务失败 session=%s", session_id)
        return False
    return True


@router.post("/{session_id}/cancel")
async def cancel(
    session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """取消运行中的生成（plan/10 §十一）。

    取消活动流的编排器任务 -> done-callback（`_finalize`）发 `error{code:cancelled}`
    并置 error 态；连接中的客户端经 SSE 收到该事件。无运行任务 -> 409。
    """
    get_owned_session(db, session_id, user)  # P4 隔离：他人会话 404
    if not cancel_running_generation(session_id):
        raise ApiError("conflict", "该会话无运行中的生成任务", status=409)
    return {"ok": True, "status": "cancelling"}


@router.post("/{plan_id}/regenerate")
def regenerate(
    plan_id: int,
    body: RegenIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """局部重生成（plan/10 §十三）：从 scope 起级联重跑其下游，feedback 注入 start_tool。

    - scope：PIPELINE 中 understand_intent 之后的工具名（其上游从 tool_calls 重建）。
    - plans.version += 1（_persist_plan 自动递增）。
    """
    plan = get_owned_plan(db, plan_id, user)  # P4 隔离：他人方案 404
    session_id = plan.session_id
    if body.scope not in PIPELINE:
        raise ApiError("bad_request", f"scope 必须为有效工具名: {PIPELINE}", status=400)
    start_tool = body.scope
    initial_upstream = _reconstruct_upstream(db, session_id, start_tool)
    with _OCCUPY_LOCK:  # err.md A1：检查与占位同临界区
        state = db.get(models.OrchestratorState, session_id)
        if state is not None and state.status == models.OrchestratorStatus.running:
            raise ApiError("conflict", "该会话已有生成任务运行中", status=409)
        # err.md A7：重生成次数上限（settings.max_regenerations 此前从未被引用，
        # 同一 plan 可无限次重生成，LLM 成本无上限）。version 初值 1，每次重生成 +1。
        if plan.version > settings.max_regenerations:
            raise ApiError(
                "quota_exceeded",
                f"该方案重生成次数已达上限 {settings.max_regenerations} 次（如需更多请新建会话重新生成）",
            )
        # understand_intent 须在上游（否则需原始 raw_text，不支持）
        if "understand_intent" not in initial_upstream:
            raise ApiError(
                "bad_request",
                f"不支持从 {start_tool} 重生成（需 understand_intent 上游；请从 design_stages 及之后起）",
                status=400,
            )
        _check_generate_quota(db, user, session_id)
        check_daily_budget(db, user.id)  # err.md A6：日预算守卫
        stream = _occupy_generation(db, session_id, "")
    args_by_tool = {start_tool: {"fixes": [body.feedback]} if body.feedback else {}}
    return StreamingResponse(
        _run_start(
            stream,
            session_id,
            args_by_tool,
            initial_upstream,
            _parse_last_event_id(request),
            start_tool=start_tool,
            user_id=user.id,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


def _parse_last_event_id(request: Request) -> int:
    """解析 Last-Event-ID 请求头（续接游标，plan/10 §十二）。无效/缺失返回 0。"""
    raw = request.headers.get("last-event-id")
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _schedule_cleanup(session_id: int) -> None:
    """终态后延迟从 _STREAMS 移除（保留窗口供迟到重连）。无运行循环则立即移除。"""
    try:
        loop = asyncio.get_running_loop()
        loop.call_later(_RETENTION_SECONDS, _STREAMS.pop, session_id, None)
    except RuntimeError:
        _STREAMS.pop(session_id, None)


def _finalize(session_id: int, stream: "_SessionStream", task: asyncio.Task) -> None:
    """编排器任务结束回调：持久化结果 + 发布终态事件（与流客户端解耦）。

    单一持久化路径：无论客户端是否断连，任务结束即持久化（防 stale running / 丢进度，R24）。
    在事件循环线程内同步执行（SQLite 写入快）；异常兜底确保流不挂死。
    """
    db = SessionLocal()
    try:
        # 会话已删除（如 DELETE /sessions 先取消了任务），或 rowid 被新会话复用
        # （err.md A12：旧任务 finalize 会把结果写进无关新会话）-> 不写 DB，
        # 仅发终态 + 清理。trace_id 匹配才说明该会话仍属本次生成。
        state = db.get(models.OrchestratorState, session_id)
        if (
            db.get(models.Session, session_id) is None
            or state is None
            or state.trace_id != stream.trace_id
        ):
            if stream.terminal_id is None:
                eid = stream.publish("error", {"code": "cancelled", "message": "会话已删除", "stage": ""})
                stream.mark_terminal(eid)
            return
        if task.cancelled():
            # iter 184 B4：取消亦持久化原始输入与错误详情（可重试）
            partial = {"raw_text": stream.raw_text, "outputs": {}}
            _set_state(
                db, session_id, models.OrchestratorStatus.error, stream.trace_id,
                partial=partial, last_event_id=str(stream.seq),
                last_error={"code": "cancelled", "message": "生成已取消"},
                budget_used=stream.budget.spent if stream.budget else 0.0,
            )
            eid = stream.publish("error", {"code": "cancelled", "message": "生成已取消", "stage": ""})
            stream.mark_terminal(eid)
            return
        try:
            result = task.result()
        except Exception as exc:  # noqa: BLE001
            log.exception("orchestrator 任务异常: %s", exc)
            partial = {"raw_text": stream.raw_text, "outputs": {}}
            _set_state(
                db, session_id, models.OrchestratorStatus.error, stream.trace_id,
                partial=partial, last_event_id=str(stream.seq),
                last_error={"code": "internal_error", "message": "生成失败"},
                budget_used=stream.budget.spent if stream.budget else 0.0,
            )
            eid = stream.publish("error", {"code": "internal_error", "message": "生成失败", "stage": ""})
            stream.mark_terminal(eid)
            return
        if result.status == "done":
            _persist_tool_calls(db, session_id, stream.trace_id, result.outputs)
            plan_id = _persist_plan(db, session_id, result.plan)
            cost = stream.budget.spent if stream.budget else 0.0
            data = {"plan_id": plan_id, "metadata": {"tools": len(result.outputs), "cost": round(cost, 6)}}
            eid = stream.publish("done", data)
            stream.mark_terminal(eid)
            _set_state(
                db, session_id, models.OrchestratorStatus.done, stream.trace_id,
                last_event_id=str(stream.seq), last_error={}, budget_used=cost,
            )
            # iter 184 B3：自动标题回填——空标题/「学习方案」/raw_text 截断前缀标题
            # （Home 传 rawText.slice(0,40)）-> intent.topic；手工标题不满足前缀条件不覆盖
            intent_out = result.outputs.get("understand_intent")
            topic = str((intent_out.data.get("topic") or "") if intent_out is not None else "").strip()
            sess = db.get(models.Session, session_id)
            if sess is not None and topic:
                t = sess.title or ""
                if t == "" or t == "学习方案" or (stream.raw_text or "").startswith(t):
                    sess.title = topic[:256]
                    db.commit()
        elif result.status == "paused_clarify":
            _persist_tool_calls(db, session_id, stream.trace_id, result.outputs)
            partial = {
                "raw_text": stream.raw_text,
                "outputs": {t: {"status": o.status, "data": o.data} for t, o in result.outputs.items()},
            }
            _set_state(
                db,
                session_id,
                models.OrchestratorStatus.paused_clarify,
                stream.trace_id,
                partial=partial,
                last_event_id=str(stream.seq),
                last_error={},
                budget_used=stream.budget.spent if stream.budget else 0.0,
            )
            # clarify_needed 已由编排器 emit（stream.seq 即其 id）；标记终态使流结束
            stream.mark_terminal(stream.seq)
        else:
            msg = result.errors[-1] if result.errors else "生成失败"
            data = {"code": "generation_failed", "message": msg, "stage": ""}
            eid = stream.publish("error", data)
            stream.mark_terminal(eid)
            # iter 184 B4：失败亦持久化原始输入与已产出 outputs（可重试；续跑升级留路）
            partial = {
                "raw_text": stream.raw_text,
                "outputs": {t: {"status": o.status, "data": o.data} for t, o in result.outputs.items()},
            }
            _set_state(
                db, session_id, models.OrchestratorStatus.error, stream.trace_id,
                partial=partial, last_event_id=str(stream.seq),
                last_error={"code": "generation_failed", "message": msg},
                budget_used=stream.budget.spent if stream.budget else 0.0,
            )
    except Exception:  # noqa: BLE001  持久化失败不阻塞流；兜底发终态防挂死
        log.exception("finalize 异常 session=%s", session_id)
        if stream.terminal_id is None:
            eid = stream.publish("error", {"code": "internal_error", "message": "生成失败", "stage": ""})
            stream.mark_terminal(eid)
    finally:
        db.close()
        _schedule_cleanup(session_id)


async def _run_start(
    stream: "_SessionStream",
    session_id: int,
    args_by_tool: dict,
    initial_upstream: dict,
    last_event_id: int = 0,
    start_tool: str = "understand_intent",
    user_id: int | None = None,
) -> str:
    """启动一次生成：编排器任务（done-callback 持久化）+ 续接投递。

    stream：调用方在 _OCCUPY_LOCK 内经 _occupy_generation 创建并占位的会话流
    （err.md A1：检查与占位同临界区，此处不再自行创建/置态）。
    start_tool：编排器起始工具。默认 understand_intent（全量生成 / 澄清续接）；
    局部重生成传 scope（plan/10 §十三：从受影响工具起级联重跑其下游，重跑范围最小化）。
    user_id：发起用户（P4 iter 58）：LLM 供应商档案按该用户解析（get_provider(user_id)）。
    """
    # 无 LLM Provider -> 发 error 终态（不启动任务）；档案按发起用户解析（P4 隔离）
    try:
        provider = get_provider(user_id)
    except Exception as exc:  # noqa: BLE001
        eid = stream.publish("error", {"code": "no_llm_provider", "message": str(exc), "stage": ""})
        stream.mark_terminal(eid)
        db = SessionLocal()
        try:  # err.md A22：try/finally 保证 Session 关闭
            # iter 184 B4：无 provider 失败亦持久化原始输入（可重试）
            _set_state(
                db, session_id, models.OrchestratorStatus.error, stream.trace_id,
                partial={"raw_text": stream.raw_text, "outputs": {}},
                last_event_id=str(stream.seq),
                last_error={"code": "no_llm_provider", "message": str(exc)[:500]},
            )
        finally:
            db.close()
        _schedule_cleanup(session_id)
        async for chunk in _relay(stream, last_event_id):
            yield chunk
        return

    budget = BudgetTracker(limit=settings.budget_per_session_usd)
    stream.budget = budget
    registry = default_registry()
    ctx = ExecContext(
        session_id=str(session_id),
        trace_id=stream.trace_id,
        registry=registry,
        llm_provider=provider,
        skill_registry=get_skill_registry() or None,  # 空 -> None -> loop 走 inline_prompt
        budget=budget,
        # iter 223：凭证档案解析后的有效 Bing Key（按发起用户；空 -> 工具内回退 .env）
        bing_api_key=resolve_bing_api_key(user_id),
    )
    def _on_event(e: str, d: dict) -> int:
        # iter 185 B5：发布前更新流级实时进度（tool_start 记步骤，tool_end 累计费用）
        if e == "tool_start":
            stream.current_tool = str(d.get("tool") or "")
            stream.step_index = int(d.get("step_index") or 0)
            stream.total_steps = int(d.get("total_steps") or 0)
        elif e == "tool_end":
            stream.cost += float((d.get("metadata") or {}).get("cost") or 0.0)
        return stream.publish(e, d)

    orch = Orchestrator(
        registry, ctx, event_cb=_on_event, max_tool_calls=settings.max_tool_calls
    )
    task = asyncio.create_task(
        orch.run(args_by_tool, start_tool=start_tool, initial_upstream=initial_upstream)
    )
    stream.task = task
    task.add_done_callback(lambda t: _finalize(session_id, stream, t))

    async for chunk in _relay(stream, last_event_id):
        yield chunk


async def _relay(stream: "_SessionStream", last_event_id: int) -> str:
    """订阅会话流并投递 SSE 事件（重放历史 + 实时），含心跳与终态退出。"""
    q = stream.subscribe(last_event_id)
    last_send = time.monotonic()
    last_yielded = 0
    try:
        while True:
            try:
                eid, event, data = await asyncio.wait_for(q.get(), timeout=0.2)
            except asyncio.TimeoutError:
                # 终态已标记：若已投递则退出（兜底防空转），否则继续等终态事件入队
                if stream.terminal_id is not None:
                    if last_yielded >= stream.terminal_id:
                        break
                    continue
                # 心跳：长 LLM 调用期间防 nginx/代理/浏览器空闲断连
                if time.monotonic() - last_send > _HEARTBEAT_SECONDS:
                    yield ": keepalive\n\n"
                    last_send = time.monotonic()
                continue
            yield _sse(event, data, event_id=eid)
            last_send = time.monotonic()
            last_yielded = eid
            if stream.terminal_id is not None and eid == stream.terminal_id:
                break
    finally:
        stream.unsubscribe(q)


def _set_state(
    db: Session,
    session_id: int,
    status: models.OrchestratorStatus,
    trace_id: str,
    partial: dict | None = None,
    last_event_id: str | None = None,
    last_error: dict | None = None,
    budget_used: float | None = None,
) -> None:
    state = db.get(models.OrchestratorState, session_id)
    if state is None:
        state = models.OrchestratorState(session_id=session_id, status=status, trace_id=trace_id)
        db.add(state)
    else:
        state.status = status
        state.trace_id = trace_id
    if partial is not None:
        state.partial_plan = partial
    if last_event_id is not None:
        state.last_event_id = last_event_id
    # iter 184 B4：错误详情持久化（done/paused 清空 {}）+ 启用死列 budget_used；
    # current_tool 在状态迁移点统一清空（运行中的实时当前工具由 _STREAMS 内存提供，见 B5）
    if last_error is not None:
        state.last_error = last_error
    if budget_used is not None:
        state.budget_used = budget_used
    state.current_tool = None
    db.commit()


def _persist_tool_calls(db: Session, session_id: int, trace_id: str, outputs: dict[str, ToolOutput]) -> None:
    """持久化 tool_calls + api_usage（成本计量 Pillar 3，plan/01 §三）。批量写，失败不阻塞。

    api_usage.user_id 取会话归属用户（P4 iter 58 按用户统计用量）。
    """
    try:
        sess = db.get(models.Session, session_id)
        usage_user_id = sess.user_id if sess is not None else None
        for tool_name, out in outputs.items():
            m = out.metadata
            err_msg = "; ".join(e.message for e in out.errors) if out.errors else None
            db.add(
                models.ToolCall(
                    session_id=session_id,
                    tool=tool_name,
                    input={},
                    output=out.data,
                    status=out.status,
                    error=err_msg,
                    llm_tokens_in=m.tokens_in,
                    llm_tokens_out=m.tokens_out,
                    cost=m.cost,
                    duration_ms=m.duration_ms,
                )
            )
            if m.tokens_in or m.tokens_out:  # LLM 调用才记 api_usage
                db.add(
                    models.ApiUsage(
                        user_id=usage_user_id,
                        provider="anthropic",
                        model=m.model,
                        tokens_in=m.tokens_in,
                        tokens_out=m.tokens_out,
                        cache_read=m.cache_read,
                        cache_write=m.cache_write,
                        cost=m.cost,
                    )
                )
        db.commit()
    except Exception:  # noqa: BLE001  审计写入失败不阻塞主流程
        log.exception("持久化 tool_calls 失败")
        db.rollback()


def _persist_plan(db: Session, session_id: int, plan_dict: dict) -> int:
    content_json = json.dumps(plan_dict, ensure_ascii=False)
    # version = 该会话现有最大版本 + 1（plan/10 §十三 plans.version += 1；首版 1）
    existing = db.query(models.Plan).filter_by(session_id=session_id).all()
    version = (max(p.version for p in existing) + 1) if existing else 1
    plan = models.Plan(session_id=session_id, content=plan_dict, version=version)
    db.add(plan)
    db.flush()
    if len(content_json.encode("utf-8")) > PLAN_OVERFLOW_BYTES:
        from app.core.storage import exports_dir

        path = exports_dir(session_id) / f"plan_{plan.id}.json"
        path.write_text(content_json, encoding="utf-8")
        plan.content_path = str(path)
        plan.content = {}
    _persist_knowledge_nodes(db, plan.id, plan_dict)
    db.commit()
    db.refresh(plan)
    # 后端插件事件（API.md「后端插件 API」§事件清单）：生成/重生成/重试均经本函数
    # 落库新版本，统一在此广播；监听器异常隔离，不影响主流程
    from app.plugins import bus as plugin_bus

    plugin_bus.emit(
        "plan_created",
        {
            "plan_id": plan.id,
            "session_id": session_id,
            "version": plan.version,
            "topic": str(((plan_dict or {}).get("intent") or {}).get("topic") or ""),
        },
    )
    return plan.id


def _persist_knowledge_nodes(db: Session, plan_id: int, plan_dict: dict) -> None:
    """持久化 knowledge_map.nodes 为 knowledge_nodes 行（进度/quiz 关联用，plan/01 §四）。

    sid 保留 plan 内容中的原始字符串 id（前端 quiz node_id -> DB id 映射）；
    parent_id 两遍插入（先建行取 id，再回填父引用）。上限 100（同 build_knowledge_map 截断）。
    """
    nodes = ((plan_dict.get("knowledge_map") or {}).get("nodes") or [])[:100]
    sid_to_row: dict[str, models.KnowledgeNode] = {}
    for n in nodes:
        name = str(n.get("name") or "")[:256]
        if not name:
            continue
        row = models.KnowledgeNode(
            plan_id=plan_id,
            sid=str(n.get("id") or "")[:64],
            name=name,
            difficulty=int(n.get("difficulty") or 1),
            must_learn=bool(n.get("must_learn")),
        )
        db.add(row)
        if row.sid:
            sid_to_row[row.sid] = row
    db.flush()
    for n in nodes:
        pid = n.get("parent_id")
        row = sid_to_row.get(str(n.get("id") or ""))
        parent = sid_to_row.get(str(pid)) if pid is not None else None
        if row is not None and parent is not None and parent is not row:
            row.parent_id = parent.id
    db.flush()


# --- 列表 / 详情 ---


class PlanSummary(BaseModel):
    id: int
    session_id: int
    version: int
    created_at: str
    topic: str = ""  # 列表展示用（iter 84：侧栏/方案列表页需要标题，免 N+1 详情查询）


def _plan_topic(p: models.Plan) -> str:
    """方案标题（intent.topic），大方案从 content_path 文件读；失败静默为空。"""
    content = p.content
    if not content and p.content_path:
        try:
            content = json.loads(Path(p.content_path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            content = None
    if isinstance(content, dict):
        return str((content.get("intent") or {}).get("topic") or content.get("title") or "")
    return ""


@router.get("")
def list_plans(
    session_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    q: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    # P4 隔离：仅列当前用户会话下的方案；指定 session_id 须为本用户会话（否则 404）
    q_ = db.query(models.Plan).join(models.Session, models.Plan.session_id == models.Session.id).filter(
        models.Session.user_id == user.id
    )
    if session_id is not None:
        get_owned_session(db, session_id, user)
        q_ = q_.filter(models.Plan.session_id == session_id)
    if q and q.strip():
        # iter 128：按主题子串搜索（JSON_EXTRACT intent.topic；溢出落盘的大方案
        # content={} 无路径 -> NULL 不匹配，属已知限制见 stage.md）
        q_ = q_.filter(models.Plan.content["intent"]["topic"].as_string().contains(q.strip()))
    total = q_.count()
    items = q_.order_by(models.Plan.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {
        "items": [
            PlanSummary(
                id=p.id, session_id=p.session_id, version=p.version,
                created_at=str(p.created_at), topic=_plan_topic(p),
            ).model_dump()
            for p in items
        ],
        "total": total,
    }


@router.get("/{plan_id}")
def get_plan(
    plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    plan = get_owned_plan(db, plan_id, user)  # P4 隔离：他人方案 404
    return {"id": plan.id, "session_id": plan.session_id, "version": plan.version, "plan": _load_plan_content(plan)}


def _load_plan_content(plan: models.Plan) -> dict:
    """读 plan 内容（大方案从 content_path 溢出文件回读）；读不到返 {}。"""
    content = plan.content
    if not content and plan.content_path:
        try:
            content = json.loads(Path(plan.content_path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            content = {}
    return content or {}


def _plan_or_404(db: Session, plan_id: int, user: User) -> tuple[models.Plan, dict]:
    """加载当前用户拥有的 plan + 内容；不存在/属他人 -> 404，内容为空 -> 400。"""
    plan = get_owned_plan(db, plan_id, user)
    content = _load_plan_content(plan)
    if not content:
        raise ApiError("empty_content", "方案内容为空")
    return plan, content


# --- 自学材料（P2，plan/07 §五；同步 JSON / SSE 逐单元流（iter 46 内容协商）；iter 43 起持久化）---


class MaterialsScopeIn(BaseModel):
    """局部生成范围（plan/02 §13 scope；缺省=全方案）。"""

    stage_indexes: list[int] | None = None
    node_ids: list[str] | None = None


class MaterialsIn(BaseModel):
    depth: Literal["brief", "full"] = "full"
    refresh: bool = False  # True 强制重新生成（替换旧单元，内容文件级联清理）
    scope: MaterialsScopeIn | None = None
    # iter 93：按单元重生成——选中若干单元单独重新生成（隐含强制重生成，与 scope 互斥；
    # 仅 SSE 路径支持，同步 JSON 路径 422）。上限同 _MAX_MATERIAL_UNITS（12）
    unit_ids: list[int] | None = Field(default=None, max_length=12)


def _stage_for_unit(content: dict, unit: models.StudyUnit) -> dict:
    """单元对应的 plan stage（stage_index 匹配）；阶段已消失/无归属 -> 以单元标题构造
    最小伪阶段（generate_unit_content 只需 name/topics，保证任意单元可重生成）。"""
    if unit.stage_index is not None:
        for s in content.get("stages") or []:
            if s.get("index") == unit.stage_index:
                return s
    return {"name": unit.title}


def _resolve_scope_stages(content: dict, scope: MaterialsScopeIn | None) -> list[tuple[dict, int | None]]:
    """scope -> [(stage, stage_index)] 选定子集（plan/02 §13）。

    stage_indexes 直接选阶段（非法 -> 422）；node_ids 经知识图谱节点名匹配
    阶段 topics（无匹配 -> 422）。无 scope -> 全部阶段。
    """
    stages = list(content.get("stages") or [])
    if scope is None:
        return [(s, s.get("index")) for s in stages]
    if scope.stage_indexes is not None:
        picked: list[tuple[dict, int | None]] = []
        for i in scope.stage_indexes:
            match = next((s for s in stages if s.get("index") == i), None)
            if match is None:
                raise ApiError("invalid_scope", f"stage_index {i} 不存在（方案共 {len(stages)} 个阶段）")
            picked.append((match, i))
        return picked
    if scope.node_ids is not None:
        nodes = (content.get("knowledge_map") or {}).get("nodes") or []
        wanted = set(scope.node_ids)
        names = {n.get("name") for n in nodes if n.get("id") in wanted and n.get("name")}
        if not names:
            raise ApiError("invalid_scope", "node_ids 均不在方案知识图谱中")
        picked = [
            (s, s.get("index"))
            for s in stages
            if any(t.get("name") in names for t in (s.get("topics") or []))
        ]
        if not picked:
            raise ApiError("invalid_scope", "node_ids 未匹配到任何阶段知识点")
        return picked
    return [(s, s.get("index")) for s in stages]


def _delete_study_units(
    db: Session, plan_id: int, stage_indexes: set[int] | None = None
) -> list[str]:
    """删除旧单元（ORM 级联 study_materials），返回旧内容文件路径（提交与文件清理由调用方负责）。

    stage_indexes 非空时仅删该 scope 内单元（plan/02 §13 局部生成不清整方案）。
    """
    q = db.query(models.StudyUnit).filter_by(plan_id=plan_id)
    if stage_indexes is not None:
        q = q.filter(models.StudyUnit.stage_index.in_(stage_indexes))
    old_units = q.all()
    old_paths = [m.content_md_path for u in old_units for m in u.materials]
    for u in old_units:
        db.delete(u)  # cascade study_materials
    db.flush()
    return old_paths


def _resequence_study_units(db: Session, plan_id: int) -> None:
    """局部生成后重排 order（按 stage_index、原 order 稳定排序，NULL stage 排后）。"""
    units = db.query(models.StudyUnit).filter_by(plan_id=plan_id).all()
    units.sort(key=lambda u: (u.stage_index is None, u.stage_index or 0, u.order))
    for i, u in enumerate(units):
        u.order = i
    db.flush()


def _insert_study_unit(
    db: Session,
    plan_id: int,
    session_id: int,
    order: int,
    u: StudyUnitSpec,
    resources: list | None = None,
    stage_index: int | None = None,
) -> str:
    """插入单单元行 + 写内容文件（>500 行自动拆子文件，plan/02 §13），返回新文件路径（提交由调用方负责）。
    resources：单元 WebSearch 补充资料（iter 48），入 study_materials.resources。
    stage_index：单元对应阶段（scope 局部生成的替换粒度）。"""
    row = models.StudyUnit(
        plan_id=plan_id,
        stage_index=stage_index,
        title=u.title[:256],
        prerequisites=list(u.prerequisites),
        objectives=list(u.objectives),
        estimated_time=u.estimated_time[:64],
        order=order,
    )
    db.add(row)
    db.flush()
    path = write_unit_content(session_id, row.id, u.content_md)
    db.add(
        models.StudyMaterial(
            unit_id=row.id,
            content_md_path=str(path),
            size=len(u.content_md.encode("utf-8")),
            resources=list(resources or []),
        )
    )
    return str(path)


def _write_materials_index(session_id: int, content: dict, units: list[models.StudyUnit]) -> None:
    """生成并落盘 index 文档（plan/02 §13 index_url：路线图+计划表+复习节点）。

    从全部已持久化单元构建（局部生成后 index 亦反映全方案）。
    """
    intent = content.get("intent") or {}
    spec = StudyMaterialsSpec(
        title=intent.get("topic") or "学习方案",
        units=[
            StudyUnitSpec(
                title=u.title,
                objectives=list(u.objectives or []),
                prerequisites=list(u.prerequisites or []),
                estimated_time=u.estimated_time or "",
            )
            for u in units
        ],
    )
    md = build_materials_index(content, spec)
    (materials_dir(session_id) / "index.md").write_text(md, encoding="utf-8")


def _read_materials_index(session_id: int) -> str:
    """读 index 文档（不存在 -> 空串）。"""
    try:
        return (materials_dir(session_id) / "index.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def _cleanup_stale_material_files(old_paths: list[str], new_paths: list[str]) -> None:
    """旧内容文件清理（best-effort，含拆分子文件）；排除与新文件相同的路径
    （SQLite rowid 复用时新旧 unit id 可能相同 -> 同名文件已是新内容，不可删）。"""
    stale = set(old_paths) - set(new_paths)
    for p in stale:
        try:
            delete_unit_content(p)
        except OSError:
            log.warning("清理旧单元内容文件失败: %s", p)


def _persist_study_units(db: Session, plan: models.Plan, spec: StudyMaterialsSpec, content: dict | None = None) -> None:
    """替换式持久化：删旧单元 -> 插新行 + 写内容文件 + 落盘 index 文档，一次性提交。

    study_units 存元数据，content_md 入文件（plan/01 §五 大内容入文件系统）。
    spec 无 node/stage 归属信息（单文档 MVP），node_id/stage_index 留空。
    """
    old_paths = _delete_study_units(db, plan.id)
    new_paths = [_insert_study_unit(db, plan.id, plan.session_id, i, u) for i, u in enumerate(spec.units)]
    if content is not None:
        units = db.query(models.StudyUnit).filter_by(plan_id=plan.id).order_by(models.StudyUnit.order).all()
        _write_materials_index(plan.session_id, content, units)
    db.commit()
    _cleanup_stale_material_files(old_paths, new_paths)


def _units_to_spec(db: Session, plan: models.Plan, units: list[models.StudyUnit], content: dict) -> StudyMaterialsSpec:
    """持久化单元 -> StudyMaterialsSpec（重渲染 markdown 用；title 由 intent 推算，不入库）。"""
    intent = content.get("intent") or {}
    specs: list[StudyUnitSpec] = []
    for u in units:
        content_md = ""
        if u.materials:
            try:
                content_md = read_unit_content(u.materials[0].content_md_path)  # 自动串联拆分子文件
            except OSError:
                log.warning("读取单元内容文件失败: %s", u.materials[0].content_md_path)
        specs.append(
            StudyUnitSpec(
                title=u.title,
                objectives=list(u.objectives or []),
                prerequisites=list(u.prerequisites or []),
                estimated_time=u.estimated_time or "",
                content_md=content_md,
            )
        )
    return StudyMaterialsSpec(title=intent.get("topic") or "学习方案", units=specs)


_MAX_MATERIAL_UNITS = 12  # 单元数上限（plan/07 §三 3-12 单元；超出截断）


async def _materials_sse(
    plan_id: int,
    session_id: int,
    content: dict,
    body: MaterialsIn,
    old_paths: list[str],
    user_id: int,
    picked: list[tuple[dict, int | None]],
) -> AsyncIterator[str]:
    """逐单元材料生成 SSE 流（plan/07 §三：按 stage 拆分，每单元一次 LLM；失败降级简要大纲）。

    旧单元已在端点内删除并提交；本流逐单元生成 -> 落库（逐单元提交，断线保留已生成进度），
    结束后重排 order（局部生成）、落盘 index 文档、清理旧内容文件。
    一次性流，不做断线重连（类 clarify 终态快路径之外的轻量场景）。
    事件：unit_start {order,title,total} -> unit_done {order,title,degraded} ×N
          -> done {unit_count,degraded_count}；异常兜底 error {code,message}。
    user_id：发起用户（P4 iter 58）：供应商档案按该用户解析。
    picked：[(stage, stage_index)]（scope 解析结果；空 -> 单占位单元）。
    """
    db = SessionLocal()
    new_paths: list[str] = []
    try:
        stages = picked[:_MAX_MATERIAL_UNITS] or [({}, None)]  # 无阶段 -> 单占位单元
        total = len(stages)
        try:
            provider = get_provider(user_id)
        except Exception:  # noqa: BLE001  无 key -> 全部单元降级 brief
            provider = None
        ctx = ExecContext(
            session_id=str(session_id),
            trace_id=f"materials-{plan_id}",
            registry=default_registry(),
            llm_provider=provider,
            data_dir=settings.data_dir,
            budget=BudgetTracker(limit=settings.budget_per_session_usd),  # err.md A6
            bing_api_key=resolve_bing_api_key(user_id),  # iter 223：凭证档案有效 Bing Key
        )
        topic = (content.get("intent") or {}).get("topic") or "概述"
        degraded_count = 0
        for i, (stage, stage_idx) in enumerate(stages):
            order = i + 1
            title = stage.get("name") or topic
            yield _sse("unit_start", {"order": order, "title": title, "total": total})
            unit: StudyUnitSpec | None = None
            if provider is not None:
                inp = ToolInput(
                    tool="generate_unit_content",
                    session_id=str(session_id),
                    args={
                        "plan": content,
                        "stage": stage,
                        "order": order,
                        "total": total,
                        "depth": body.depth,
                    },
                )
                task = asyncio.ensure_future(run_tool("generate_unit_content", inp, ctx))
                try:
                    while not task.done():
                        await asyncio.wait({task}, timeout=_HEARTBEAT_SECONDS)
                        if not task.done():
                            yield ": keepalive\n\n"  # 长 LLM 调用期间保活（plan/09 §十九）
                    out = task.result()
                except BaseException:
                    task.cancel()  # 断连/取消 -> 不遗留后台 LLM 调用
                    raise
                # err.md A6：逐单元成本纳入预算守卫与 api_usage 计量
                if ctx.budget:
                    ctx.budget.add(out.metadata.cost)
                record_metadata_usage(db, out.metadata, user_id)
                if ctx.budget and ctx.budget.exceeded():
                    log.warning("材料生成预算超限 plan=%s spent=%.4f", plan_id, ctx.budget.spent)
                    yield _sse("error", {"code": "budget_exceeded", "message": "本次生成预算已用尽，已保留已生成单元"})
                    return
                if out.status == "ok":
                    data = UnitContentSpec.model_validate(out.data)
                    unit = StudyUnitSpec(
                        title=title,
                        objectives=data.objectives,
                        prerequisites=data.prerequisites,
                        estimated_time=data.estimated_time,
                        content_md=data.content_md,
                    )
            degraded = unit is None
            if degraded:
                degraded_count += 1
                unit = brief_unit_from_stage(stage, fallback_title=title)
            # 单元 WebSearch 补充资料（plan/07 §三 步骤 2，iter 48）：topic + 阶段名 + 首主题；
            # 无 key / 检索失败 -> []（不中断单元生成；内容侧由 LLM 标「无外部资料参考」）
            query_parts = [p for p in (topic, title) if p]
            first_topic = ((stage.get("topics") or [{}])[0] or {}).get("name") if stage else None
            if first_topic and first_topic not in query_parts:
                query_parts.append(first_topic)
            resources = await search_unit_resources(" ".join(query_parts), ctx)
            new_paths.append(_insert_study_unit(db, plan_id, session_id, i, unit, resources, stage_index=stage_idx))
            db.commit()  # 逐单元提交：断线保留已生成进度
            yield _sse("unit_done", {"order": order, "title": unit.title, "degraded": degraded})
        if body.scope is not None or body.unit_ids:
            _resequence_study_units(db, plan_id)  # 局部生成后全量重排 order
        all_units = (
            db.query(models.StudyUnit).filter_by(plan_id=plan_id).order_by(models.StudyUnit.order).all()
        )
        _write_materials_index(session_id, content, all_units)  # plan/02 §13 index 文档
        db.commit()
        _cleanup_stale_material_files(old_paths, new_paths)
        # 后端插件事件（API.md §8.5）：材料生成完成（全量/局部均广播；监听器异常隔离）
        from app.plugins import bus as plugin_bus

        plugin_bus.emit(
            "materials_completed",
            {
                "plan_id": plan_id,
                "session_id": session_id,
                "unit_count": total,
                "degraded_count": degraded_count,
                "partial": body.scope is not None or bool(body.unit_ids),
            },
        )
        yield _sse("done", {"unit_count": total, "degraded_count": degraded_count})
    except Exception:  # noqa: BLE001  统一兜底：流以 error 事件收尾而非裸断
        log.exception("材料流式生成异常: plan %s", plan_id)
        yield _sse("error", {"code": "internal_error", "message": "材料生成中断"})
    finally:
        db.close()


@router.post("/{plan_id}/materials")
async def generate_materials(
    plan_id: int,
    body: MaterialsIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """生成自学材料（内容协商，iter 46；plan/07 §五 SSE/异步）。

    - Accept: text/event-stream 且需生成（无已存单元或 refresh）-> SSE 逐单元流（_materials_sse）；
      旧单元在流开始前替换式删除并提交。
    - 否则 -> 同步 JSON：run_tool 生成 StudyMaterialsSpec（LLM，降级默认）-> 持久化 -> 渲染 markdown。

    幂等：已持久化且未 refresh -> 直接重渲染返回 JSON（免重复 LLM 成本；即使请求 SSE 也返回 JSON）。
    无 ANTHROPIC_API_KEY 或 LLM 失败 -> default/brief 纯 Python 兜底（稳定降级，同样落库）。
    """
    plan, content = _plan_or_404(db, plan_id, user)

    # iter 93：按单元重生成——解析选中单元（须属本方案），隐含强制重生成
    unit_rows: list[models.StudyUnit] = []
    if body.unit_ids:
        if body.scope is not None:
            raise ApiError("invalid_scope", "unit_ids 与 scope 互斥，请只用其一")
        unit_rows = (
            db.query(models.StudyUnit)
            .filter(
                models.StudyUnit.plan_id == plan.id,
                models.StudyUnit.id.in_(set(body.unit_ids)),
            )
            .order_by(models.StudyUnit.order)
            .all()
        )
        missing = set(body.unit_ids) - {u.id for u in unit_rows}
        if missing:
            raise ApiError("not_found", f"学习单元不存在: {sorted(missing)}", status=404)

    existing = db.query(models.StudyUnit).filter_by(plan_id=plan.id).order_by(models.StudyUnit.order).all()
    if existing and not body.refresh and not body.unit_ids:
        spec = _units_to_spec(db, plan, existing, content)
        return {
            "unit_count": len(spec.units),
            "markdown": render_materials_md(spec),
            "index_md": _read_materials_index(plan.session_id),
        }

    check_daily_budget(db, user.id)  # err.md A6：日预算守卫（仅新生成路径；幂等重渲染不耗 LLM）
    if body.unit_ids:
        picked = [(_stage_for_unit(content, u), u.stage_index) for u in unit_rows]
    else:
        picked = _resolve_scope_stages(content, body.scope)  # 422 invalid_scope

    if "text/event-stream" in request.headers.get("accept", ""):
        if body.unit_ids:
            # 按单元删除：仅删选中行（同 stage 的未选中单元不受影响）
            old_paths = [m.content_md_path for u in unit_rows for m in u.materials]
            for u in unit_rows:
                db.delete(u)  # cascade study_materials
            db.flush()
        else:
            # scope 局部生成仅删 scope 内单元（stage_index 集合）；全量生成删全部
            stage_set = {idx for _, idx in picked if idx is not None} if body.scope is not None else None
            old_paths = _delete_study_units(db, plan.id, stage_set)
        db.commit()  # 流开始前提交替换意图（断线亦不残留新旧混合）
        return StreamingResponse(
            _materials_sse(plan.id, plan.session_id, content, body, old_paths, user.id, picked),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    # 同步 JSON 路径：unit_ids 的逐单元替换语义无法由单文档规约表达 -> 明确 422（前端走 SSE）
    if body.unit_ids:
        raise ApiError("invalid_scope", "按单元重生成请使用 SSE 请求（Accept: text/event-stream）")

    try:
        provider = get_provider(user.id)
    except Exception:  # noqa: BLE001  无 key -> 降级默认
        provider = None

    ctx = ExecContext(
        session_id=str(plan.session_id),
        trace_id=f"materials-{plan_id}",
        registry=default_registry(),
        llm_provider=provider,
        data_dir=settings.data_dir,
        budget=BudgetTracker(limit=settings.budget_per_session_usd),  # err.md A6
    )
    # 同步 JSON 路径：scope 仅作用于 prompt（过滤阶段副本）；持久化仍为整档替换
    # （LLM 单文档规约无法可靠映射 stage_index；前端默认走 SSE 逐单元路径，scope 语义完整）
    prompt_content = content
    if body.scope is not None:
        prompt_content = {**content, "stages": [s for s, _ in picked]}
    inp = ToolInput(
        tool="generate_study_materials",
        session_id=str(plan.session_id),
        args={"plan_id": str(plan_id), "plan": prompt_content, "depth": body.depth},
    )
    spec_out = await run_tool("generate_study_materials", inp, ctx)
    record_metadata_usage(db, spec_out.metadata, user.id)  # err.md A6：计量
    # ok 用 LLM 规约；repaired/error 降级 default_materials_from_plan(plan)
    if spec_out.status == "ok":
        spec = StudyMaterialsSpec.model_validate(spec_out.data)
    else:
        spec = default_materials_from_plan(prompt_content)

    _persist_study_units(db, plan, spec, content)
    # 后端插件事件（API.md §8.5）：同步 JSON 生成路径同样广播（幂等重渲染不广播——
    # 无新生成；spec 级降级视为全部单元降级）
    from app.plugins import bus as plugin_bus

    plugin_bus.emit(
        "materials_completed",
        {
            "plan_id": plan.id,
            "session_id": plan.session_id,
            "unit_count": len(spec.units),
            "degraded_count": len(spec.units) if spec_out.status != "ok" else 0,
            "partial": body.scope is not None,
        },
    )
    md = render_materials_md(spec)
    result: dict = {
        "unit_count": len(spec.units),
        "markdown": md,
        "index_md": _read_materials_index(plan.session_id),
    }
    if spec_out.status != "ok":
        result["warning"] = "自学材料规约生成降级，使用默认结构"
    return result


@router.get("/{plan_id}/materials/export")
def export_materials_zip(
    plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> Response:
    """全案自学材料打包下载（iter 99，用户需求：整理好的学习材料能导出）：
    zip = 00-路线图.md（index 文档，如有）+ 每单元一个 md（按 order 编号）。
    单元内容经 read_unit_content 自动串联拆分子文件；无单元 -> 404。"""
    import io
    import zipfile
    from urllib.parse import quote

    plan, content = _plan_or_404(db, plan_id, user)
    units = (
        db.query(models.StudyUnit)
        .filter_by(plan_id=plan.id)
        .order_by(models.StudyUnit.order)
        .all()
    )
    if not units:
        raise ApiError("not_found", "该方案尚无自学材料，请先生成", status=404)

    topic = (content.get("intent") or {}).get("topic") or "学习方案"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        index_md = _read_materials_index(plan.session_id)
        if index_md:
            zf.writestr("00-路线图.md", index_md)
        for i, u in enumerate(units, 1):
            md = ""
            if u.materials:
                try:
                    md = read_unit_content(u.materials[0].content_md_path)  # 自动串联拆分子文件
                except OSError:
                    log.warning("导出材料：单元 %s 内容文件缺失", u.id)
            zf.writestr(
                f"{i:02d}-{_safe_download_name(u.title)}.md",
                md or f"# {u.title}\n\n（本单元暂无内容）",
            )
    filename = f"{_safe_download_name(topic)}-自学材料.zip"
    log.info("材料导出：plan=%s 单元=%d 大小=%.1fKB", plan_id, len(units), len(buf.getvalue()) / 1024)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        # RFC 5987：中文文件名经 filename* 百分号编码（UA 不支持时回退 ASCII 名）
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"materials-{plan_id}.zip\"; "
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


def _safe_download_name(name: str) -> str:
    """文件名清洗：剔除 Windows/zip 非法字符，限长 50。"""
    cleaned = "".join("_" if c in '\\/:*?"<>|' else c for c in name).strip(". ") or "材料"
    return cleaned[:50]


@router.get("/{plan_id}/materials")
def list_materials(
    plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """自学材料单元列表（plan/03 §一 GET /plans/{id}/materials）：元数据 + index 文档，不含单元内容。"""
    plan, _content = _plan_or_404(db, plan_id, user)
    units = (
        db.query(models.StudyUnit)
        .filter_by(plan_id=plan.id)
        .order_by(models.StudyUnit.order)
        .all()
    )
    return {
        "units": [
            {
                "id": u.id,
                "title": u.title,
                "objectives": u.objectives or [],
                "prerequisites": u.prerequisites or [],
                "estimated_time": u.estimated_time,
                "order": u.order,
                "stage_index": u.stage_index,
                "done": u.done_at is not None,  # 标记已学（plan/07 §六）
            }
            for u in units
        ],
        "index_md": _read_materials_index(plan.session_id),  # plan/02 §13 index 文档
    }


# --- 测验（P2，plan/02 §8；简化 MVP：同步一次 LLM 调用 -> JSON；iter 42 起 quizzes 表持久化）---


class QuizIn(BaseModel):
    refresh: bool = False  # True 强制重新生成（替换旧题，attempts 级联删）


def _quiz_type_safe(raw: str) -> models.QuizType:
    """err.md A3：LLM 输出的自由字符串题型非法时回退 choice（原直接 QuizType(...) 抛 ValueError -> 500）。"""
    try:
        return models.QuizType(raw)
    except ValueError:
        log.warning("非法测验题型 %r，回退 choice", raw)
        return models.QuizType.choice


def _persist_quizzes(db: Session, plan_id: int, spec: QuizSpec) -> list[models.Quiz]:
    """替换式持久化：删旧题（含 attempts，FK 约束需先删）-> 插新题。

    node_id 映射：payload 保留原始 sid 字符串（前端写回 /progress 用），
    列 node_id 存 knowledge_nodes.id（sid 查不到 -> None，不阻塞出题）。
    """
    # ORM 逐行删除：级联删 attempts（Quiz.attempts cascade=delete-orphan），
    # 且会话 identity map 同步移除（批量 delete 不同步会在 SQLite rowid 复用时撞 SAWarning）
    for old in db.query(models.Quiz).filter_by(plan_id=plan_id).all():
        db.delete(old)
    db.flush()
    sid_map = {n.sid: n.id for n in db.query(models.KnowledgeNode).filter_by(plan_id=plan_id)}
    rows = [
        models.Quiz(
            plan_id=plan_id,
            node_id=sid_map.get(q.node_id),
            type=_quiz_type_safe(q.type),
            payload=q.model_dump(),
        )
        for q in spec.quizzes
    ]
    db.add_all(rows)
    db.commit()
    for r in rows:
        db.refresh(r)
    return rows


def _quiz_result(rows: list[models.Quiz], content: dict, spec_title: str | None = None) -> dict:
    """quizzes 行 -> 响应（payload + DB id）。title：生成路径用 spec 标题；
    持久化重读路径由 intent 推算（title 不入库，与 default_quiz_from_plan 同式）。"""
    intent = content.get("intent") or {}
    title = spec_title or f"{intent.get('topic') or '学习方案'}测验"
    return {
        "title": title,
        "quiz_count": len(rows),
        "quizzes": [{**(r.payload or {}), "id": r.id} for r in rows],
    }


@router.post("/{plan_id}/quiz")
async def generate_quiz_endpoint(
    plan_id: int,
    body: QuizIn | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """生成测验：run_tool 生成 QuizSpec（LLM，降级默认）并持久化 quizzes 表，返回 JSON 供前端交互判分。

    幂等：已持久化且未指定 refresh -> 直接返回已存题（免重复 LLM 成本）。
    无 ANTHROPIC_API_KEY 或 LLM 失败 -> default_quiz_from_plan 纯 Python 默认（稳定兜底，同样落库）。
    """
    plan, content = _plan_or_404(db, plan_id, user)
    refresh = bool(body and body.refresh)

    existing = db.query(models.Quiz).filter_by(plan_id=plan.id).all()
    if existing and not refresh:
        return _quiz_result(existing, content)

    check_daily_budget(db, user.id)  # err.md A6：日预算守卫（仅新生成路径）

    try:
        provider = get_provider(user.id)
    except Exception:  # noqa: BLE001  无 key -> 降级默认
        provider = None

    ctx = ExecContext(
        session_id=str(plan.session_id),
        trace_id=f"quiz-{plan_id}",
        registry=default_registry(),
        llm_provider=provider,
        skill_registry=get_skill_registry() or None,  # iter 61：deep-tutor-quiz Skill（空 -> inline 回退）
        data_dir=settings.data_dir,
        budget=BudgetTracker(limit=settings.budget_per_session_usd),  # err.md A6
    )
    # quiz 题源联动（iter 49）：已持久化自学单元的「检查理解」自测题 -> 题源（无单元/无小节 -> 空，行为同前）
    unit_contents: list[tuple[str, str]] = []
    for u in (
        db.query(models.StudyUnit).filter_by(plan_id=plan.id).order_by(models.StudyUnit.order).all()
    ):
        if not u.materials:
            continue
        try:
            # read_unit_content 自动串联 .pN 拆分子文件——直接 read_text 只读主文件，
            # >500 行单元的「检查理解」小节（模板第 4/6 节，常落入拆分尾部）会被静默漏掉
            unit_contents.append((u.title, read_unit_content(u.materials[0].content_md_path)))
        except OSError:
            log.warning("读取单元内容文件失败: %s", u.materials[0].content_md_path)
    check_questions = extract_check_questions(unit_contents)
    inp = ToolInput(
        tool="generate_quiz",
        session_id=str(plan.session_id),
        args={
            "plan_id": str(plan_id),
            "plan": content,
            **({"unit_check_questions": check_questions} if check_questions else {}),
        },
    )
    spec_out = await run_tool("generate_quiz", inp, ctx)
    record_metadata_usage(db, spec_out.metadata, user.id)  # err.md A6：计量
    # ok 用 LLM 规约；repaired/error 降级 default_quiz_from_plan(plan)
    if spec_out.status == "ok":
        spec = QuizSpec.model_validate(spec_out.data)
    else:
        spec = default_quiz_from_plan(content)

    rows = _persist_quizzes(db, plan.id, spec)
    result = _quiz_result(rows, content, spec_title=spec.title)
    if spec_out.status != "ok":
        result["warning"] = "测验题生成降级，使用默认自测题"
    return result


@router.get("/{plan_id}/quiz/wrong")
def wrong_quizzes(
    plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """错题重练（iter 114）：筛出最近一次作答判错的题原题重练，零 LLM 成本。

    判定：每题取最新一条 QuizAttempt（created_at 倒序，同刻 id 大者优先），
    correct IS False 计入；自评题（fill/short 判分 correct=None）与从未作答的题不计。
    无错题 -> quiz_count=0（前端展示空态）。响应形状同 POST /quiz（_quiz_result）。
    """
    plan, content = _plan_or_404(db, plan_id, user)
    return _quiz_result(_wrong_quiz_rows(db, plan), content, spec_title=_wrong_quiz_title(content))


def _wrong_quiz_rows(db: Session, plan: models.Plan) -> list[models.Quiz]:
    """错题行（iter 114 判定逻辑，iter 134 抽出供导出复用）：每题最新作答判错 -> 原题。"""
    quizzes = db.query(models.Quiz).filter_by(plan_id=plan.id).all()
    if not quizzes:
        return []
    # 单查询取全部作答，按 quiz 分组后组内首条即最新（避免逐题 N+1）
    attempts = (
        db.query(models.QuizAttempt)
        .join(models.Quiz, models.QuizAttempt.quiz_id == models.Quiz.id)
        .filter(models.Quiz.plan_id == plan.id)
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
    return [
        q
        for q in quizzes
        if (la := latest_by_quiz.get(q.id)) is not None and la.correct is False
    ]


def _wrong_quiz_title(content: dict) -> str:
    return f"{(content.get('intent') or {}).get('topic') or '学习方案'}错题重练"


@router.get("/{plan_id}/quiz/stats")
def quiz_stats(
    plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """测验历史统计（iter 152）：题数 / 作答次数 / 判分正确率 + 当前错题数。

    accuracy 口径：correct IS NOT NULL 的作答中 correct IS True 占比（自评题 None 不计）；
    无任何判分作答 -> None（前端不显示）。wrong_count 与 GET quiz/wrong 同判定。
    """
    plan, _content = _plan_or_404(db, plan_id, user)
    quiz_count = (
        db.query(func.count(models.Quiz.id)).filter_by(plan_id=plan.id).scalar() or 0
    )
    attempts, graded, correct = (
        db.query(
            func.count(models.QuizAttempt.id),
            func.count(models.QuizAttempt.correct),  # count 跳过 NULL
            func.coalesce(
                func.sum(case((models.QuizAttempt.correct.is_(True), 1), else_=0)), 0
            ),
        )
        .join(models.Quiz, models.QuizAttempt.quiz_id == models.Quiz.id)
        .filter(models.Quiz.plan_id == plan.id)
        .one()
    )
    return {
        "quiz_count": int(quiz_count),
        "attempts": int(attempts),
        "graded": int(graded),
        "correct": int(correct),
        "accuracy": round(correct / graded, 4) if graded else None,
        "wrong_count": len(_wrong_quiz_rows(db, plan)),
    }

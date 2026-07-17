"""plans API（plan/03 §一 plans；SSE 生成 + 列表 + 详情）。

POST /api/plans/generate  SSE 流式生成（编排器 + 事件回调 -> SSE）
GET  /api/plans           列表（分页）
GET  /api/plans/{id}      详情

稳定性：运行态冲突 409（R33）、预算守卫、错误/取消传播、plan 持久化（大方案溢出文件）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent import PIPELINE, BudgetTracker, ExecContext, Orchestrator
from app.api.deps import get_db, require_auth
from app.api.errors import ApiError
from app.core.settings import settings
from app.db import models
from app.db.base import SessionLocal
from app.llm import get_provider
from app.tools import default_registry
from app.tools.base import ToolOutput

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
_MAX_BUFFERED_EVENTS = 500  # 单会话最大缓冲；超限丢最旧（降级重放）
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
                    text = p.read_text(encoding="utf-8")
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
        return "error", {
            "code": "previous_failed",
            "message": "上次生成未完成，请返回首页重新生成",
            "stage": "",
        }
    return None


async def _terminal_stream(event: str, data: dict) -> str:
    """终态快路径：仅发一个事件即关闭（不重放历史，plan/10 §十二 done/error 重连直接返回终态）。"""
    yield _sse(event, data, event_id=1)


@router.post("/generate")
def generate(body: GenerateIn, request: Request, db: Session = Depends(get_db)) -> StreamingResponse:
    sess = db.get(models.Session, body.session_id)
    if sess is None:
        raise ApiError("not_found", f"会话 {body.session_id} 不存在")

    state = db.get(models.OrchestratorState, body.session_id)
    # 运行中：有活动流 -> 续接（重放 + 实时，带 Last-Event-ID）；无活动流（进程重启等 stale）-> 409
    if state is not None and state.status == models.OrchestratorStatus.running:
        stream = _STREAMS.get(body.session_id)
        if stream is not None and stream.terminal_id is None:
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

    parsed = _load_parsed_materials(db, body.session_id)
    concatenated = "\n\n".join(p["text"] for p in parsed if p["text"])
    initial_upstream: dict[str, ToolOutput] = {}
    if concatenated:
        chunks = [concatenated[i : i + 3000] for i in range(0, len(concatenated), 3000)]
        initial_upstream["parse_material"] = ToolOutput(
            tool="parse_material",
            status="ok",
            data={"text": concatenated, "meta": {"source_type": "materials", "pages": len(parsed)}, "chunks": chunks},
        )
    args_by_tool = {"understand_intent": {"raw_text": body.raw_text, "parsed": parsed}}
    return StreamingResponse(
        _run_start(body.session_id, args_by_tool, initial_upstream, _parse_last_event_id(request)),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/{session_id}/clarify")
def clarify(session_id: int, body: ClarifyIn, db: Session = Depends(get_db)) -> StreamingResponse:
    """澄清续接（plan/10 §四）：paused_clarify 状态下以 answers 重跑 understand_intent -> 继续 pipeline。"""
    state = db.get(models.OrchestratorState, session_id)
    if state is None or state.status != models.OrchestratorStatus.paused_clarify:
        raise ApiError("conflict", "该会话无待澄清任务", status=409)
    partial = state.partial_plan or {}
    outputs_dump = partial.get("outputs", {})
    initial_upstream = {
        tool: ToolOutput(tool=tool, status=d.get("status", "ok"), data=d.get("data", {}))
        for tool, d in outputs_dump.items()
    }
    raw_text = partial.get("raw_text", "")
    parsed = _load_parsed_materials(db, session_id)
    args_by_tool = {"understand_intent": {"raw_text": raw_text, "parsed": parsed, "answers": body.answers}}
    return StreamingResponse(
        _run_start(session_id, args_by_tool, initial_upstream, 0),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/{session_id}/cancel")
async def cancel(session_id: int, db: Session = Depends(get_db)) -> dict:
    """取消运行中的生成（plan/10 §十一）。

    取消活动流的编排器任务 -> done-callback（`_finalize`）发 `error{code:cancelled}`
    并置 error 态；连接中的客户端经 SSE 收到该事件。无运行任务 -> 409。
    """
    sess = db.get(models.Session, session_id)
    if sess is None:
        raise ApiError("not_found", f"会话 {session_id} 不存在")
    stream = _STREAMS.get(session_id)
    if stream is None or stream.task is None or stream.terminal_id is not None:
        raise ApiError("conflict", "该会话无运行中的生成任务", status=409)
    stream.task.cancel()  # 同事件循环内，安全
    return {"ok": True, "status": "cancelling"}


@router.post("/{plan_id}/regenerate")
def regenerate(
    plan_id: int, body: RegenIn, request: Request, db: Session = Depends(get_db)
) -> StreamingResponse:
    """局部重生成（plan/10 §十三）：从 scope 起级联重跑其下游，feedback 注入 start_tool。

    - scope：PIPELINE 中 understand_intent 之后的工具名（其上游从 tool_calls 重建）。
    - plans.version += 1（_persist_plan 自动递增）。
    """
    plan = db.get(models.Plan, plan_id)
    if plan is None:
        raise ApiError("not_found", f"方案 {plan_id} 不存在")
    session_id = plan.session_id
    state = db.get(models.OrchestratorState, session_id)
    if state is not None and state.status == models.OrchestratorStatus.running:
        raise ApiError("conflict", "该会话已有生成任务运行中", status=409)

    if body.scope not in PIPELINE:
        raise ApiError("bad_request", f"scope 必须为有效工具名: {PIPELINE}", status=400)
    start_tool = body.scope
    initial_upstream = _reconstruct_upstream(db, session_id, start_tool)
    # understand_intent 须在上游（否则需原始 raw_text，不支持）
    if "understand_intent" not in initial_upstream:
        raise ApiError(
            "bad_request",
            f"不支持从 {start_tool} 重生成（需 understand_intent 上游；请从 design_stages 及之后起）",
            status=400,
        )
    args_by_tool = {start_tool: {"fixes": [body.feedback]} if body.feedback else {}}
    return StreamingResponse(
        _run_start(session_id, args_by_tool, initial_upstream, _parse_last_event_id(request)),
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
        if task.cancelled():
            _set_state(db, session_id, models.OrchestratorStatus.error, stream.trace_id, last_event_id=str(stream.seq))
            eid = stream.publish("error", {"code": "cancelled", "message": "生成已取消", "stage": ""})
            stream.mark_terminal(eid)
            return
        try:
            result = task.result()
        except Exception as exc:  # noqa: BLE001
            log.exception("orchestrator 任务异常: %s", exc)
            _set_state(db, session_id, models.OrchestratorStatus.error, stream.trace_id, last_event_id=str(stream.seq))
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
            _set_state(db, session_id, models.OrchestratorStatus.done, stream.trace_id, last_event_id=str(stream.seq))
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
            )
            # clarify_needed 已由编排器 emit（stream.seq 即其 id）；标记终态使流结束
            stream.mark_terminal(stream.seq)
        else:
            msg = result.errors[-1] if result.errors else "生成失败"
            data = {"code": "generation_failed", "message": msg, "stage": ""}
            eid = stream.publish("error", data)
            stream.mark_terminal(eid)
            _set_state(db, session_id, models.OrchestratorStatus.error, stream.trace_id, last_event_id=str(stream.seq))
    except Exception:  # noqa: BLE001  持久化失败不阻塞流；兜底发终态防挂死
        log.exception("finalize 异常 session=%s", session_id)
        if stream.terminal_id is None:
            eid = stream.publish("error", {"code": "internal_error", "message": "生成失败", "stage": ""})
            stream.mark_terminal(eid)
    finally:
        db.close()
        _schedule_cleanup(session_id)


async def _run_start(
    session_id: int, args_by_tool: dict, initial_upstream: dict, last_event_id: int = 0
) -> str:
    """启动一次生成：创建会话流 + 编排器任务（done-callback 持久化）+ 续接投递。"""
    trace_id = uuid.uuid4().hex
    stream = _SessionStream(session_id, trace_id)
    stream.raw_text = args_by_tool.get("understand_intent", {}).get("raw_text", "")
    _STREAMS[session_id] = stream
    db = SessionLocal()
    _set_state(db, session_id, models.OrchestratorStatus.running, trace_id)
    db.close()

    # 无 LLM Provider -> 发 error 终态（不启动任务）
    try:
        provider = get_provider()
    except Exception as exc:  # noqa: BLE001
        eid = stream.publish("error", {"code": "no_llm_provider", "message": str(exc), "stage": ""})
        stream.mark_terminal(eid)
        db2 = SessionLocal()
        _set_state(db2, session_id, models.OrchestratorStatus.error, trace_id, last_event_id=str(stream.seq))
        db2.close()
        _schedule_cleanup(session_id)
        async for chunk in _relay(stream, last_event_id):
            yield chunk
        return

    budget = BudgetTracker(limit=settings.budget_per_session_usd)
    stream.budget = budget
    registry = default_registry()
    ctx = ExecContext(
        session_id=str(session_id),
        trace_id=trace_id,
        registry=registry,
        llm_provider=provider,
        budget=budget,
    )
    orch = Orchestrator(
        registry, ctx, event_cb=lambda e, d: stream.publish(e, d), max_tool_calls=settings.max_tool_calls
    )
    task = asyncio.create_task(
        orch.run(args_by_tool, start_tool="understand_intent", initial_upstream=initial_upstream)
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
    db.commit()


def _persist_tool_calls(db: Session, session_id: int, trace_id: str, outputs: dict[str, ToolOutput]) -> None:
    """持久化 tool_calls + api_usage（成本计量 Pillar 3，plan/01 §三）。批量写，失败不阻塞。"""
    try:
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
    db.commit()
    db.refresh(plan)
    return plan.id


# --- 列表 / 详情 ---


class PlanSummary(BaseModel):
    id: int
    session_id: int
    version: int
    created_at: str


@router.get("")
def list_plans(
    session_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    q = db.query(models.Plan)
    if session_id is not None:
        q = q.filter_by(session_id=session_id)
    total = q.count()
    items = q.order_by(models.Plan.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {
        "items": [PlanSummary(id=p.id, session_id=p.session_id, version=p.version, created_at=str(p.created_at)).model_dump() for p in items],
        "total": total,
    }


@router.get("/{plan_id}")
def get_plan(plan_id: int, db: Session = Depends(get_db)) -> dict:
    plan = db.get(models.Plan, plan_id)
    if plan is None:
        raise ApiError("not_found", f"方案 {plan_id} 不存在")
    content = plan.content
    if not content and plan.content_path:
        try:
            content = json.loads(Path(plan.content_path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            content = {}
    return {"id": plan.id, "session_id": plan.session_id, "version": plan.version, "plan": content}

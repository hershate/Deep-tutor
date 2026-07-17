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

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent import BudgetTracker, ExecContext, Orchestrator
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


class GenerateIn(BaseModel):
    session_id: int
    raw_text: str = ""


class ClarifyIn(BaseModel):
    answers: dict = Field(default_factory=dict)


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
def generate(body: GenerateIn, db: Session = Depends(get_db)) -> StreamingResponse:
    sess = db.get(models.Session, body.session_id)
    if sess is None:
        raise ApiError("not_found", f"会话 {body.session_id} 不存在")
    # 运行态冲突（R33）
    state = db.get(models.OrchestratorState, body.session_id)
    if state and state.status == models.OrchestratorStatus.running:
        raise ApiError("conflict", "该会话已有生成任务运行中", status=409)

    # 终态快路径（plan/10 §十二）：done/paused_clarify 不重跑，直接回放终态事件，
    # 避免页面刷新/断线重连触发重复生成。
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
        _stream(body.session_id, args_by_tool, initial_upstream),
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
        _stream(session_id, args_by_tool, initial_upstream),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


async def _stream(session_id: int, args_by_tool: dict, initial_upstream: dict) -> str:
    db = SessionLocal()
    trace_id = uuid.uuid4().hex
    _set_state(db, session_id, models.OrchestratorStatus.running, trace_id)
    queue: asyncio.Queue = asyncio.Queue()

    def cb(event: str, data: dict) -> None:
        queue.put_nowait((event, data))

    # SSE 事件 id（递增），plan/10 §十二 断线续接游标；前端可带 Last-Event-ID 续接。
    seq = 0

    def emit(event: str, data: dict) -> str:
        nonlocal seq
        seq += 1
        return _sse(event, data, event_id=seq)

    # 构造 LLM Provider（无 key -> 发 error SSE 并结束）
    try:
        provider = get_provider()
    except Exception as exc:  # noqa: BLE001
        yield emit("error", {"code": "no_llm_provider", "message": str(exc), "stage": ""})
        _set_state(db, session_id, models.OrchestratorStatus.error, trace_id, last_event_id=str(seq))
        db.close()
        return

    budget = BudgetTracker(limit=settings.budget_per_session_usd)
    registry = default_registry()
    ctx = ExecContext(
        session_id=str(session_id),
        trace_id=trace_id,
        registry=registry,
        llm_provider=provider,
        budget=budget,
    )
    orch = Orchestrator(registry, ctx, event_cb=cb, max_tool_calls=settings.max_tool_calls)

    task = asyncio.create_task(
        orch.run(args_by_tool, start_tool="understand_intent", initial_upstream=initial_upstream)
    )
    try:
        last_send = time.monotonic()
        while True:
            if task.done():
                while not queue.empty():
                    e, d = queue.get_nowait()
                    yield emit(e, d)
                    last_send = time.monotonic()
                break
            try:
                e, d = await asyncio.wait_for(queue.get(), timeout=0.2)
                yield emit(e, d)
                last_send = time.monotonic()
            except asyncio.TimeoutError:
                # 心跳：长 LLM 调用期间防 nginx/代理/浏览器空闲断连
                if time.monotonic() - last_send > _HEARTBEAT_SECONDS:
                    yield ": keepalive\n\n"
                    last_send = time.monotonic()
                continue

        result = await task
        if result.status == "done":
            _persist_tool_calls(db, session_id, trace_id, result.outputs)
            plan_id = _persist_plan(db, session_id, result.plan)
            yield emit(
                "done",
                {"plan_id": plan_id, "metadata": {"tools": len(result.outputs), "cost": round(budget.spent, 6)}},
            )
            _set_state(db, session_id, models.OrchestratorStatus.done, trace_id, last_event_id=str(seq))
        elif result.status == "paused_clarify":
            _persist_tool_calls(db, session_id, trace_id, result.outputs)
            raw_text = args_by_tool.get("understand_intent", {}).get("raw_text", "")
            partial = {
                "raw_text": raw_text,
                "outputs": {t: {"status": o.status, "data": o.data} for t, o in result.outputs.items()},
            }
            _set_state(db, session_id, models.OrchestratorStatus.paused_clarify, trace_id, partial=partial, last_event_id=str(seq))
            # clarify_needed 已由编排器 emit
        else:
            msg = result.errors[-1] if result.errors else "生成失败"
            yield emit("error", {"code": "generation_failed", "message": msg, "stage": ""})
            _set_state(db, session_id, models.OrchestratorStatus.error, trace_id, last_event_id=str(seq))
    except Exception as exc:  # noqa: BLE001
        log.exception("generate 流异常: %s", exc)
        yield emit("error", {"code": "internal_error", "message": "生成失败", "stage": ""})
        _set_state(db, session_id, models.OrchestratorStatus.error, trace_id, last_event_id=str(seq))
    finally:
        db.close()


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
    plan = models.Plan(session_id=session_id, content=plan_dict, version=1)
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

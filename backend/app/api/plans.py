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


class GenerateIn(BaseModel):
    session_id: int
    raw_text: str = ""


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


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


@router.post("/generate")
def generate(body: GenerateIn, db: Session = Depends(get_db)) -> StreamingResponse:
    sess = db.get(models.Session, body.session_id)
    if sess is None:
        raise ApiError("not_found", f"会话 {body.session_id} 不存在")
    # 运行态冲突（R33）
    state = db.get(models.OrchestratorState, body.session_id)
    if state and state.status == models.OrchestratorStatus.running:
        raise ApiError("conflict", "该会话已有生成任务运行中", status=409)

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


async def _stream(session_id: int, args_by_tool: dict, initial_upstream: dict) -> str:
    db = SessionLocal()
    trace_id = uuid.uuid4().hex
    _set_state(db, session_id, models.OrchestratorStatus.running, trace_id)
    queue: asyncio.Queue = asyncio.Queue()

    def cb(event: str, data: dict) -> None:
        queue.put_nowait((event, data))

    # 构造 LLM Provider（无 key -> 发 error SSE 并结束）
    try:
        provider = get_provider()
    except Exception as exc:  # noqa: BLE001
        yield _sse("error", {"code": "no_llm_provider", "message": str(exc), "stage": ""})
        _set_state(db, session_id, models.OrchestratorStatus.error, trace_id)
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
        while True:
            if task.done():
                while not queue.empty():
                    e, d = queue.get_nowait()
                    yield _sse(e, d)
                break
            try:
                e, d = await asyncio.wait_for(queue.get(), timeout=0.2)
                yield _sse(e, d)
            except asyncio.TimeoutError:
                continue

        result = await task
        if result.status == "done":
            plan_id = _persist_plan(db, session_id, result.plan)
            yield _sse(
                "done",
                {"plan_id": plan_id, "metadata": {"tools": len(result.outputs), "cost": round(budget.spent, 6)}},
            )
            _set_state(db, session_id, models.OrchestratorStatus.done, trace_id)
        elif result.status == "paused_clarify":
            _set_state(db, session_id, models.OrchestratorStatus.paused_clarify, trace_id)
            # clarify_needed 已由编排器 emit
        else:
            msg = result.errors[-1] if result.errors else "生成失败"
            yield _sse("error", {"code": "generation_failed", "message": msg, "stage": ""})
            _set_state(db, session_id, models.OrchestratorStatus.error, trace_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("generate 流异常: %s", exc)
        yield _sse("error", {"code": "internal_error", "message": "生成失败", "stage": ""})
        _set_state(db, session_id, models.OrchestratorStatus.error, trace_id)
    finally:
        db.close()


def _set_state(db: Session, session_id: int, status: models.OrchestratorStatus, trace_id: str) -> None:
    state = db.get(models.OrchestratorState, session_id)
    if state is None:
        state = models.OrchestratorState(session_id=session_id, status=status, trace_id=trace_id)
        db.add(state)
    else:
        state.status = status
        state.trace_id = trace_id
    db.commit()


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

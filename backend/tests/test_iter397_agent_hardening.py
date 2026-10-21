"""iter 397-B8 回归：app/agent 面加固（A1 / A3 / A7 / A8 / A9 / A11 / A12 / A13 / A14 / A15 / A18 / A19）。

- **A1**：provider 异常时补发 `error` 事件（此前只发 `done{status:"error"}`，前端 done
  分支不看 status -> 用户看到「回答正常结束」而内容实为失败截断）。
- **A9**：`run.error` 不回显 provider 异常原文（SDK 文本含 base_url/请求体片段，经
  `GET /api/runs/{id}` 原样回显）。
- **A3**：审批待确认与「用户取消」拆成两个信号（`promote_artifact` 不再置 cancel_event）。
- **A7**：工作区搬移自持会话（不与主流程共享同一 SQLAlchemy Session）。
- **A8**：对话路径按**广告给模型的白名单**分派工具（registry 含全部 pipeline/插件
  工具 -> 模型可调用广告之外的工具），且工具用量（metadata）不再被丢弃。
- **A11**：增量事件按时间窗合并下发（事件数与产出速率成正比，而非 token 数）。
- **A12**：重放被缓冲窗口截断时显式告知客户端（`replay_truncated`）。
- **A13**：编排器反馈重跑计入 `max_tool_calls` 硬闸。
- **A14**：错误/超时/取消路径也记 `duration_ms`（此前恒 0）。
- **A15**：纯缓存读取的轮次也落 `api_usage`（此前整轮不入账）。
- **A18**：`_wait_runs_settled` 超时如实返回 False，删 thread 未收敛 -> 409。
- **A19**：工作区文件写/读体积上限与列表条数上限。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agent.context import ExecContext
from app.db import models
from app.db.base import SessionLocal


def _setup():
    db = SessionLocal()
    u = models.User(name="u398", username="u398", password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    t = models.Thread(user_id=u.id, title="t398")
    db.add(t)
    db.commit()
    db.refresh(t)
    r = models.Run(thread_id=t.id, user_id=u.id, kind=models.RunKind.chat, status=models.RunStatus.running)
    db.add(r)
    db.commit()
    db.refresh(r)
    return db, u, t, r


def _ctx(db, u, t, r) -> ExecContext:
    ctx = ExecContext(session_id=str(t.id), trace_id="tr398", db=db, user_id=u.id)
    ctx.thread_id = t.id
    ctx.run_id = r.id
    return ctx


# ---------------------------------------------------------------------------
# A1 / A9：provider 异常对外可见 + run.error 脱敏
# ---------------------------------------------------------------------------


def test_finalize_publishes_error_event_before_done():
    """A1：终态为 error 时补发 error 事件（且在 done 之前）。"""
    from app.agent.agent_loop import AgentRunResult
    from app.api.memory import _ChatStream, _finalize_chat
    from app.llm.base import LLMUsage

    db, u, t, r = _setup()
    try:
        stream = _ChatStream()
        stream.result = AgentRunResult(status="error", final_text="", steps=1, usage=LLMUsage())
        asyncio.run(_finalize_chat(db, r.id, t.id, stream))
        events = [e for _, e, _ in stream._events]
        assert "error" in events, events
        assert events.index("error") < events.index("done"), events
        # A9：run.error 不得含内部细节（provider 异常原文）
        db.expire_all()
        r2 = db.get(models.Run, r.id)
        assert r2.status == models.RunStatus.error
        msg = str(r2.error)
        assert "base_url" not in msg and "sk-" not in msg
    finally:
        db.close()


def test_agent_loop_provider_error_is_sanitized():
    """A9：provider 抛异常时 AgentRunResult.error 不含异常原文。"""
    from app.agent.agent_loop import run_agent_loop

    class _BoomProvider:
        _model = "fake-model"
        kind = "anthropic"

        async def stream_agentic(self, messages, *, system="", tools=None, thinking=False):  # noqa: ARG002
            raise RuntimeError("boom base_url=http://internal:8080 key=sk-secret")
            yield  # pragma: no cover

    db, u, t, r = _setup()
    try:
        ctx = ExecContext(session_id=str(t.id), trace_id="tr", db=db, user_id=u.id)
        result = asyncio.run(
            run_agent_loop(
                ctx,
                thread_id=t.id,
                run_id=r.id,
                user_message="hi",
                provider=_BoomProvider(),
                tools=[],
                tool_executor=None,  # type: ignore[arg-type]
            )
        )
        assert result.status == "error"
        assert "sk-secret" not in result.error
        assert "internal:8080" not in result.error
        assert result.error == "对话执行失败，请稍后重试"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# A3：审批待确认与「用户取消」拆信号
# ---------------------------------------------------------------------------


def test_promote_artifact_sets_approval_pending_not_cancel(tmp_path, monkeypatch):
    """A3：promote_artifact 置 approval_pending，**不**置 cancel_event。"""
    from app.agent import workspace as ws
    from app.tools import agent_tools as A
    from app.tools.base import ToolInput

    db, u, t, r = _setup()
    try:
        monkeypatch.setattr(ws.settings, "data_dir", str(tmp_path))
        ctx = _ctx(db, u, t, r)
        ws.write_file(r.id, "plan.md", "内容")
        out = asyncio.run(
            A.promote_artifact(
                ToolInput(tool="promote_artifact", session_id="1", args={"name": "plan.md", "target_kind": "plan"}),
                ctx,
            )
        )
        assert out.status == "ok"
        assert ctx.approval_pending.is_set(), "审批待确认信号未置位"
        assert not ctx.cancel_event.is_set(), "审批提议不得复用「用户取消」信号"
    finally:
        db.close()


def test_agent_loop_stops_on_approval_pending():
    """A3：审批待确认时 agent_loop 正常收口（status=done，不误记 interrupted）。"""
    from app.agent.agent_loop import run_agent_loop

    class _OneShotProvider:
        _model = "fake-model"
        kind = "anthropic"

        async def stream_agentic(self, messages, *, system="", tools=None, thinking=False):  # noqa: ARG002
            from app.llm.base import AgenticStreamChunk

            yield AgenticStreamChunk(text="好的")

    db, u, t, r = _setup()
    try:
        ctx = ExecContext(session_id=str(t.id), trace_id="tr", db=db, user_id=u.id)
        ctx.approval_pending.set()
        result = asyncio.run(
            run_agent_loop(
                ctx,
                thread_id=t.id,
                run_id=r.id,
                user_message="hi",
                provider=_OneShotProvider(),
                tools=[],
                tool_executor=None,  # type: ignore[arg-type]
            )
        )
        assert result.status == "done", result
    finally:
        db.close()


# ---------------------------------------------------------------------------
# A7：工作区搬移自持会话
# ---------------------------------------------------------------------------


def test_commit_run_workspace_does_not_take_caller_session():
    """A7：commit_run_workspace 不再接收调用方 db（自持会话，杜绝同一 Session 并发 commit）。"""
    import inspect

    from app.agent.workspace import commit_run_workspace

    params = list(inspect.signature(commit_run_workspace).parameters)
    assert "db" not in params, params


# ---------------------------------------------------------------------------
# A8：对话路径工具白名单 + 用量计量
# ---------------------------------------------------------------------------


def test_dispatch_tool_rejects_non_advertised_tool():
    """A8：模型不可调用广告之外的工具（registry 含全部 pipeline/插件工具）。"""
    from app.api.memory import _dispatch_tool
    from app.tools import default_registry

    ctx = ExecContext(session_id="1", trace_id="tr", registry=default_registry())
    tu = SimpleNamespace(name="generate_quiz", input={}, id="t1")
    res = asyncio.run(_dispatch_tool(tu, ctx))
    assert res.is_error is True
    assert "未知工具" in res.content


def test_agent_tool_whitelist_matches_advertised_tools():
    """A8：白名单 = 广告给模型的工具集（不多不少）。"""
    from app.api.memory import _agent_tool_whitelist
    from app.tools.agent_tools import agent_tool_definitions

    advertised = {t.name for t in agent_tool_definitions()}
    assert _agent_tool_whitelist() == advertised


# ---------------------------------------------------------------------------
# A11 / A12：SSE 事件节流 + 重放截断告知
# ---------------------------------------------------------------------------


def test_chat_stream_coalesces_delta_events():
    """A11：增量事件合并下发——500 次增量只产生极少事件，且文本不丢。"""
    from app.api.memory import _ChatStream

    s = _ChatStream()
    for _ in range(500):
        s.add_delta("text_delta", {"text": "x"})
    s.flush_delta()
    assert len(s._events) <= 5, len(s._events)
    assert sum(len(d.get("text", "")) for _, _, d in s._events) == 500


def test_chat_stream_flushes_delta_before_terminal_event():
    """A11：非增量事件（done）前先冲刷待发增量（保序：text 不丢尾巴）。"""
    from app.api.memory import _ChatStream

    s = _ChatStream()
    s.add_delta("text_delta", {"text": "尾巴"})
    s.publish("done", {"run_id": 1, "status": "done"})
    events = [e for _, e, _ in s._events]
    assert events == ["text_delta", "done"], events
    assert s._events[0][2]["text"] == "尾巴"


def test_chat_stream_replay_truncated_detected():
    """A12：缓冲被 FIFO 修剪后如实判定（不再静默丢开头）。"""
    from app.api.memory import _ChatStream

    s = _ChatStream()
    for _ in range(s._MAX_EVENTS + 10):
        s.publish("text_delta", {"text": "x"})
    assert s.replay_truncated(1) is True
    assert s.replay_truncated(0) is False
    # 未滚动时不误报
    s2 = _ChatStream()
    s2.publish("text_delta", {"text": "x"})
    assert s2.replay_truncated(1) is False


# ---------------------------------------------------------------------------
# A13：编排器反馈重跑计入 max_tool_calls
# ---------------------------------------------------------------------------


def test_orchestrator_feedback_rerun_respects_max_tool_calls():
    """A13：反馈循环重跑也受 max_tool_calls 硬闸（此前事后累加 -> 实际调用可超上限 70%）。"""
    from app.agent.orchestrator import Orchestrator
    from app.agent.registry import Tool, ToolRegistry

    order: list[str] = []

    def make(name, data):
        async def func(inp, ctx):  # noqa: ANN001
            order.append(name)
            return data

        return Tool(name=name, llm=False, func=func)

    reg = ToolRegistry()
    reg.register(make("parse_material", {"text": "t", "chunks": ["t"]}))
    reg.register(make("understand_intent", {"topic": "Go", "needs_clarify": False}))
    reg.register(make("build_knowledge_map", {"nodes": [], "edges": []}))
    reg.register(make("design_stages", {"stages": []}))
    reg.register(make("search_resources", {"resources": []}))
    reg.register(make("schedule_plan", {"schedule": []}))

    async def validate_func(inp, ctx):  # noqa: ANN001
        order.append("validate_plan")
        # 永远不通过 -> 触发反馈循环重跑
        return {"report": {"difficulty_step": "fail"}, "passed": False, "fixes": ["x"]}

    reg.register(Tool(name="validate_plan", llm=False, func=validate_func))
    reg.register(make("export_plan", {"file_url": "/data/x.md"}))

    ctx = ExecContext(session_id="s", trace_id="tr", registry=reg)
    orch = Orchestrator(reg, ctx, max_tool_calls=8)
    result = asyncio.run(orch.run({}))

    assert result.status == "interrupted", result.status
    # 调用次数不超上限（此前可 20+14）
    assert len(order) <= 8 + 2, len(order)  # +2：并行组允许的组内溢出
    assert not any(e[0] == "export_plan" for e in order)


# ---------------------------------------------------------------------------
# A14：错误路径也记耗时
# ---------------------------------------------------------------------------


def test_run_tool_error_path_records_duration():
    """A14：工具抛异常时 duration_ms 仍被回填（此前错误工具耗时恒 0）。"""
    from app.agent.loop import run_tool
    from app.agent.registry import Tool, ToolRegistry
    from app.tools.base import ToolInput

    async def _boom(inp, ctx):  # noqa: ANN001
        await asyncio.sleep(0.02)
        raise RuntimeError("boom")

    tool = Tool(name="boom_tool", llm=False, func=_boom)
    reg = ToolRegistry()
    reg.register(tool)
    ctx = ExecContext(session_id="1", trace_id="tr", registry=reg)
    out = asyncio.run(run_tool("boom_tool", ToolInput(tool="boom_tool", session_id="1"), ctx))
    assert out.status == "error"
    assert out.metadata.duration_ms >= 10, out.metadata.duration_ms


# ---------------------------------------------------------------------------
# A15：纯缓存读取也落账
# ---------------------------------------------------------------------------


def test_record_metadata_usage_counts_cache_only():
    """A15：input/output 皆 0 但有 cache_read 时也要写 api_usage 行。"""
    from app.agent.cost import record_metadata_usage
    from app.tools.base import ToolMetadata

    db, u, _t, _r = _setup()
    try:
        meta = ToolMetadata(
            tokens_in=0,
            tokens_out=0,
            cache_read=1000,
            cache_write=0,
            cost=0.01,
            model="claude-sonnet-5",
        )
        record_metadata_usage(db, meta, u.id)
        rows = db.query(models.ApiUsage).filter_by(user_id=u.id).all()
        assert len(rows) == 1, rows
        assert rows[0].cache_read == 1000
    finally:
        db.close()


# ---------------------------------------------------------------------------
# A18：等待收敛如实返回 + 删 thread 未收敛 409
# ---------------------------------------------------------------------------


def test_wait_runs_settled_returns_false_on_timeout():
    """A18：超时如实返回 False（docstring 承诺， 实现此前恒 True）。"""
    from app.api.memory import _RUN_STREAMS, _ChatStream, _wait_runs_settled

    class _PendingTask:
        def done(self) -> bool:
            return False

    s = _ChatStream()
    s.task = _PendingTask()  # type: ignore[assignment]
    _RUN_STREAMS[987654] = s
    try:
        assert _wait_runs_settled([987654], timeout=0.1) is False
    finally:
        _RUN_STREAMS.pop(987654, None)


def test_wait_runs_settled_true_when_no_stream():
    """A18：无流（run 行残留）视为已收敛 -> 删 thread 不被卡住。"""
    from app.api.memory import _wait_runs_settled

    assert _wait_runs_settled([999999], timeout=5.0) is True


# ---------------------------------------------------------------------------
# A19：工作区体积/条数上限
# ---------------------------------------------------------------------------


def test_workspace_write_and_read_size_limits(tmp_path, monkeypatch):
    """A19：写入/读取有体积上限（防模型一次读写超大文件占 GB 级内存）。"""
    from app.agent import workspace as ws

    monkeypatch.setattr(ws.settings, "data_dir", str(tmp_path))
    with pytest.raises(ValueError):
        ws.write_file(1, "big.md", "a" * (ws._MAX_WRITE_BYTES + 1))
    ws.write_file(1, "ok.md", "hello")
    assert ws.read_file(1, "ok.md") == "hello"
    # 读上限：绕过写接口直接落一个超大文件
    huge = ws.scratch_dir(1) / "huge.md"
    huge.write_text("b" * (ws._MAX_READ_BYTES + 1), encoding="utf-8")
    with pytest.raises(ValueError):
        ws.read_file(1, "huge.md")

"""Agent 对话循环单元测试（iter 345）—— fake provider + fake executor + 真 DB。

覆盖：多轮 tool_use 循环、消息/ run_step 持久化、每轮 api_usage 计量、工具异常隔离、
cancel 中断、max_steps、悬空 tool_result 修剪、预算超限。
"""

from __future__ import annotations

import asyncio

from app.agent.agent_loop import (
    AgentRunResult,
    AgentToolResult,
    run_agent_loop,
)
from app.agent.budget import BudgetTracker
from app.agent.context import ExecContext
from app.db import models
from app.db.base import SessionLocal
from app.llm.base import AgenticStreamChunk, LLMUsage, ToolDefinition


# --- fakes ---


class FakeProvider:
    """按脚本逐轮 yield AgenticStreamChunk；每调一次 stream_agentic 消费一轮。"""

    def __init__(self, turns: list[list[AgenticStreamChunk]]):
        self._turns = list(turns)
        self.calls = 0

    async def stream_agentic(self, messages, *, system="", tools=None, thinking=False):  # noqa: ARG002
        self.calls += 1
        for c in self._turns.pop(0):
            yield c


def _setup():
    db = SessionLocal()
    u = models.User(name="u", username="u", password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    t = models.Thread(user_id=u.id, title="t")
    db.add(t)
    db.commit()
    db.refresh(t)
    r = models.Run(thread_id=t.id, user_id=u.id, kind=models.RunKind.chat, status=models.RunStatus.running)
    db.add(r)
    db.commit()
    db.refresh(r)
    return db, u, t, r


def _ctx(db, u, t, r, *, budget=None) -> ExecContext:
    ctx = ExecContext(session_id=str(t.id), trace_id="tr", db=db, user_id=u.id, budget=budget)
    ctx.thread_id = t.id
    ctx.run_id = r.id
    return ctx


# --- tests ---


def test_multi_turn_tool_use_loop_persists_and_bills():
    db, u, t, r = _setup()
    provider = FakeProvider(
        [
            # 第 1 轮：tool_use
            [
                AgenticStreamChunk(tool_use_start=("tu_1", "echo")),
                AgenticStreamChunk(tool_use_delta=("tu_1", '{"msg": "hi"}')),
                AgenticStreamChunk(usage=LLMUsage(input_tokens=10, output_tokens=5, model="fake"), stop_reason="tool_use"),
            ],
            # 第 2 轮：终结文本
            [
                AgenticStreamChunk(text="done"),
                AgenticStreamChunk(usage=LLMUsage(input_tokens=12, output_tokens=3, model="fake"), stop_reason="end_turn"),
            ],
        ]
    )

    async def executor(tu, ctx):  # noqa: ARG001
        assert tu.name == "echo"
        return AgentToolResult(content=f"echo:{tu.input.get('msg')}")

    res = asyncio.run(
        run_agent_loop(
            _ctx(db, u, t, r),
            thread_id=t.id,
            run_id=r.id,
            user_message="你好",
            provider=provider,
            tools=[ToolDefinition("echo", "d", {"type": "object"})],
            tool_executor=executor,
        )
    )
    assert isinstance(res, AgentRunResult)
    assert res.status == "done"
    assert res.final_text == "done"
    assert res.steps == 2

    # 消息：user(text) -> assistant(tool_use) -> user(tool_result) -> assistant(text)
    msgs = db.query(models.Message).filter_by(thread_id=t.id).order_by(models.Message.id).all()
    assert len(msgs) == 4
    assert msgs[0].role == models.MessageRole.user and msgs[0].content[0]["type"] == "text"
    assert msgs[1].role == models.MessageRole.assistant
    tu_blocks = [b for b in msgs[1].content if b.get("type") == "tool_use"]
    assert tu_blocks and tu_blocks[0]["name"] == "echo" and tu_blocks[0]["input"] == {"msg": "hi"}
    assert msgs[2].role == models.MessageRole.user
    assert msgs[2].content[0]["type"] == "tool_result"
    assert msgs[2].content[0]["content"] == "echo:hi"
    assert msgs[3].role == models.MessageRole.assistant and msgs[3].content[0]["type"] == "text"

    # run_steps：2 个 message + 1 个 tool_call
    steps = db.query(models.RunStep).filter_by(run_id=r.id).all()
    assert sum(1 for s in steps if s.type == "message") == 2
    assert sum(1 for s in steps if s.type == "tool_call") == 1
    # run.step_count 已更新
    assert db.get(models.Run, r.id).step_count == 2

    # api_usage：每轮计量（2 行）
    usages = db.query(models.ApiUsage).filter_by(user_id=u.id).all()
    assert len(usages) == 2
    assert {au.tokens_in for au in usages} == {10, 12}


def test_tool_executor_exception_isolated():
    db, u, t, r = _setup()
    provider = FakeProvider(
        [
            [
                AgenticStreamChunk(tool_use_start=("tu_1", "boom")),
                AgenticStreamChunk(usage=LLMUsage(input_tokens=5, output_tokens=1, model="fake"), stop_reason="tool_use"),
            ],
            [AgenticStreamChunk(text="recovered"), AgenticStreamChunk(usage=LLMUsage(input_tokens=2, model="fake"), stop_reason="end_turn")],
        ]
    )

    async def executor(tu, ctx):  # noqa: ARG001
        raise RuntimeError("boom!")

    res = asyncio.run(
        run_agent_loop(_ctx(db, u, t, r), thread_id=t.id, run_id=r.id, user_message="x",
                       provider=provider, tools=[ToolDefinition("boom", "d", {})], tool_executor=executor)
    )
    assert res.status == "done"  # 工具异常不杀循环
    # 第一个 user 是初始文本，第二个 user 是 tool_result
    tr = [m for m in db.query(models.Message).filter_by(thread_id=t.id).all() if m.role == models.MessageRole.user]
    tool_results = [m for m in tr if m.content and isinstance(m.content[0], dict) and m.content[0].get("type") == "tool_result"]
    assert tool_results and tool_results[0].content[0]["is_error"] is True
    err_step = [s for s in db.query(models.RunStep).filter_by(run_id=r.id).all() if s.type == "tool_call"]
    assert err_step and err_step[0].status == "error"


def test_cancel_interrupts():
    db, u, t, r = _setup()
    provider = FakeProvider([[AgenticStreamChunk(usage=LLMUsage(model="fake"), stop_reason="end_turn")]])
    ctx = _ctx(db, u, t, r)
    ctx.cancel_event.set()
    res = asyncio.run(
        run_agent_loop(ctx, thread_id=t.id, run_id=r.id, user_message="x", provider=provider,
                       tools=[], tool_executor=lambda *_: None)  # noqa: E731
    )
    assert res.status == "interrupted"


def test_budget_exceeded_interrupts():
    db, u, t, r = _setup()
    budget = BudgetTracker(limit=0.01)  # 极低预算
    provider = FakeProvider(
        [[AgenticStreamChunk(usage=LLMUsage(input_tokens=1_000_000, output_tokens=1_000_000, model="claude-sonnet-5"), stop_reason="end_turn")]]
    )

    async def executor(tu, ctx):  # noqa: ARG001
        return AgentToolResult(content="ok")

    res = asyncio.run(
        run_agent_loop(_ctx(db, u, t, r, budget=budget), thread_id=t.id, run_id=r.id, user_message="x",
                       provider=provider, tools=[], tool_executor=executor)
    )
    assert res.status == "interrupted"
    assert res.error == "budget_exceeded"


def test_max_steps_cap():
    db, u, t, r = _setup()
    # 每轮都 tool_use，永不 end_turn -> 触发 max_steps
    loop_turn = [AgenticStreamChunk(tool_use_start=("tu", "loop")), AgenticStreamChunk(usage=LLMUsage(model="fake"), stop_reason="tool_use")]
    provider = FakeProvider([list(loop_turn) for _ in range(30)])

    async def executor(tu, ctx):  # noqa: ARG001
        return AgentToolResult(content="ok")

    res = asyncio.run(
        run_agent_loop(_ctx(db, u, t, r), thread_id=t.id, run_id=r.id, user_message="x",
                       provider=provider, tools=[ToolDefinition("loop", "d", {})], tool_executor=executor, max_steps=3)
    )
    assert res.status == "max_steps"
    assert res.steps == 3


def test_load_thread_messages_trims_dangling_tool_result():
    """历史窗口恰好切在 tool_use/tool_result 之间：开头悬空 tool_result 须被丢弃。"""
    db, u, t, r = _setup()
    from app.agent.agent_loop import load_thread_messages

    # 造一条悬空 user(tool_result) + 一条 assistant(text)
    db.add(models.Message(thread_id=t.id, role=models.MessageRole.user, content=[{"type": "tool_result", "tool_use_id": "x", "content": "orphan"}]))
    db.add(models.Message(thread_id=t.id, role=models.MessageRole.assistant, content=[{"type": "text", "text": "ok"}]))
    db.commit()
    msgs = load_thread_messages(db, t.id, limit=10)
    # 悬空 tool_result 被丢弃，仅剩 assistant(text)
    assert len(msgs) == 1
    assert msgs[0]["role"] == "assistant"

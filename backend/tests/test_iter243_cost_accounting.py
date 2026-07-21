"""iter 243 成本计量闭环回归测试（日预算守卫只信 api_usage 每日求和，落账缺口=预算绕过）：

- loop.py：LLM 工具异常/超时出口携带已累积 usage（此前全零 -> 重试轮次成本隐形）
- orchestrator.py：executed 台账记录全部执行（含反馈循环被 outputs 覆盖的早轮）
- plans.py _persist_tool_calls：按执行落账 + 注入上游去重（重建上游不重复建行）
- anthropic_provider.py：AsyncAnthropic 按 (api_key, base_url) 缓存复用、跨事件循环重建、有界
"""

from __future__ import annotations

import asyncio

from app.agent import ExecContext, Orchestrator, Tool, ToolRegistry, run_tool
from app.agent.context import Timeouts
from app.db.base import SessionLocal
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.tools.base import ToolInput, ToolOutput


class _ScriptedProvider(LLMProvider):
    def __init__(self, script: list) -> None:
        self._script = list(script)

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True) -> LLMResponse:
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _resp(content: str, inp=100, out=50) -> LLMResponse:
    return LLMResponse(content=content, usage=LLMUsage(input_tokens=inp, output_tokens=out, model="claude-sonnet-5"))


def _ctx(provider, timeouts: Timeouts | None = None) -> ExecContext:
    reg = ToolRegistry()
    reg.register(Tool(name="understand_intent", llm=True, output_model=None))
    return ExecContext(
        session_id="s", trace_id="t", registry=reg, llm_provider=provider,
        timeouts=timeouts or Timeouts(),
    )


# --- loop：异常/超时出口携带已累积 usage ---


def test_llm_tool_exception_carries_accumulated_usage():
    # 第 1 轮：非法 JSON（usage 已产生）；第 2 轮：非瞬态异常 -> tool_exception
    provider = _ScriptedProvider([_resp("not json at all"), RuntimeError("HTTP 400 bad request")])
    out = asyncio.run(run_tool("understand_intent", ToolInput(tool="understand_intent", session_id="s"), _ctx(provider)))
    assert out.status == "error"
    assert out.errors[0].code == "tool_exception"
    assert out.metadata.tokens_in == 100  # 第 1 轮成本不丢（此前全零）
    assert out.metadata.tokens_out == 50
    assert out.metadata.model == "claude-sonnet-5"


def test_llm_tool_timeout_carries_accumulated_usage():
    async def _hang(system, user, *, model=None, max_tokens=4096, cache_system=True):
        await asyncio.sleep(10)
        return _resp("{}")

    class _HangAfterFirst(_ScriptedProvider):
        async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
            if self._script:
                return self._script.pop(0)
            return await _hang(system, user, model=model, max_tokens=max_tokens, cache_system=cache_system)

    provider = _HangAfterFirst([_resp("not json at all")])
    ctx = _ctx(provider, timeouts=Timeouts(llm=0.05))
    out = asyncio.run(run_tool("understand_intent", ToolInput(tool="understand_intent", session_id="s"), ctx))
    assert out.status == "error"
    assert out.errors[0].code == "upstream_timeout"
    assert out.metadata.tokens_in == 100  # 超时前已成功轮次的成本不丢
    assert out.metadata.tokens_out == 50


# --- orchestrator：executed 台账含反馈循环被覆盖轮次 ---


def test_orchestrator_executed_tracks_feedback_rerun_rounds():
    calls = {"design": 0, "validate": 0}

    async def design_func(inp, ctx):
        calls["design"] += 1
        return {"stages": [], "round": calls["design"]}

    async def validate_func(inp, ctx):
        calls["validate"] += 1
        passed = calls["validate"] > 1  # 第 1 轮不过 -> 反馈重跑 design_stages
        return {"passed": passed, "report": {} if passed else {"difficulty_step": False}}

    reg = ToolRegistry()
    reg.register(Tool(name="design_stages", llm=False, func=design_func))
    reg.register(Tool(name="validate_plan", llm=False, func=validate_func))
    orch = Orchestrator(reg, ExecContext(session_id="s", trace_id="t", registry=reg))
    result = asyncio.run(orch.run({}, start_tool="design_stages", initial_upstream={}))
    assert result.status == "done"
    # outputs 每工具仅留最后一轮（第 2 轮覆盖第 1 轮）
    assert result.outputs["design_stages"].data["round"] == 2
    # executed 台账：两轮 design_stages + 两轮 validate_plan 全部在案
    names = [o.tool for o in result.executed]
    assert names == ["design_stages", "validate_plan", "design_stages", "validate_plan"]
    assert result.executed[0].data["round"] == 1 and result.executed[2].data["round"] == 2


# --- _persist_tool_calls：按执行落账 + 注入上游去重 ---


def _mk_session() -> int:
    from app.db import models

    db = SessionLocal()
    try:
        u = models.User(name="u", username="u", password_hash="", is_admin=False)
        db.add(u)
        db.flush()
        s = models.Session(user_id=u.id, title="s")
        db.add(s)
        db.commit()
        db.refresh(s)
        return s.id
    finally:
        db.close()


def _out(tool: str, tokens: int = 0, data: dict | None = None) -> ToolOutput:
    from app.tools.base import ToolMetadata

    return ToolOutput(
        tool=tool, status="ok", data=data or {},
        metadata=ToolMetadata(tokens_in=tokens, tokens_out=tokens, model="m", cost=0.001 if tokens else 0.0),
    )


def test_persist_tool_calls_executed_and_upstream_dedupe():
    from app.api.plans import _persist_tool_calls
    from app.db import models

    sid = _mk_session()
    db = SessionLocal()
    try:
        # 首跑：executed=[understand_intent]，outputs 含注入上游 parse_material -> 补登一行
        _persist_tool_calls(
            db, sid, "t1",
            outputs={"parse_material": _out("parse_material"), "understand_intent": _out("understand_intent", 100)},
            executed=[_out("understand_intent", 100)],
        )
        rows = db.query(models.ToolCall).filter_by(session_id=sid).all()
        assert sorted(r.tool for r in rows) == ["parse_material", "understand_intent"]
        assert db.query(models.ApiUsage).count() == 1  # 零 token 的注入上游不记 api_usage

        # 重生成落账：outputs 为重建上游（无 metadata）+ 本轮新执行的 design_stages
        _persist_tool_calls(
            db, sid, "t2",
            outputs={"parse_material": _out("parse_material"), "understand_intent": _out("understand_intent")},
            executed=[_out("design_stages", 200)],
        )
        rows = db.query(models.ToolCall).filter_by(session_id=sid).all()
        # 重建上游不重复建行；仅新增 design_stages
        assert sorted(r.tool for r in rows) == ["design_stages", "parse_material", "understand_intent"]
        assert db.query(models.ApiUsage).count() == 2
        # 反馈循环早轮（同工具多次执行）逐次落账
        _persist_tool_calls(
            db, sid, "t3",
            outputs={"design_stages": _out("design_stages", 200)},
            executed=[_out("design_stages", 200), _out("design_stages", 300)],
        )
        assert db.query(models.ToolCall).filter_by(session_id=sid, tool="design_stages").count() == 3
        usage_total = sum(u.tokens_in for u in db.query(models.ApiUsage).all())
        assert usage_total == 100 + 200 + 200 + 300  # 全部轮次成本可见，日预算无黑洞
    finally:
        db.close()


# --- AsyncAnthropic client 缓存复用 ---


def test_anthropic_client_cache_reuse_and_bounds():
    from app.llm.anthropic_provider import _clients, _shared_client, reset_client_cache

    reset_client_cache()
    try:
        c1 = _shared_client("k1", None)
        assert _shared_client("k1", None) is c1  # 同 key 复用（此前每次新建且从不关闭）
        assert _shared_client("k2", None) is not c1  # 不同 key 各自独立
        # 有界 FIFO：超出上限后缓存不无限增长
        for i in range(12):
            _shared_client(f"key-{i}", None)
        assert len(_clients) <= 8

        # 跨事件循环自动重建（缓存 client 绑定旧循环，换循环复用会 Event loop is closed）
        reset_client_cache()

        async def _get():
            return _shared_client("loop-key", None)

        a = asyncio.run(_get())
        b = asyncio.run(_get())  # 第二个 loop：须重建而非复用旧循环 client
        assert a is not b
    finally:
        reset_client_cache()

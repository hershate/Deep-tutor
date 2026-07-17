"""Agent 循环与编排器单元测试（mock LLM，不联网）。

覆盖：Python 工具 / LLM 工具校验 / 工具调用修复重试 / 降级默认 / 字段覆盖 /
预算守卫 / 编排器 pipeline 顺序 / 反馈循环 / 澄清暂停。
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from app.agent import (
    BudgetTracker,
    ExecContext,
    Orchestrator,
    PIPELINE,
    Tool,
    ToolRegistry,
    check_field_coverage,
    run_tool,
)
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.tools.base import ToolInput, ToolOutput


# --- fakes ---


class ScriptedProvider(LLMProvider):
    """按队列返回 LLM 响应；记录每次调用的 system/user。"""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True) -> LLMResponse:
        self.calls.append({"system": system, "user": user, "cache_system": cache_system})
        if not self._responses:
            return LLMResponse(content="{}", usage=LLMUsage(model=model or "claude-sonnet-5"))
        return self._responses.pop(0)


def _resp(content: str, inp=100, out=50, model="claude-sonnet-5") -> LLMResponse:
    return LLMResponse(content=content, usage=LLMUsage(input_tokens=inp, output_tokens=out, model=model))


class IntentOutput(BaseModel):
    topic: str
    needs_clarify: bool = False


def _ctx(provider=None, registry=None, budget=None, skills=None) -> ExecContext:
    ctx = ExecContext(
        session_id="sess_test",
        trace_id="trace_test",
        registry=registry,
        llm_provider=provider,
        skill_registry=skills,
        budget=budget,
    )
    return ctx


def _inp(name="understand_intent", args=None, upstream=None) -> ToolInput:
    return ToolInput(tool=name, session_id="sess_test", args=args or {}, upstream=upstream or {})


# === loop: Python 工具 ===


def test_python_tool_runs_func():
    async def func(inp, ctx):
        return {"text": "hello", "meta": {"chars": 5}}

    reg = ToolRegistry()
    reg.register(Tool(name="parse_material", llm=False, func=func))
    ctx = _ctx(registry=reg)
    out = asyncio.run(run_tool("parse_material", _inp("parse_material"), ctx))
    assert out.status == "ok"
    assert out.data == {"text": "hello", "meta": {"chars": 5}}
    assert out.metadata.duration_ms >= 0


def test_python_tool_returning_tool_output_passes_through():
    async def func(inp, ctx):
        return ToolOutput(tool="parse_material", status="ok", data={"x": 1})

    reg = ToolRegistry()
    reg.register(Tool(name="parse_material", llm=False, func=func))
    out = asyncio.run(run_tool("parse_material", _inp("parse_material"), _ctx(registry=reg)))
    assert out.data == {"x": 1}


# === loop: LLM 工具校验 ===


def test_llm_tool_valid_first_try():
    provider = ScriptedProvider([_resp('{"topic": "React", "needs_clarify": false}')])
    reg = ToolRegistry()
    reg.register(Tool(name="understand_intent", llm=True, skill_name="intent", output_model=IntentOutput))
    ctx = _ctx(provider=provider, registry=reg, skills={"intent": "SYS"})
    out = asyncio.run(run_tool("understand_intent", _inp(), ctx))
    assert out.status == "ok"
    assert out.data["topic"] == "React"
    # system 用 Skill prompt + cache_system=True
    assert provider.calls[0]["system"] == "SYS"
    assert provider.calls[0]["cache_system"] is True
    assert out.metadata.tokens_in == 100


def test_llm_tool_repairs_then_succeeds():
    # 第 1 次非法 JSON，第 2 次合法 -> 重试后 ok，usage 累加 2 次
    provider = ScriptedProvider(
        [_resp("not json at all"), _resp('{"topic": "Go", "needs_clarify": false}')]
    )
    reg = ToolRegistry()
    reg.register(Tool(name="understand_intent", llm=True, output_model=IntentOutput))
    out = asyncio.run(run_tool("understand_intent", _inp(), _ctx(provider=provider, registry=reg)))
    assert out.status == "ok"
    assert out.data["topic"] == "Go"
    assert len(provider.calls) == 2
    # 第 2 次的 user 应含校验失败反馈
    assert "校验失败反馈" in provider.calls[1]["user"]
    assert out.metadata.tokens_in == 200  # 100 + 100


def test_llm_tool_json_fence_extracted():
    provider = ScriptedProvider([_resp('```json\n{"topic": "Rust", "needs_clarify": false}\n```')])
    reg = ToolRegistry()
    reg.register(Tool(name="understand_intent", llm=True, output_model=IntentOutput))
    out = asyncio.run(run_tool("understand_intent", _inp(), _ctx(provider=provider, registry=reg)))
    assert out.status == "ok"
    assert out.data["topic"] == "Rust"


def test_llm_tool_degrades_to_default():
    # 始终非法 -> 重试耗尽 -> 降级 default_data, status=repaired
    provider = ScriptedProvider([_resp("nope")] * 10)
    reg = ToolRegistry()
    reg.register(
        Tool(
            name="understand_intent",
            llm=True,
            output_model=IntentOutput,
            default_data={"topic": "unknown", "needs_clarify": True, "clarify_questions": ["想学什么？"]},
        )
    )
    out = asyncio.run(run_tool("understand_intent", _inp(), _ctx(provider=provider, registry=reg)))
    assert out.status == "repaired"
    assert out.data["topic"] == "unknown"
    assert out.errors and out.errors[0].code == "schema_violation"
    # 1 初始 + 3 重试 = 4 次调用
    assert len(provider.calls) == 4


def test_llm_tool_no_default_becomes_error():
    provider = ScriptedProvider([_resp("nope")] * 10)
    reg = ToolRegistry()
    reg.register(Tool(name="understand_intent", llm=True, output_model=IntentOutput))
    out = asyncio.run(run_tool("understand_intent", _inp(), _ctx(provider=provider, registry=reg)))
    assert out.status == "error"
    assert out.errors[0].code == "schema_violation"


def test_llm_tool_missing_skill_uses_default_prompt():
    provider = ScriptedProvider([_resp('{"topic": "X", "needs_clarify": false}')])
    reg = ToolRegistry()
    reg.register(Tool(name="understand_intent", llm=True, skill_name="missing", output_model=IntentOutput))
    out = asyncio.run(run_tool("understand_intent", _inp(), _ctx(provider=provider, registry=reg, skills={})))
    assert out.status == "ok"
    assert "工具执行器" in provider.calls[0]["system"]  # DEFAULT_SKILL_PROMPT


def test_no_provider_degrades():
    reg = ToolRegistry()
    reg.register(Tool(name="understand_intent", llm=True, output_model=IntentOutput, default_data={"topic": "?", "needs_clarify": False}))
    out = asyncio.run(run_tool("understand_intent", _inp(), _ctx(provider=None, registry=reg)))
    assert out.status == "repaired"
    assert out.errors[0].code == "no_llm_provider"


# === 字段覆盖 ===


def test_field_coverage_missing():
    reg = ToolRegistry()
    # design_stages 需要 understand_intent.topic，但 outputs 无 understand_intent
    tool = Tool(name="design_stages", llm=True, requires=["understand_intent.topic"])
    reg.register(tool)
    missing = check_field_coverage(tool, {})
    assert "understand_intent.topic" in missing


def test_field_coverage_satisfied():
    tool = Tool(name="design_stages", llm=True, requires=["understand_intent.topic"])
    outputs = {"understand_intent": ToolOutput(tool="understand_intent", status="ok", data={"topic": "Go"})}
    assert check_field_coverage(tool, outputs) == []


# === 预算守卫 ===


def test_budget_exceeded():
    tracker = BudgetTracker(limit=1.0)  # $1
    # 1M input tokens @ $3/M = $3 > $1
    tracker.add(3.0)
    assert tracker.exceeded() is True


def test_budget_unlimited_when_zero():
    tracker = BudgetTracker(limit=0.0)
    tracker.add(999999.0)
    assert tracker.exceeded() is False


# === 编排器：pipeline 顺序 ===


def _py_registry(order: list[str], validate_plan_fn=None, design_stages_fn=None) -> ToolRegistry:
    reg = ToolRegistry()

    def make(name, data):
        async def func(inp, ctx):
            order.append(name)
            return data
        return Tool(name=name, llm=False, func=func)

    reg.register(make("parse_material", {"text": "t", "chunks": ["t"]}))
    reg.register(make("understand_intent", {"topic": "Go", "needs_clarify": False}))
    reg.register(make("build_knowledge_map", {"nodes": [], "edges": []}))
    if design_stages_fn:
        reg.register(Tool(name="design_stages", llm=False, func=design_stages_fn))
    else:
        reg.register(make("design_stages", {"stages": []}))
    reg.register(make("search_resources", {"resources": []}))
    reg.register(make("schedule_plan", {"schedule": []}))

    calls = {"n": 0}

    async def validate_func(inp, ctx):
        order.append("validate_plan")
        calls["n"] += 1
        if validate_plan_fn:
            return validate_plan_fn(calls["n"])
        return {"report": {}, "passed": True, "fixes": []}

    reg.register(Tool(name="validate_plan", llm=False, func=validate_func))
    reg.register(make("export_plan", {"file_url": "/data/x.md"}))
    return reg


def test_orchestrator_pipeline_order_and_done():
    order: list[str] = []
    reg = _py_registry(order)
    orch = Orchestrator(reg, _ctx(registry=reg))
    result = asyncio.run(orch.run({}))
    assert result.status == "done"
    assert order == PIPELINE  # 按固定顺序执行
    # _assemble 组装完整方案（各组件聚合，非 export_plan 的 file_url）
    assert result.plan["intent"]["topic"] == "Go"
    assert result.plan["validation_report"]["passed"] is True
    assert result.plan["stages"] == []


def test_orchestrator_clarify_pauses():
    order: list[str] = []
    # understand_intent 需澄清 -> 暂停
    async def clarify_func(inp, ctx):
        order.append("understand_intent")
        return {"topic": None, "needs_clarify": True, "clarify_questions": ["想学哪方面？"]}

    reg = ToolRegistry()
    reg.register(Tool(name="parse_material", llm=False, func=_mk("parse_material", {"text": "t"}, order)))
    reg.register(Tool(name="understand_intent", llm=False, func=clarify_func))
    orch = Orchestrator(reg, _ctx(registry=reg))
    result = asyncio.run(orch.run({}))
    assert result.status == "paused_clarify"
    assert order == ["parse_material", "understand_intent"]


def _mk(name, data, order):
    async def func(inp, ctx):
        order.append(name)
        return data
    return func


def test_orchestrator_feedback_loop_reruns_design_stages():
    order: list[str] = []
    design_calls = {"n": 0}

    async def design_func(inp, ctx):
        order.append("design_stages")
        design_calls["n"] += 1
        return {"stages": [{"index": 0, "name": "s0", "difficulty": 1}]}

    def validate_fn(n):
        # 第 1 次：difficulty_step 失败 -> 反馈循环重跑 design_stages
        # 第 2 次：通过
        if n == 1:
            return {"report": {"difficulty_step": "fail"}, "passed": False, "fixes": ["插入过渡阶段"]}
        return {"report": {}, "passed": True, "fixes": []}

    reg = _py_registry(order, validate_plan_fn=validate_fn, design_stages_fn=design_func)
    events: list[tuple] = []
    orch = Orchestrator(reg, _ctx(registry=reg), event_cb=lambda e, d: events.append((e, d)))
    result = asyncio.run(orch.run({}))

    assert result.status == "done"
    # design_stages 被执行 2 次（初始 + 反馈重跑）
    assert design_calls["n"] == 2
    # validate_plan 被执行 2 次
    assert order.count("validate_plan") == 2
    # 触发了 warn 事件
    assert any(e == "warn" for e, _ in events)


def test_orchestrator_budget_interrupts():
    order: list[str] = []

    async def expensive_func(inp, ctx):
        order.append("understand_intent")
        # 返回带巨大成本（通过 ToolOutput.metadata 手动设）
        return ToolOutput(
            tool="understand_intent",
            status="ok",
            data={"topic": "X", "needs_clarify": False},
            metadata={"tokens_in": 1_000_000, "tokens_out": 0, "cost": 3.0, "model": "claude-sonnet-5"},  # type: ignore[arg-type]
        )

    reg = ToolRegistry()
    reg.register(Tool(name="parse_material", llm=False, func=_mk("parse_material", {"text": "t"}, order)))
    reg.register(Tool(name="understand_intent", llm=False, func=expensive_func))
    budget = BudgetTracker(limit=1.0)
    events: list[tuple] = []
    orch = Orchestrator(reg, _ctx(registry=reg, budget=budget), event_cb=lambda e, d: events.append((e, d)))
    result = asyncio.run(orch.run({}))

    assert result.status == "interrupted"
    assert any(e == "budget_exceeded" for e, _ in events)
    # 后续工具未执行
    assert "build_knowledge_map" not in order

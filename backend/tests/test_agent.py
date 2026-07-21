"""Agent 循环与编排器单元测试（mock LLM，不联网）。

覆盖：Python 工具 / LLM 工具校验 / 工具调用修复重试 / 降级默认 / 字段覆盖 /
预算守卫 / 编排器 pipeline 顺序 / 反馈循环 / 澄清暂停。
"""

from __future__ import annotations

import asyncio

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
    events: list[tuple] = []
    orch = Orchestrator(reg, _ctx(registry=reg), event_cb=lambda e, d: events.append((e, d)))
    result = asyncio.run(orch.run({}))
    assert result.status == "done"
    assert order == PIPELINE  # 按固定顺序执行
    # iter 184 B2：主流程事件 step_index 与 PIPELINE 顺序一致、attempt=1
    starts = [(d["tool"], d["step_index"], d["total_steps"], d["attempt"]) for e, d in events if e == "tool_start"]
    assert starts == [(name, i + 1, len(PIPELINE), 1) for i, name in enumerate(PIPELINE)]
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
    # iter 184 B2：事件携带步骤序号与重跑轮次
    starts = [(d["tool"], d["step_index"], d["total_steps"], d["attempt"]) for e, d in events if e == "tool_start"]
    # 主流程首发 attempt=1，序号与 PIPELINE 全局位置一致
    assert ("design_stages", PIPELINE.index("design_stages") + 1, len(PIPELINE), 1) in starts
    # 反馈循环重跑 attempt=2（design_stages 与其下游 validate_plan 均重跑）
    assert ("design_stages", PIPELINE.index("design_stages") + 1, len(PIPELINE), 2) in starts
    assert ("validate_plan", PIPELINE.index("validate_plan") + 1, len(PIPELINE), 2) in starts
    # tool_end 同样携带
    ends = [(d["tool"], d["attempt"]) for e, d in events if e == "tool_end"]
    assert ("design_stages", 2) in ends


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


def test_orchestrator_budget_exceeded_during_feedback_rerun():
    """反馈循环重跑期间预算超限 -> 中断（不继续 export），防反馈循环超支。"""
    order: list[str] = []
    design_calls = {"n": 0}

    async def design_func(inp, ctx):
        order.append("design_stages")
        design_calls["n"] += 1
        # 初始低成本；反馈重跑时高成本 -> 触发预算超限
        cost = 0.0 if design_calls["n"] == 1 else 5.0
        return ToolOutput(
            tool="design_stages",
            status="ok",
            data={"stages": [{"index": 0, "name": "s0", "difficulty": 1}]},
            metadata={"tokens_in": 1_000_000, "tokens_out": 0, "cost": cost, "model": "claude-sonnet-5"},  # type: ignore[arg-type]
        )

    def validate_fn(n):
        if n == 1:
            return {"report": {"difficulty_step": "fail"}, "passed": False, "fixes": ["插入过渡阶段"]}
        return {"report": {}, "passed": True, "fixes": []}

    reg = _py_registry(order, validate_plan_fn=validate_fn, design_stages_fn=design_func)
    budget = BudgetTracker(limit=1.0)
    events: list[tuple] = []
    orch = Orchestrator(reg, _ctx(registry=reg, budget=budget), event_cb=lambda e, d: events.append((e, d)))
    result = asyncio.run(orch.run({}))

    assert result.status == "interrupted"
    assert any(e == "budget_exceeded" for e, _ in events)
    # export_plan 未执行（中断保留部分）
    assert "export_plan" not in order
    # design_stages 重跑过一次（反馈循环触发后超限中断）
    assert design_calls["n"] == 2


def test_orchestrator_emits_plan_chunk_per_tool():
    """plan/03 §一：每个工具产出后广播 plan_chunk（前端「查看部分」），内容随进度增长。"""
    order: list[str] = []
    reg = _py_registry(order)
    events: list[tuple] = []
    orch = Orchestrator(reg, _ctx(registry=reg), event_cb=lambda e, d: events.append((e, d)))
    result = asyncio.run(orch.run({}))

    assert result.status == "done"
    chunks = [d["partial_plan"] for e, d in events if e == "plan_chunk"]
    # 每个已注册工具一次（export_plan 也含），至少 7 个 chunk
    assert len(chunks) >= 7
    # 递增包含：首个 chunk 仅有 parse_material 之外为空 intent；含 intent 的 chunk 在 understand_intent 之后
    intents = [c.get("intent") for c in chunks]
    assert any(i is None for i in intents[:1])
    assert any(i and i.get("topic") == "Go" for i in intents)
    # 最终 chunk 与 result.plan 一致（全组件聚合）
    assert chunks[-1]["validation_report"]["passed"] is True


# === iter 229 R2：search_resources ∥ schedule_plan 并行段 ===


def _overlap_registry(track, validate_plan_fn=None, schedule_fn=None):
    """search/schedule 带重叠追踪的注册表（sleep 制造并发窗口，其余工具瞬时完成）。"""
    reg = ToolRegistry()

    def make(name, data):
        async def func(inp, ctx):
            return data
        return Tool(name=name, llm=False, func=func)

    reg.register(make("parse_material", {"text": "t"}))
    reg.register(make("understand_intent", {"topic": "Go", "needs_clarify": False}))
    reg.register(make("build_knowledge_map", {"nodes": [], "edges": []}))
    reg.register(make("design_stages", {"stages": []}))

    async def search_func(inp, ctx):
        track["cur"] += 1
        track["max"] = max(track["max"], track["cur"])
        await asyncio.sleep(0.05)
        track["cur"] -= 1
        return {"resources": []}

    async def schedule_func(inp, ctx):
        track["cur"] += 1
        track["max"] = max(track["max"], track["cur"])
        await asyncio.sleep(0.05)
        track["cur"] -= 1
        return {"schedule": []}

    reg.register(Tool(name="search_resources", llm=False, func=search_func))
    reg.register(Tool(name="schedule_plan", llm=False, func=schedule_fn or schedule_func))

    calls = {"n": 0}

    async def validate_func(inp, ctx):
        calls["n"] += 1
        if validate_plan_fn:
            return validate_plan_fn(calls["n"])
        return {"report": {}, "passed": True, "fixes": []}

    reg.register(Tool(name="validate_plan", llm=False, func=validate_func))
    reg.register(make("export_plan", {"file_url": "/data/x.md"}))
    return reg, calls


def test_orchestrator_parallel_span_concurrent():
    """R2：search_resources 与 schedule_plan 真实并发（重叠窗口 max==2），双输出落位。"""
    track = {"cur": 0, "max": 0}
    reg, _ = _overlap_registry(track)
    orch = Orchestrator(reg, _ctx(registry=reg))
    result = asyncio.run(orch.run({}))
    assert result.status == "done"
    assert track["max"] == 2  # 两工具执行窗口真实重叠（串行实现下恒为 1）
    assert result.outputs["search_resources"].status == "ok"
    assert result.outputs["schedule_plan"].status == "ok"


def test_check_parallel_groups_safe_catches_intra_group_read():
    """iter 235 W4b：同组成员读取另一成员输出 -> ValueError（并行安全断言）。

    并行组成员共享同一 outputs 快照，读同组成员即读到缺失/陈旧数据；本断言把
    「同组互不依赖」从注释约定升级为 import 期强制，违例启动即炸而非线上静默错数据。
    """
    import pytest

    from app.agent import check_parallel_groups_safe

    def _pytool(name, reads=frozenset()):
        async def _f(inp, ctx):  # noqa: ANN001
            return {}

        return Tool(name=name, llm=False, func=_f, upstream_reads=reads)

    # 当前真实配置：search 读 understand/design_stages，schedule 不读上游 -> 通过
    check_parallel_groups_safe({
        "search_resources": _pytool("search_resources", frozenset({"understand_intent", "design_stages"})),
        "schedule_plan": _pytool("schedule_plan"),
    })
    # 坏配置：schedule_plan 读同组成员 search_resources -> 抛 ValueError
    with pytest.raises(ValueError, match="读取同组工具输出"):
        check_parallel_groups_safe({
            "search_resources": _pytool("search_resources"),
            "schedule_plan": _pytool("schedule_plan", frozenset({"search_resources"})),
        })


def test_default_registry_passes_parallel_safety_check():
    """iter 235 W4b：真实 default_registry 构建即触发并行安全校验（不抛即通过）。"""
    from app.tools import default_registry

    reg = default_registry()  # 构建内部已调用 check_parallel_groups_safe
    assert reg.has("search_resources") and reg.has("schedule_plan")


def test_orchestrator_parallel_span_error_deterministic():
    """R2：组内错误按 PIPELINE 顺序确定性报告；成功成员输出保留在 outputs。"""
    from app.tools.base import ToolError

    track = {"cur": 0, "max": 0}

    async def bad_schedule(inp, ctx):
        return ToolOutput(
            tool="schedule_plan",
            status="error",
            errors=[ToolError(code="schedule_broken", message="排程失败")],
        )

    reg, _ = _overlap_registry(track, schedule_fn=bad_schedule)
    events: list[tuple] = []
    orch = Orchestrator(reg, _ctx(registry=reg), event_cb=lambda e, d: events.append((e, d)))
    result = asyncio.run(orch.run({}))
    assert result.status == "error"
    assert result.errors == ["schedule_plan: schedule_broken - 排程失败"]
    assert result.outputs["search_resources"].status == "ok"  # 成功成员输出保留
    err_events = [d for e, d in events if e == "error"]
    assert err_events and err_events[-1]["stage"] == "schedule_plan"
    assert "export_plan" not in result.outputs  # 错误中断，下游未执行


def test_orchestrator_regenerate_scope_skips_parallel_sibling():
    """R2：start_tool 落在并行组中间（scope=schedule_plan）时同组上游成员不重跑。"""
    track = {"cur": 0, "max": 0}
    reg, calls = _overlap_registry(track)
    order: list[str] = []
    # 包一层记录执行顺序
    for name in ("search_resources", "schedule_plan", "validate_plan", "export_plan"):
        tool = reg.get(name)
        inner = tool.func

        async def wrapped(inp, ctx, _inner=inner, _name=name):
            order.append(_name)
            return await _inner(inp, ctx)

        reg.register(Tool(name=name, llm=False, func=wrapped))
    orch = Orchestrator(reg, _ctx(registry=reg))
    upstream = {
        "design_stages": ToolOutput(tool="design_stages", status="ok", data={"stages": []}),
        "search_resources": ToolOutput(tool="search_resources", status="ok", data={"resources": []}),
    }
    result = asyncio.run(orch.run({}, start_tool="schedule_plan", initial_upstream=upstream))
    assert result.status == "done"
    assert "search_resources" not in order  # 同组上游成员由 initial_upstream 保留
    assert order == ["schedule_plan", "validate_plan", "export_plan"]
    assert calls["n"] == 1


def test_orchestrator_feedback_rerun_uses_parallel_span():
    """R2：反馈循环重跑区间覆盖并行段时，search/schedule 同样并发重跑。"""
    track = {"cur": 0, "max": 0}

    def validate_fn(n):
        if n == 1:
            return {"report": {"dependency_closure": "fail"}, "passed": False, "fixes": ["补依赖"]}
        return {"report": {}, "passed": True, "fixes": []}

    reg, calls = _overlap_registry(track, validate_plan_fn=validate_fn)
    orch = Orchestrator(reg, _ctx(registry=reg))
    result = asyncio.run(orch.run({}))
    assert result.status == "done"
    assert calls["n"] == 2  # 反馈循环重跑一轮
    assert track["max"] == 2  # 重跑段内 search/schedule 同样并发

"""validate_plan 工具测试（mock LLM）。"""

from __future__ import annotations

import asyncio
import json

from app.agent.context import ExecContext
from app.agent.loop import run_tool
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.tools import default_registry
from app.tools.base import ToolInput


class ScriptedProvider(LLMProvider):
    def __init__(self, responses):
        self._r = list(responses)

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        if not self._r:
            return LLMResponse(content="{}", usage=LLMUsage(model="claude-sonnet-5"))
        return self._r.pop(0)


def _ctx(provider):
    return ExecContext(session_id="s", trace_id="t", registry=default_registry(), llm_provider=provider)


def _run(provider, args=None):
    inp = ToolInput(tool="validate_plan", session_id="s", args=args or {})
    return asyncio.run(run_tool("validate_plan", inp, _ctx(provider)))


def _resp(obj):
    return LLMResponse(content=json.dumps(obj), usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))


def _report(**overrides):
    base = {k: {"status": "pass", "details": ""} for k in
            ["dependency_closure", "difficulty_step", "cognitive_load", "milestone_feasibility", "gap_analysis"]}
    base.update(overrides)
    return base


def test_all_pass():
    provider = ScriptedProvider([_resp({"report": _report(), "passed": True, "fixes": []})])
    out = _run(provider)
    assert out.status == "ok"
    assert out.data["passed"] is True
    assert out.data["fixes"] == []


def test_difficulty_step_fail_with_fixes():
    provider = ScriptedProvider(
        [_resp({"report": _report(difficulty_step={"status": "fail", "details": "Stage2->Stage3 跳级"}),
                "passed": False, "fixes": ["插入过渡阶段"]})]
    )
    out = _run(provider)
    assert out.status == "ok"
    assert out.data["passed"] is False
    assert out.data["report"]["difficulty_step"]["status"] == "fail"
    assert out.data["fixes"] == ["插入过渡阶段"]


def test_degrade_delivers_with_warn():
    provider = ScriptedProvider([_resp("not json")] * 10)
    out = _run(provider)
    assert out.status == "repaired"
    assert out.data["passed"] is True  # 降级交付，不阻塞
    assert out.errors[0].code == "schema_violation"

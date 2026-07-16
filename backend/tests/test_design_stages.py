"""design_stages 工具测试（mock LLM）。"""

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
    inp = ToolInput(tool="design_stages", session_id="s", args=args or {})
    return asyncio.run(run_tool("design_stages", inp, _ctx(provider)))


def _resp(obj):
    return LLMResponse(content=json.dumps(obj), usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))


def _stage(i, diff):
    return {"index": i, "name": f"s{i}", "goal": "g", "difficulty": diff, "estimated_weeks": 1, "topics": [{"name": "t", "why": "w", "must_learn": True}], "milestone": "m"}


def test_valid_three_stages():
    provider = ScriptedProvider([_resp({"stages": [_stage(0, 1), _stage(1, 3), _stage(2, 5)]})])
    out = _run(provider)
    assert out.status == "ok"
    assert len(out.data["stages"]) == 3
    assert out.data["stages"][0]["topics"][0]["must_learn"] is True


def test_too_few_stages_triggers_repair_then_ok():
    # 第 1 次 2 阶段（不符 3-6）-> 校验失败重试；第 2 次 3 阶段 -> ok
    provider = ScriptedProvider(
        [_resp({"stages": [_stage(0, 1), _stage(1, 3)]}), _resp({"stages": [_stage(0, 1), _stage(1, 3), _stage(2, 5)]})]
    )
    out = _run(provider)
    assert out.status == "ok"
    assert len(out.data["stages"]) == 3


def test_too_many_stages_triggers_repair():
    provider = ScriptedProvider([_resp({"stages": [_stage(i, 1) for i in range(7)]})] * 10)
    out = _run(provider)
    # 始终 7 阶段 -> 重试耗尽 -> 降级默认 3 阶段
    assert out.status == "repaired"
    assert len(out.data["stages"]) == 3
    assert out.errors[0].code == "schema_violation"

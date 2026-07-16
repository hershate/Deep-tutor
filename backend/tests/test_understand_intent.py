"""understand_intent 工具测试（mock LLM）。"""

from __future__ import annotations

import asyncio

from app.agent import ExecContext, ToolRegistry, run_tool
from app.agent.context import ExecContext as Ctx  # noqa: N814
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.tools import default_registry
from app.tools.base import ToolInput


class ScriptedProvider(LLMProvider):
    def __init__(self, responses):
        self._r = list(responses)
        self.calls: list[dict] = []

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        self.calls.append({"system": system, "user": user})
        if not self._r:
            return LLMResponse(content="{}", usage=LLMUsage(model="claude-sonnet-5"))
        return self._r.pop(0)


def _ctx(provider):
    reg = default_registry()
    return Ctx(session_id="s", trace_id="t", registry=reg, llm_provider=provider)


def _run(provider, args):
    inp = ToolInput(tool="understand_intent", session_id="s", args=args)
    return asyncio.run(run_tool("understand_intent", inp, _ctx(provider)))


def _resp(content):
    return LLMResponse(content=content, usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))


def test_understand_intent_full():
    provider = ScriptedProvider(
        [_resp('{"topic":"React","level":"零基础","goal":"做项目","daily_minutes":90,"deadline":null,"needs_clarify":false,"clarify_questions":[]}')]
    )
    out = _run(provider, {"raw_text": "我想学 React，零基础，做项目，每天 1-2h"})
    assert out.status == "ok"
    assert out.data["topic"] == "React"
    assert out.data["daily_minutes"] == 90
    assert out.data["needs_clarify"] is False


def test_understand_intent_needs_clarify():
    provider = ScriptedProvider(
        [_resp('{"topic":"编程","level":null,"goal":null,"daily_minutes":null,"deadline":null,"needs_clarify":true,"clarify_questions":["想学哪方面编程？","每天能投入多少时间？"]}')]
    )
    out = _run(provider, {"raw_text": "我想学编程"})
    assert out.status == "ok"
    assert out.data["needs_clarify"] is True
    assert len(out.data["clarify_questions"]) == 2


def test_understand_intent_repairs_invalid_then_valid():
    provider = ScriptedProvider(
        [_resp("not json"), _resp('{"topic":"Go","level":null,"goal":null,"daily_minutes":null,"deadline":null,"needs_clarify":false,"clarify_questions":[]}')]
    )
    out = _run(provider, {"raw_text": "学 Go"})
    assert out.status == "ok"
    assert out.data["topic"] == "Go"


def test_understand_intent_degrades_when_always_invalid():
    provider = ScriptedProvider([_resp("nope")] * 10)
    out = _run(provider, {"raw_text": "学 X"})
    # default_data -> needs_clarify=True
    assert out.status == "repaired"
    assert out.data["needs_clarify"] is True
    assert out.errors[0].code == "schema_violation"


def test_understand_intent_uses_inline_prompt_when_no_skill():
    provider = ScriptedProvider(
        [_resp('{"topic":"Rust","level":null,"goal":null,"daily_minutes":null,"deadline":null,"needs_clarify":false,"clarify_questions":[]}')]
    )
    # ctx 无 skill_registry -> 应使用 inline_prompt（含「学习意图理解」）
    ctx = _ctx(provider)
    inp = ToolInput(tool="understand_intent", session_id="s", args={"raw_text": "学 Rust"})
    asyncio.run(run_tool("understand_intent", inp, ctx))
    assert "学习意图理解" in provider.calls[0]["system"]

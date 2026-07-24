"""understand_intent 工具测试（mock LLM）。"""

from __future__ import annotations

import asyncio

from app.agent import run_tool
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


# --- deadline 确定性校验（plan/09 §十六 past_deadline -> 澄清，iter 75）---


def test_validate_intent_past_deadline_triggers_clarify():
    from app.tools.understand_intent import _validate_intent

    data = {"topic": "Go", "deadline": "2020-01-01", "needs_clarify": False, "clarify_questions": []}
    out = _validate_intent(data, None)
    assert out["deadline"] is None
    assert out["needs_clarify"] is True
    assert any("2020-01-01" in q for q in out["clarify_questions"])


def test_validate_intent_future_deadline_kept():
    from app.tools.understand_intent import _validate_intent

    data = {"topic": "Go", "deadline": "2099-12-31", "needs_clarify": False}
    out = _validate_intent(data, None)
    assert out["deadline"] == "2099-12-31"
    assert out["needs_clarify"] is False


def test_validate_intent_invalid_deadline_cleared_silently():
    from app.tools.understand_intent import _validate_intent

    out = _validate_intent({"topic": "Go", "deadline": "明年年底"}, None)
    assert out["deadline"] is None
    assert out.get("needs_clarify") in (None, False)  # 非法串静默清空，不打扰


def test_validate_intent_no_deadline_noop():
    from app.tools.understand_intent import _validate_intent

    data = {"topic": "Go", "deadline": None}
    assert _validate_intent(data, None) is data


def test_validate_intent_datetime_with_time_past():
    from app.tools.understand_intent import _validate_intent

    out = _validate_intent({"topic": "Go", "deadline": "2025-01-01T23:59:59"}, None)
    assert out["deadline"] is None and out["needs_clarify"] is True


def test_validate_intent_daily_minutes_range():
    from app.tools.understand_intent import _validate_intent

    assert _validate_intent({"topic": "x", "daily_minutes": 60}, None)["daily_minutes"] == 60
    assert _validate_intent({"topic": "x", "daily_minutes": 0}, None)["daily_minutes"] is None
    assert _validate_intent({"topic": "x", "daily_minutes": 99999}, None)["daily_minutes"] is None
    assert _validate_intent({"topic": "x", "daily_minutes": "abc"}, None)["daily_minutes"] is None
    assert _validate_intent({"topic": "x", "daily_minutes": "90"}, None)["daily_minutes"] == 90


def test_validate_intent_topic_truncated():
    from app.tools.understand_intent import _validate_intent

    out = _validate_intent({"topic": "长" * 300}, None)
    assert len(out["topic"]) == 200


# --- iter 358 BUG 修复：澄清死循环兜底（args.answers 非空 = 已澄清，强制推进）---


def test_validate_intent_clarify_round_forces_progress():
    """澄清续接轮 LLM 仍 needs_clarify=true -> 兜底强制推进，缺失维度补中性默认。"""
    from app.tools.understand_intent import _validate_intent

    data = {
        "topic": "React", "level": None, "goal": None, "daily_minutes": None,
        "deadline": None, "needs_clarify": True,
        "clarify_questions": ["每天能投入多少时间？"],
    }
    inp = ToolInput(
        tool="understand_intent", session_id="s",
        args={"answers": {"每天能投入多少时间？": "看情况，随便"}},
    )
    out = _validate_intent(data, inp)
    assert out["needs_clarify"] is False
    assert out["clarify_questions"] == []
    assert out["daily_minutes"] == 60  # 中性默认
    assert out["level"] == "未明确"
    assert "React" in out["goal"]


def test_validate_intent_clarify_round_keeps_extracted_fields():
    """澄清轮若 LLM 已抽出明确字段 -> 保留抽取值，默认不覆盖。"""
    from app.tools.understand_intent import _validate_intent

    data = {
        "topic": "React", "level": "零基础", "goal": "做项目", "daily_minutes": 90,
        "deadline": None, "needs_clarify": True, "clarify_questions": ["..."],
    }
    inp = ToolInput(tool="understand_intent", session_id="s", args={"answers": {"q": "a"}})
    out = _validate_intent(data, inp)
    assert out["needs_clarify"] is False
    assert out["daily_minutes"] == 90  # 保留 LLM 抽取值
    assert out["level"] == "零基础"
    assert out["goal"] == "做项目"


def test_validate_intent_no_answers_still_clarifies():
    """首次生成（无 answers）行为不变：needs_clarify=true 仍保留。"""
    from app.tools.understand_intent import _validate_intent

    data = {
        "topic": "编程", "level": None, "goal": None, "daily_minutes": None,
        "deadline": None, "needs_clarify": True, "clarify_questions": ["..."],
    }
    out = _validate_intent(data, None)  # 无 answers
    assert out["needs_clarify"] is True  # 首次生成照常澄清


def test_validate_intent_clarify_round_past_deadline_no_loop():
    """澄清轮 + 过期 deadline：过期先置 None + needs_clarify=True，兜底再强制推进（不死循环）。"""
    from app.tools.understand_intent import _validate_intent

    data = {"topic": "Go", "deadline": "2020-01-01", "needs_clarify": False, "clarify_questions": []}
    inp = ToolInput(tool="understand_intent", session_id="s", args={"answers": {"q": "a"}})
    out = _validate_intent(data, inp)
    assert out["deadline"] is None
    assert out["needs_clarify"] is False  # 不再反复追问过期日期


def test_understand_intent_clarify_round_does_not_reclarify():
    """iter 358 端到端（工具级）：澄清续接轮 LLM 仍返回 needs_clarify=true，
    post_process 兜底强制推进——复现并锁定「反复追问不推进」BUG 的修复。"""
    provider = ScriptedProvider(
        [_resp('{"topic":"React","level":null,"goal":null,"daily_minutes":null,"deadline":null,"needs_clarify":true,"clarify_questions":["每天能投入多少时间？"]}')]
    )
    out = _run(provider, {"raw_text": "我想学 React", "answers": {"每天能投入多少时间？": "看情况"}})
    assert out.status == "ok"
    assert out.data["needs_clarify"] is False  # 兜底强制推进，不再澄清
    assert out.data["daily_minutes"] == 60
    assert out.data["clarify_questions"] == []

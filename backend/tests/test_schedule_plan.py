"""schedule_plan 工具测试（mock LLM）。"""

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


def _run(provider, args):
    inp = ToolInput(tool="schedule_plan", session_id="s", args=args)
    return asyncio.run(run_tool("schedule_plan", inp, _ctx(provider)))


def _resp(obj):
    return LLMResponse(content=json.dumps(obj), usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))


def _item(date, est):
    return {"date": date, "stage_index": 0, "tasks": ["t"], "est_minutes": est}


def test_schedule_fits_capacity():
    # 2 天，每日 60min，总 100min <= 容量 120
    provider = ScriptedProvider([_resp({"schedule": [_item("2026-07-16", 50), _item("2026-07-17", 50)]})])
    out = _run(provider, {"daily_minutes": 60, "deadline": None})
    assert out.status == "ok"
    assert out.data["total_minutes"] == 100
    assert out.data["capacity_minutes"] == 120
    assert "warning" not in out.data


def test_schedule_overloaded_warns():
    # 2 天，每日 60min，总 200min > 容量 120
    provider = ScriptedProvider([_resp({"schedule": [_item("2026-07-16", 100), _item("2026-07-17", 100)]})])
    out = _run(provider, {"daily_minutes": 60})
    assert out.status == "ok"
    assert out.data["warning"] == "insufficient_time"
    assert "suggestion" in out.data


def test_degrade_to_empty():
    provider = ScriptedProvider([_resp("not json")] * 10)
    out = _run(provider, {"daily_minutes": 60})
    assert out.status == "repaired"
    assert out.data["schedule"] == []
    assert out.errors[0].code == "schema_violation"

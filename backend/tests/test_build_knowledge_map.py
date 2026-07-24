"""build_knowledge_map 工具测试（mock LLM）。"""

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
    return ExecContext(
        session_id="s", trace_id="t", registry=default_registry(), llm_provider=provider
    )


def _run(provider, args=None):
    inp = ToolInput(tool="build_knowledge_map", session_id="s", args=args or {})
    return asyncio.run(run_tool("build_knowledge_map", inp, _ctx(provider)))


def _resp(obj):
    return LLMResponse(content=json.dumps(obj), usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))


def test_valid_map_and_from_alias_preserved():
    provider = ScriptedProvider(
        [_resp({"nodes": [{"id": "k1", "name": "基础", "difficulty": 1, "must_learn": True, "parent_id": None}],
                "edges": [{"from": "k1", "to": "k2", "relation": "前置"}]})]
    )
    # k2 不在 nodes -> 悬空边应被丢弃
    out = _run(provider)
    assert out.status == "ok"
    assert out.data["nodes"][0]["id"] == "k1"
    assert out.data["edges"] == []  # k2 悬空，边被丢


def test_cycle_is_broken():
    provider = ScriptedProvider(
        [_resp({"nodes": [{"id": "k1", "name": "a"}, {"id": "k2", "name": "b"}],
                "edges": [{"from": "k1", "to": "k2"}, {"from": "k2", "to": "k1"}]})]
    )
    out = _run(provider)
    assert out.status == "ok"
    assert len(out.data["edges"]) == 1  # 环断开，仅保留一条
    assert out.data["edges"][0]["from"] == "k1"


def test_node_truncation():
    nodes = [{"id": f"k{i}", "name": f"n{i}"} for i in range(101)]
    provider = ScriptedProvider([_resp({"nodes": nodes, "edges": []})])
    out = _run(provider)
    assert out.status == "ok"
    assert len(out.data["nodes"]) == 100
    assert "截断" in out.data.get("_note", "")


def test_degrade_to_empty_map():
    provider = ScriptedProvider([_resp("not json")] * 10)
    out = _run(provider)
    assert out.status == "repaired"
    assert out.data == {"nodes": [], "edges": []}
    assert out.errors[0].code == "schema_violation"


def test_difficulty_clamped_to_range():
    # iter 347：LLM 越界（7 / 0 / -3）应被夹紧到 [1,5]
    provider = ScriptedProvider(
        [_resp({"nodes": [
            {"id": "k1", "name": "a", "difficulty": 7},
            {"id": "k2", "name": "b", "difficulty": 0},
            {"id": "k3", "name": "c", "difficulty": -3},
            {"id": "k4", "name": "d", "difficulty": 3},
        ], "edges": []})]
    )
    out = _run(provider)
    assert out.status == "ok"
    by_id = {n["id"]: n["difficulty"] for n in out.data["nodes"]}
    assert by_id == {"k1": 5, "k2": 1, "k3": 1, "k4": 3}


def test_difficulty_non_int_defaults_to_one():
    # iter 347：非整数/缺失 difficulty 兜底为 1，不抛
    provider = ScriptedProvider(
        [_resp({"nodes": [
            {"id": "k1", "name": "a", "difficulty": "很难"},
            {"id": "k2", "name": "b"},
            {"id": "k3", "name": "c", "difficulty": None},
        ], "edges": []})]
    )
    out = _run(provider)
    assert out.status == "ok"
    by_id = {n["id"]: n["difficulty"] for n in out.data["nodes"]}
    assert by_id == {"k1": 1, "k2": 1, "k3": 1}

"""生成时持久化 knowledge_nodes 测试（P2 iter 39）。

_persist_plan 将 plan 内容 knowledge_map.nodes 落库为 knowledge_nodes 行：
sid 保留原始字符串 id（前端 quiz/progress node_id -> DB id 映射），parent_id 两遍回填。
"""

from __future__ import annotations

import json

from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))


def _canned(nodes_json: str) -> list[LLMResponse]:
    return [
        _resp('{"topic":"React","level":"零基础","goal":"做项目","daily_minutes":60,"deadline":null,"needs_clarify":false,"clarify_questions":[]}'),
        _resp(nodes_json),
        _resp('{"stages":[{"index":0,"name":"基础","goal":"g","difficulty":1,"estimated_weeks":1,"topics":[],"milestone":"m"},{"index":1,"name":"进阶","goal":"g","difficulty":3,"estimated_weeks":1,"topics":[],"milestone":"m"},{"index":2,"name":"实战","goal":"g","difficulty":5,"estimated_weeks":1,"topics":[],"milestone":"m"}]}'),
        _resp('{"schedule":[{"date":"2026-07-16","stage_index":0,"tasks":["学 JS"],"est_minutes":60}]}'),
        _resp('{"report":{"dependency_closure":{"status":"pass","details":""},"difficulty_step":{"status":"pass","details":""},"cognitive_load":{"status":"pass","details":""},"milestone_feasibility":{"status":"pass","details":""},"gap_analysis":{"status":"pass","details":""}},"passed":true,"fixes":[]}'),
    ]


class _Provider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._r = list(responses)

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        return self._r.pop(0)


def _make_plan(client, auth_headers, monkeypatch, provider: LLMProvider) -> int:
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: provider)
    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "学 React"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    for block in r.text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: ") and '"plan_id"' in line:
                return json.loads(line[6:])["plan_id"]
    raise AssertionError(f"未拿到 plan_id: {r.text}")


def _nodes_of(plan_id: int):
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        return db.query(models.KnowledgeNode).filter_by(plan_id=plan_id).all()
    finally:
        db.close()


def test_nodes_persisted_with_sid_and_parent(client, auth_headers, monkeypatch):
    """生成后 knowledge_map.nodes 落库：sid/字段保留，parent_id 按 sid 映射回填；悬空父引用忽略。"""
    nodes = (
        '{"nodes":['
        '{"id":"k1","name":"JS","difficulty":1,"must_learn":true,"parent_id":null},'
        '{"id":"k2","name":"React","difficulty":3,"must_learn":true,"parent_id":"k1"},'
        '{"id":"k3","name":"孤儿","difficulty":2,"must_learn":false,"parent_id":"k99"}],'
        '"edges":[{"from":"k1","to":"k2","relation":"前置"}]}'
    )
    plan_id = _make_plan(client, auth_headers, monkeypatch, _Provider(_canned(nodes)))

    rows = _nodes_of(plan_id)
    assert len(rows) == 3
    by_sid = {r.sid: r for r in rows}
    assert by_sid["k1"].name == "JS"
    assert by_sid["k1"].difficulty == 1
    assert by_sid["k1"].must_learn is True
    assert by_sid["k1"].parent_id is None
    assert by_sid["k2"].parent_id == by_sid["k1"].id  # sid -> DB id 映射
    assert by_sid["k3"].parent_id is None  # 悬空父引用 k99 -> 忽略
    assert all(r.plan_id == plan_id for r in rows)


def test_nodes_empty_map_no_rows(client, auth_headers, monkeypatch):
    """空 knowledge_map（降级）-> 不落行，plan 仍正常持久化。"""
    plan_id = _make_plan(client, auth_headers, monkeypatch, _Provider(_canned('{"nodes":[],"edges":[]}')))
    assert _nodes_of(plan_id) == []


def test_nodes_missing_name_skipped(client, auth_headers, monkeypatch):
    """缺 name 的节点跳过（DB name 非空约束），正常节点不受影响。"""
    nodes = (
        '{"nodes":['
        '{"id":"k1","name":"JS","difficulty":1,"must_learn":true,"parent_id":null},'
        '{"id":"k2","name":"","difficulty":2,"must_learn":false,"parent_id":null}],"edges":[]}'
    )
    plan_id = _make_plan(client, auth_headers, monkeypatch, _Provider(_canned(nodes)))
    rows = _nodes_of(plan_id)
    assert [r.sid for r in rows] == ["k1"]

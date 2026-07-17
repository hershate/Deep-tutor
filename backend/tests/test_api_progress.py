"""进度端点测试（plan/03 §一 progress，P2 iter 39）。

POST /api/progress（覆盖写 + 掌握度加权 + 间隔重复）；GET /api/progress/{plan_id} 总览。
前置：mock 生成建 plan（knowledge_nodes 落库，sid k1/k2）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))


_CANNED = [
    _resp('{"topic":"React","level":"零基础","goal":"做项目","daily_minutes":60,"deadline":null,"needs_clarify":false,"clarify_questions":[]}'),
    _resp('{"nodes":[{"id":"k1","name":"JS","difficulty":1,"must_learn":true,"parent_id":null},{"id":"k2","name":"React","difficulty":3,"must_learn":true,"parent_id":"k1"}],"edges":[{"from":"k1","to":"k2","relation":"前置"}]}'),
    _resp('{"stages":[{"index":0,"name":"基础","goal":"g","difficulty":1,"estimated_weeks":1,"topics":[],"milestone":"m"},{"index":1,"name":"进阶","goal":"g","difficulty":3,"estimated_weeks":1,"topics":[],"milestone":"m"},{"index":2,"name":"实战","goal":"g","difficulty":5,"estimated_weeks":1,"topics":[],"milestone":"m"}]}'),
    _resp('{"schedule":[{"date":"2026-07-16","stage_index":0,"tasks":["学 JS"],"est_minutes":60}]}'),
    _resp('{"report":{"dependency_closure":{"status":"pass","details":""},"difficulty_step":{"status":"pass","details":""},"cognitive_load":{"status":"pass","details":""},"milestone_feasibility":{"status":"pass","details":""},"gap_analysis":{"status":"pass","details":""}},"passed":true,"fixes":[]}'),
]


class CannedProvider(LLMProvider):
    def __init__(self) -> None:
        self.i = 0

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        r = _CANNED[self.i]
        self.i += 1
        return r


def _utc_today():
    return datetime.now(timezone.utc).date()


def _make_plan(client, auth_headers, monkeypatch) -> int:
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda: CannedProvider())
    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "学 React"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    for block in r.text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: ") and '"plan_id"' in line:
                return json.loads(line[6:])["plan_id"]
    raise AssertionError(f"未拿到 plan_id: {r.text}")


def test_update_progress_create_and_overwrite(client, auth_headers, monkeypatch):
    """首次 POST 建行；再次 POST 覆盖写（幂等 upsert），quiz_score 加权 mastery。"""
    plan_id = _make_plan(client, auth_headers, monkeypatch)

    r = client.post("/api/progress", json={"plan_id": plan_id, "node_id": "k1", "status": "learning"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] is True
    assert body["status"] == "learning"
    assert body["mastery"] == 0.0  # learning 无 quiz -> 不变
    assert body["next_review"] == (_utc_today() + timedelta(days=1)).isoformat()  # mastery 0 <0.5

    # 覆盖写：done + quiz_score=1.0 -> 0.7*1 + 0.3*0
    r = client.post("/api/progress", json={"plan_id": plan_id, "node_id": "k1", "status": "done", "quiz_score": 1.0}, headers=auth_headers)
    assert r.json()["mastery"] == 0.7
    # 再次 quiz 满分：0.7*1 + 0.3*0.7 = 0.91（近期权重高）
    r = client.post("/api/progress", json={"plan_id": plan_id, "node_id": "k1", "status": "done", "quiz_score": 1.0}, headers=auth_headers)
    body = r.json()
    assert body["mastery"] == 0.91
    assert body["next_review"] == (_utc_today() + timedelta(days=14)).isoformat()  # >=0.85

    # DB 仍一行（upsert 不重复建行）
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        assert db.query(models.Progress).filter_by(plan_id=plan_id).count() == 1
    finally:
        db.close()


def test_update_progress_done_without_quiz(client, auth_headers, monkeypatch):
    """主观标记 done（无 quiz）-> mastery=max(old, 0.8)。"""
    plan_id = _make_plan(client, auth_headers, monkeypatch)
    r = client.post("/api/progress", json={"plan_id": plan_id, "node_id": "k2", "status": "done"}, headers=auth_headers)
    assert r.json()["mastery"] == 0.8


def test_update_progress_node_not_found(client, auth_headers, monkeypatch):
    """节点不存在 -> 忽略 + warning（plan/02 §9 errors，不报错不落行）。"""
    plan_id = _make_plan(client, auth_headers, monkeypatch)
    r = client.post("/api/progress", json={"plan_id": plan_id, "node_id": "k999", "status": "done"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] is False
    assert "node_not_found" in body["warning"]


def test_update_progress_plan_not_found(client, auth_headers):
    r = client.post("/api/progress", json={"plan_id": 999999, "node_id": "k1", "status": "done"}, headers=auth_headers)
    assert r.status_code == 404


def test_update_progress_invalid_status_422(client, auth_headers, monkeypatch):
    plan_id = _make_plan(client, auth_headers, monkeypatch)
    r = client.post("/api/progress", json={"plan_id": plan_id, "node_id": "k1", "status": "mastered"}, headers=auth_headers)
    assert r.status_code == 422


def test_get_progress_overview(client, auth_headers, monkeypatch):
    """总览：全部节点合并 progress（无行 -> not_started/0.0/无复习日）+ mastery_avg。"""
    plan_id = _make_plan(client, auth_headers, monkeypatch)
    client.post("/api/progress", json={"plan_id": plan_id, "node_id": "k1", "status": "done"}, headers=auth_headers)

    r = client.get(f"/api/progress/{plan_id}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan_id"] == plan_id
    by_sid = {n["node_id"]: n for n in body["nodes"]}
    assert set(by_sid) == {"k1", "k2"}
    assert by_sid["k1"]["status"] == "done"
    assert by_sid["k1"]["mastery"] == 0.8
    assert by_sid["k1"]["name"] == "JS"
    assert by_sid["k1"]["next_review"] == (_utc_today() + timedelta(days=7)).isoformat()  # 0.8 <0.85
    assert by_sid["k2"]["status"] == "not_started"
    assert by_sid["k2"]["mastery"] == 0.0
    assert by_sid["k2"]["next_review"] is None
    assert body["mastery_avg"] == 0.4  # (0.8 + 0.0) / 2


def test_get_progress_empty_and_not_found(client, auth_headers, monkeypatch):
    """无更新时全 not_started/mastery_avg 0；plan 不存在 -> 404。"""
    plan_id = _make_plan(client, auth_headers, monkeypatch)
    body = client.get(f"/api/progress/{plan_id}", headers=auth_headers).json()
    assert body["mastery_avg"] == 0.0
    assert all(n["status"] == "not_started" for n in body["nodes"])

    r = client.get("/api/progress/999999", headers=auth_headers)
    assert r.status_code == 404

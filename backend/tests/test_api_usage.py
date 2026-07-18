"""用量统计 API 测试：生成后 api_usage 应有记录。"""

from __future__ import annotations

from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _resp(c):
    return LLMResponse(content=c, usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))


_CANNED = [
    _resp('{"topic":"React","level":"零基础","goal":"做项目","daily_minutes":60,"deadline":null,"needs_clarify":false,"clarify_questions":[]}'),
    _resp('{"nodes":[{"id":"k1","name":"JS","difficulty":1,"must_learn":true,"parent_id":null}],"edges":[]}'),
    _resp('{"stages":[{"index":0,"name":"基础","goal":"g","difficulty":1,"estimated_weeks":1,"topics":[{"name":"JS","why":"w","must_learn":true}],"milestone":"m"},{"index":1,"name":"进阶","goal":"g","difficulty":3,"estimated_weeks":1,"topics":[],"milestone":"m"},{"index":2,"name":"实战","goal":"g","difficulty":5,"estimated_weeks":1,"topics":[],"milestone":"m"}]}'),
    _resp('{"schedule":[{"date":"2026-07-16","stage_index":0,"tasks":["学 JS"],"est_minutes":60}]}'),
    _resp('{"report":{"dependency_closure":{"status":"pass","details":""},"difficulty_step":{"status":"pass","details":""},"cognitive_load":{"status":"pass","details":""},"milestone_feasibility":{"status":"pass","details":""},"gap_analysis":{"status":"pass","details":""}},"passed":true,"fixes":[]}'),
]


class CannedProvider(LLMProvider):
    def __init__(self):
        self.i = 0

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        r = _CANNED[self.i]
        self.i += 1
        return r


def test_usage_after_generate(client, auth_headers, monkeypatch):
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: CannedProvider())

    # 生成前 usage 为空
    u0 = client.get("/api/usage", headers=auth_headers).json()
    assert u0["total_tokens"] == 0

    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "学 React"}, headers=auth_headers)

    # 生成后 usage 有记录（5 次 LLM 调用）
    u1 = client.get("/api/usage", headers=auth_headers).json()
    assert u1["calls"] >= 5
    assert u1["total_tokens"] > 0
    assert u1["total_cost"] > 0
    # iter 164：今日费用与日预算（刚产生的调用必然落在今日）
    assert u1["today_cost"] == u1["total_cost"]
    assert u1["daily_budget_usd"] > 0


def test_usage_today_cost_excludes_old_rows(client, auth_headers):
    """iter 164：昨日及更早的调用不计入 today_cost（但计入 total_cost）。"""
    from datetime import datetime, timedelta, timezone

    from app.db import models
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(admin)
        db.flush()
        utc_now = datetime.now(timezone.utc).replace(tzinfo=None)  # 与 created_at naive UTC 同口径
        db.add_all(
            [
                models.ApiUsage(user_id=admin.id, cost=0.5, tokens_in=1, tokens_out=1, cache_read=0,
                                created_at=utc_now - timedelta(days=1)),
                models.ApiUsage(user_id=admin.id, cost=0.2, tokens_in=1, tokens_out=1, cache_read=0,
                                created_at=utc_now),
            ]
        )
        db.commit()
    finally:
        db.close()
    u = client.get("/api/usage", headers=auth_headers).json()
    assert abs(u["today_cost"] - 0.2) < 1e-9
    assert abs(u["total_cost"] - 0.7) < 1e-9

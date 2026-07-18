"""澄清续接 API 测试（mock LLM，共享 provider 跨 generate + clarify）。"""

from __future__ import annotations

import json

from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _resp(c):
    return LLMResponse(content=c, usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))


_NEEDS_CLARIFY = _resp(
    '{"topic":"编程","level":null,"goal":null,"daily_minutes":null,"deadline":null,"needs_clarify":true,"clarify_questions":["想学哪方面编程？","每天能投入多少时间？"]}'
)

# 澄清后续：完整 intent + map + stages + schedule + validate
_RESUME = [
    _resp('{"topic":"React","level":"零基础","goal":"做项目","daily_minutes":60,"deadline":null,"needs_clarify":false,"clarify_questions":[]}'),
    _resp('{"nodes":[{"id":"k1","name":"JS","difficulty":1,"must_learn":true,"parent_id":null}],"edges":[]}'),
    _resp('{"stages":[{"index":0,"name":"基础","goal":"g","difficulty":1,"estimated_weeks":1,"topics":[{"name":"JS","why":"w","must_learn":true}],"milestone":"m"},{"index":1,"name":"进阶","goal":"g","difficulty":3,"estimated_weeks":1,"topics":[],"milestone":"m"},{"index":2,"name":"实战","goal":"g","difficulty":5,"estimated_weeks":1,"topics":[],"milestone":"m"}]}'),
    _resp('{"schedule":[{"date":"2026-07-16","stage_index":0,"tasks":["学 JS"],"est_minutes":60}]}'),
    _resp('{"report":{"dependency_closure":{"status":"pass","details":""},"difficulty_step":{"status":"pass","details":""},"cognitive_load":{"status":"pass","details":""},"milestone_feasibility":{"status":"pass","details":""},"gap_analysis":{"status":"pass","details":""}},"passed":true,"fixes":[]}'),
]


class ClarifyProvider(LLMProvider):
    """首次 understand_intent 返回 needs_clarify；之后按序返回 resume 响应。"""

    def __init__(self):
        self.i = 0

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        if self.i == 0:
            self.i += 1
            return _NEEDS_CLARIFY
        r = _RESUME[self.i - 1]
        self.i += 1
        return r


def _parse_sse(text):
    out = []
    for block in text.split("\n\n"):
        ev = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if ev:
            out.append((ev, json.loads(data) if data else {}))
    return out


def test_clarify_flow(client, auth_headers, monkeypatch):
    from app.api import plans as plans_mod

    provider = ClarifyProvider()
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: provider)

    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]

    # 1) generate -> 需澄清
    r1 = client.post(
        "/api/plans/generate", json={"session_id": sid, "raw_text": "我想学编程"}, headers=auth_headers
    )
    assert r1.status_code == 200
    ev1 = _parse_sse(r1.text)
    assert any(e == "clarify_needed" for e, _ in ev1), ev1

    # 2) 无待澄清任务时 clarify -> 409（用一个新会话）
    sid2 = client.post("/api/sessions", json={"title": "t2"}, headers=auth_headers).json()["session_id"]
    r_conflict = client.post(
        f"/api/plans/{sid2}/clarify", json={"answers": {}}, headers=auth_headers
    )
    assert r_conflict.status_code == 409

    # 3) 提交澄清答案 -> 续接生成完成
    r2 = client.post(
        f"/api/plans/{sid}/clarify",
        json={"answers": {"topic": "React", "level": "零基础", "daily_minutes": 60}},
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text
    ev2 = _parse_sse(r2.text)
    assert any(e == "done" for e, _ in ev2), ev2
    done = next(d for e, d in ev2 if e == "done")
    plan_id = done["plan_id"]

    # plan 已持久化
    detail = client.get(f"/api/plans/{plan_id}", headers=auth_headers).json()
    assert detail["id"] == plan_id

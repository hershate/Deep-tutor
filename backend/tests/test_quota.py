"""配额防护测试（plan/09 §十八 速率限制 / §二十 配额 / plan/11 §二 §九，iter 86）。

覆盖：materials content-hash 去重、每会话资料数 ≤20、/materials/url 与 /export 限频、
/plans/generate 每用户并发 1 + 全局 ≤2、schedule ≤365 天截断、raw_text ≤10k 截断。
"""

from __future__ import annotations

import json

from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.tools.base import ToolOutput


def _resp(c):
    return LLMResponse(content=c, usage=LLMUsage(input_tokens=10, output_tokens=5, model="claude-sonnet-5"))


_CANNED = [
    _resp('{"topic":"React","level":"零基础","goal":"做项目","daily_minutes":60,"deadline":null,"needs_clarify":false,"clarify_questions":[]}'),
    _resp('{"nodes":[{"id":"k1","name":"JS","difficulty":1,"must_learn":true,"parent_id":null}],"edges":[]}'),
    _resp('{"stages":[{"index":0,"name":"基础","goal":"g","difficulty":1,"estimated_weeks":1,"topics":[{"name":"JS","why":"w","must_learn":true}],"milestone":"m"}]}'),
    _resp('{"schedule":[{"date":"2026-07-16","stage_index":0,"tasks":["学 JS"],"est_minutes":60}]}'),
    _resp('{"report":{"dependency_closure":{"status":"pass","details":""},"difficulty_step":{"status":"pass","details":""},"cognitive_load":{"status":"pass","details":""},"milestone_feasibility":{"status":"pass","details":""},"gap_analysis":{"status":"pass","details":""}},"passed":true,"fixes":[]}'),
]


class CannedProvider(LLMProvider):
    def __init__(self):
        self.i = 0

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        r = _CANNED[self.i % len(_CANNED)]
        self.i += 1
        return r


def _new_session(client, auth_headers, title="t"):
    return client.post("/api/sessions", json={"title": title}, headers=auth_headers).json()["session_id"]


# --- content hash 去重（plan/11 §九）---


def test_text_material_dedup_by_hash(client, auth_headers):
    sid = _new_session(client, auth_headers)
    r1 = client.post("/api/materials/text", json={"session_id": sid, "text": "同一份笔记"}, headers=auth_headers)
    r2 = client.post("/api/materials/text", json={"session_id": sid, "text": "同一份笔记"}, headers=auth_headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["material_id"] == r2.json()["material_id"]
    assert r2.json()["meta"].get("deduped") is True

    r3 = client.post("/api/materials/text", json={"session_id": sid, "text": "另一份"}, headers=auth_headers)
    assert r3.json()["material_id"] != r1.json()["material_id"]


def test_upload_material_dedup_by_hash(client, auth_headers):
    sid = _new_session(client, auth_headers)
    body = "# 标题\n\n同一份文件内容".encode("utf-8")
    f = lambda: {"file": ("a.md", body, "text/markdown")}  # noqa: E731
    r1 = client.post("/api/materials/upload", data={"session_id": sid}, files=f(), headers=auth_headers)
    r2 = client.post("/api/materials/upload", data={"session_id": sid}, files=f(), headers=auth_headers)
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    assert r1.json()["material_id"] == r2.json()["material_id"]
    assert r2.json()["meta"].get("deduped") is True


# --- 每会话资料数 ≤20（plan/09 §二十）---


def test_materials_count_quota(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.api.materials._MAX_MATERIALS_PER_SESSION", 2)
    sid = _new_session(client, auth_headers)
    for i in range(2):
        r = client.post("/api/materials/text", json={"session_id": sid, "text": f"笔记 {i}"}, headers=auth_headers)
        assert r.status_code == 200
    r = client.post("/api/materials/text", json={"session_id": sid, "text": "第三条"}, headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "quota_exceeded"


# --- 速率限制（plan/09 §十八）---


def _stub_parse(monkeypatch):
    from app.api import materials as materials_mod

    async def _fake(inp, ctx):
        return ToolOutput(tool="parse_material", status="ok", data={"text": "正文", "meta": {}})

    monkeypatch.setattr(materials_mod, "parse_material", _fake)


def test_url_rate_limit(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.api.materials._URL_RATE_PER_MIN", 2)
    _stub_parse(monkeypatch)
    sid = _new_session(client, auth_headers)
    for i in range(2):
        r = client.post("/api/materials/url", json={"session_id": sid, "url": f"https://example.com/{i}"}, headers=auth_headers)
        assert r.status_code == 200, r.text
    r = client.post("/api/materials/url", json={"session_id": sid, "url": "https://example.com/2"}, headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"


def test_export_rate_limit(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.api.exports._EXPORT_RATE_PER_MIN", 1)
    monkeypatch.setattr("app.api.plans.get_provider", lambda *a, **k: CannedProvider())
    sid = _new_session(client, auth_headers)
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "学 React"}, headers=auth_headers)
    plan_id = None
    for block in r.text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: ") and '"plan_id"' in line:
                plan_id = json.loads(line[6:])["plan_id"]
    assert plan_id is not None

    r1 = client.post(f"/api/export/{plan_id}", json={"format": "md"}, headers=auth_headers)
    assert r1.status_code == 200
    r2 = client.post(f"/api/export/{plan_id}", json={"format": "md"}, headers=auth_headers)
    assert r2.status_code == 429
    assert r2.json()["error"]["code"] == "rate_limited"


# --- /plans/generate 并发配额（plan/09 §十八/§二十）---


def test_generate_per_user_concurrency_1(client, auth_headers, monkeypatch):
    """同用户另一会话生成中 -> 新会话 generate 409（plan/09 §十八 每用户并发 1）。"""
    from app.db import models
    from app.db.base import SessionLocal

    monkeypatch.setattr("app.api.plans.get_provider", lambda *a, **k: CannedProvider())
    sid_a = _new_session(client, auth_headers, "A")
    sid_b = _new_session(client, auth_headers, "B")
    db = SessionLocal()
    try:
        db.add(models.OrchestratorState(session_id=sid_a, status=models.OrchestratorStatus.running, partial_plan={}))
        db.commit()
    finally:
        db.close()

    r = client.post("/api/plans/generate", json={"session_id": sid_b, "raw_text": "学 Go"}, headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


def test_generate_global_concurrency_cap(client, auth_headers, monkeypatch):
    """全局同时生成 ≤2（plan/09 §二十 R31）：打满 -> 503 server_busy。"""
    monkeypatch.setattr("app.api.plans._MAX_GENERATE_GLOBAL", 0)  # 视为已打满
    monkeypatch.setattr("app.api.plans.get_provider", lambda *a, **k: CannedProvider())
    sid = _new_session(client, auth_headers)
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "学 Go"}, headers=auth_headers)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "server_busy"


# --- raw_text ≤10k 截断（plan/09 §十）---


def test_generate_raw_text_truncated(client, auth_headers, monkeypatch):
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: CannedProvider())
    sid = _new_session(client, auth_headers)
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "x" * 20_000}, headers=auth_headers)
    assert r.status_code == 200
    assert len(plans_mod._STREAMS[sid].raw_text) == plans_mod._MAX_RAW_TEXT_CHARS


# --- schedule ≤365 天截断（plan/11 §二/§三）---


def test_schedule_truncated_to_365_days():
    from app.tools.base import ToolInput
    from app.tools.schedule_plan import _MAX_SCHEDULE_DAYS, check_schedule

    items = [{"date": f"2026-01-{(i % 28) + 1:02d}", "stage_index": 0, "tasks": ["t"], "est_minutes": 10} for i in range(400)]
    data = check_schedule({"schedule": items}, ToolInput(tool="schedule_plan", session_id="1", args={"daily_minutes": 60}))
    assert len(data["schedule"]) == _MAX_SCHEDULE_DAYS == 365
    assert data["truncated"] is True

    short = check_schedule({"schedule": items[:10]}, ToolInput(tool="schedule_plan", session_id="1", args={"daily_minutes": 60}))
    assert "truncated" not in short

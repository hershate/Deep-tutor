"""导出 API 测试（先 mock 生成建 plan，再导出）。"""

from __future__ import annotations

from pathlib import Path

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


def _make_plan(client, auth_headers):
    from app.api import plans as plans_mod
    # 每测试新建 provider 实例（响应序归零）
    plans_mod.get_provider = lambda: CannedProvider()
    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "学 React"}, headers=auth_headers)
    import json as _json
    for block in r.text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: ") and '"plan_id"' in line:
                return _json.loads(line[6:])["plan_id"]
    raise AssertionError(f"未拿到 plan_id: {r.text}")


def test_export_md(client, auth_headers):
    plan_id = _make_plan(client, auth_headers)
    r = client.post(f"/api/export/{plan_id}", json={"format": "md"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "md"
    assert body["file_url"].endswith(".md")
    assert Path(body["file_url"]).exists()


def test_export_pdf_degrades_to_md(client, auth_headers):
    plan_id = _make_plan(client, auth_headers)
    r = client.post(f"/api/export/{plan_id}", json={"format": "pdf"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "md"  # 降级
    assert "warning" in body


def test_export_plan_not_found(client, auth_headers):
    r = client.post("/api/export/999999", json={"format": "md"}, headers=auth_headers)
    assert r.status_code == 404

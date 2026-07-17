"""POST /plans/{id}/materials 端点测试（P2 自学材料，iter 36）。

先 mock 生成建 plan（CannedProvider 同 test_api_export），再调 materials 端点。
"""

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


class _MaterialsProvider(LLMProvider):
    """materials 端点专用 provider（返回 StudyMaterialsSpec JSON）。"""

    def __init__(self, responses):
        self._r = list(responses)

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        return self._r.pop(0) if self._r else _resp("{}")


def _make_plan(client, auth_headers):
    from app.api import plans as plans_mod

    plans_mod.get_provider = lambda: CannedProvider()
    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "学 React"}, headers=auth_headers)
    import json as _json

    for block in r.text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: ") and '"plan_id"' in line:
                return _json.loads(line[6:])["plan_id"]
    raise AssertionError(f"未拿到 plan_id: {r.text}")


def test_materials_ok(client, auth_headers, monkeypatch):
    """LLM 合法规约 -> 200 + markdown + unit_count。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)
    spec_json = (
        '{"title":"React 自学材料","units":['
        '{"title":"JS 基础","objectives":["学完后能够写基础函数"],"prerequisites":["无"],'
        '"estimated_time":"45 min","content_md":"### 概述\\n\\nJS 是交互逻辑。\\n\\n### 实践练习\\n\\n1. 写一个函数\\n2. 写一个循环"},'
        '{"title":"React 组件","objectives":["学完后能够写函数组件"],"prerequisites":["JS 基础"],'
        '"estimated_time":"60 min","content_md":"### 概述\\n\\n组件是 UI 单元。"}]}'
    )
    monkeypatch.setattr(plans_mod, "get_provider", lambda: _MaterialsProvider([_resp(spec_json)]))
    r = client.post(f"/api/plans/{plan_id}/materials", json={"depth": "full"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unit_count"] == 2
    assert "# React 自学材料" in body["markdown"]
    assert "## 1. JS 基础" in body["markdown"]
    assert "## 2. React 组件" in body["markdown"]
    assert "JS 是交互逻辑。" in body["markdown"]
    assert "warning" not in body  # 未降级


def test_materials_degrade_no_provider(client, auth_headers, monkeypatch):
    """无 LLM provider（无 key）-> 降级 default_materials_from_plan，仍 200 + markdown + warning。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)

    def _raise():
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    monkeypatch.setattr(plans_mod, "get_provider", _raise)
    r = client.post(f"/api/plans/{plan_id}/materials", json={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unit_count"] >= 1
    assert "# React" in body["markdown"]  # 默认规约取 intent.topic
    assert "warning" in body  # 降级提示


def test_materials_plan_not_found(client, auth_headers):
    r = client.post("/api/plans/999999/materials", json={}, headers=auth_headers)
    assert r.status_code == 404


def test_materials_invalid_depth(client, auth_headers):
    """depth 非 brief|full -> 422。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)
    plans_mod.get_provider = lambda: CannedProvider()
    r = client.post(f"/api/plans/{plan_id}/materials", json={"depth": "verbose"}, headers=auth_headers)
    assert r.status_code == 422

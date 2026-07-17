"""端到端冒烟测试（step 8，plan/04 §四 第一步动作）：完整用户旅程 via TestClient + mock LLM。

建会话 -> 加文本资料 -> 生成(SSE) -> 方案详情/列表 -> 用量 -> 设置 -> 导出。
验证 P0 全链路集成（含 material -> 生成注入 initial_upstream，他测均仅 raw_text）。
不联网（mock provider）；真机 Anthropic 冒烟需配置 key 后另跑。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))


# 5 个 LLM 工具的罐装响应（understand_intent / build_knowledge_map / design_stages / schedule_plan / validate_plan）
_CANNED = [
    _resp('{"topic":"React","level":"零基础","goal":"做项目","daily_minutes":60,"deadline":null,"needs_clarify":false,"clarify_questions":[]}'),
    _resp('{"nodes":[{"id":"k1","name":"JS","difficulty":1,"must_learn":true,"parent_id":null},{"id":"k2","name":"React","difficulty":3,"must_learn":true,"parent_id":"k1"}],"edges":[{"from":"k1","to":"k2","relation":"前置"}]}'),
    _resp('{"stages":[{"index":0,"name":"基础","goal":"g","difficulty":1,"estimated_weeks":1,"topics":[{"name":"JS","why":"w","must_learn":true}],"milestone":"m"},{"index":1,"name":"进阶","goal":"g","difficulty":3,"estimated_weeks":1,"topics":[],"milestone":"m"},{"index":2,"name":"实战","goal":"g","difficulty":5,"estimated_weeks":1,"topics":[],"milestone":"m"}]}'),
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


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
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


def test_e2e_plan_generation_smoke(client, auth_headers, monkeypatch):
    """完整用户旅程冒烟：会话+资料 -> 生成 -> 详情/列表 -> 用量 -> 设置 -> 导出。"""
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda: CannedProvider())

    # 1. 建会话
    sid = client.post("/api/sessions", json={"title": "e2e 冒烟"}, headers=auth_headers).json()["session_id"]
    assert sid > 0

    # 2. 加文本资料（验证 material -> 生成集成：parsed 注入 initial_upstream）
    mat = client.post(
        "/api/materials/text",
        json={"session_id": sid, "text": "React 是用于构建用户界面的声明式库。"},
        headers=auth_headers,
    ).json()
    assert mat["material_id"] > 0

    # 3. 生成（SSE 流）-> done
    r = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "我想学 React，零基础"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    ev_names = [e for e, _ in events]
    assert "tool_start" in ev_names and "tool_end" in ev_names
    assert "done" in ev_names
    plan_id = next(d for e, d in events if e == "done")["plan_id"]
    assert plan_id > 0

    # 4. 方案详情 + 列表
    detail = client.get(f"/api/plans/{plan_id}", headers=auth_headers).json()
    assert detail["id"] == plan_id
    assert isinstance(detail["plan"], dict)
    lst = client.get(f"/api/plans?session_id={sid}", headers=auth_headers).json()
    assert any(p["id"] == plan_id for p in lst["items"])

    # 5. 用量（api_usage 已在 done-callback 落库；5 次 LLM 调用）
    usage = client.get("/api/usage", headers=auth_headers).json()
    assert usage["calls"] >= 5
    assert usage["total_tokens"] > 0
    assert usage["total_cost"] > 0

    # 6. 设置（只读，不泄密）
    s = client.get("/api/settings", headers=auth_headers).json()
    assert s["auth_token_set"] is True
    assert "anthropic_api_key" not in s

    # 7. 导出 md
    exp = client.post(f"/api/export/{plan_id}", json={"format": "md"}, headers=auth_headers).json()
    assert exp["format"] == "md"
    assert Path(exp["file_url"]).exists()

    # 8. 导出 slides（build_plan_slides LLM -> 渲染自包含 HTML）
    from app.api import exports as exports_mod
    from app.core import settings as settings_mod

    class _SlidesProvider(LLMProvider):
        async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
            return _resp(
                '{"title":"React","theme":{"--bg":"#0a0e1a","--accent":"#3b82f6"},'
                '"transition":"trans-fade","slides":['
                '{"title":"React","content_html":"<p>零基础</p>","layout":"center","animation":"anim-fade-in"}]}'
            )

    monkeypatch.setattr(exports_mod, "get_provider", lambda: _SlidesProvider())
    monkeypatch.setattr(settings_mod.settings, "skills_dir", str(Path(__file__).resolve().parents[2] / "skills"))
    exp_slides = client.post(f"/api/export/{plan_id}", json={"format": "slides"}, headers=auth_headers).json()
    assert exp_slides["format"] == "html"
    assert exp_slides["slide_count"] >= 1
    assert "<html" in exp_slides["html"]
    assert "React" in exp_slides["html"]

    # 9. 自学材料（generate_study_materials LLM -> 渲染 markdown，P2）
    class _MaterialsProvider(LLMProvider):
        async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
            return _resp(
                '{"title":"React 自学材料","units":['
                '{"title":"JS 基础","objectives":["写函数"],"prerequisites":["无"],'
                '"estimated_time":"45 min","content_md":"### 概述\\n\\nJS 是交互逻辑。"}]}'
            )

    monkeypatch.setattr(plans_mod, "get_provider", lambda: _MaterialsProvider())
    mat_res = client.post(f"/api/plans/{plan_id}/materials", json={"depth": "full"}, headers=auth_headers)
    assert mat_res.status_code == 200, mat_res.text
    mat_body = mat_res.json()
    assert mat_body["unit_count"] == 1
    assert "# React 自学材料" in mat_body["markdown"]
    assert "## 1. JS 基础" in mat_body["markdown"]

    # 10. 测验（generate_quiz LLM -> 题目 JSON，P2）
    class _QuizProvider(LLMProvider):
        async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
            return _resp(
                '{"title":"React 测验","quizzes":['
                '{"node_id":"k1","node_name":"JS","type":"choice","question":"声明常量用？",'
                '"options":["var","const"],"answer":"const","explanation":"const 声明常量。"}]}'
            )

    monkeypatch.setattr(plans_mod, "get_provider", lambda: _QuizProvider())
    quiz_res = client.post(f"/api/plans/{plan_id}/quiz", json={}, headers=auth_headers)
    assert quiz_res.status_code == 200, quiz_res.text
    quiz_body = quiz_res.json()
    assert quiz_body["quiz_count"] == 1
    assert quiz_body["quizzes"][0]["type"] == "choice"
    assert quiz_body["quizzes"][0]["answer"] == "const"

    # 11. 进度（P2）：测验回写（quiz_score）-> 掌握度加权；总览含 nodes + mastery_avg
    prog = client.post(
        "/api/progress",
        json={"plan_id": plan_id, "node_id": "k1", "status": "done", "quiz_score": 1.0},
        headers=auth_headers,
    )
    assert prog.status_code == 200, prog.text
    assert prog.json()["updated"] is True
    assert prog.json()["mastery"] == 0.7  # 0.7*1.0 + 0.3*0.0

    overview = client.get(f"/api/progress/{plan_id}", headers=auth_headers)
    assert overview.status_code == 200, overview.text
    ov = overview.json()
    by_sid = {n["node_id"]: n for n in ov["nodes"]}
    assert set(by_sid) == {"k1", "k2"}
    assert by_sid["k1"]["status"] == "done"
    assert by_sid["k1"]["mastery"] == 0.7
    assert by_sid["k1"]["next_review"] is not None
    assert by_sid["k2"]["status"] == "not_started"
    assert ov["mastery_avg"] == 0.35  # (0.7 + 0.0) / 2

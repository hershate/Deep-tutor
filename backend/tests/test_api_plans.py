"""plans/generate SSE 端点测试（mock LLM provider，不联网）。"""

from __future__ import annotations

import json

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


def _session(client, auth_headers):
    return client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]


def test_generate_happy_path(client, auth_headers, monkeypatch):
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda: CannedProvider())

    sid = _session(client, auth_headers)
    r = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "我想学 React，零基础"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    ev_names = [e for e, _ in events]
    assert "tool_start" in ev_names
    assert "tool_end" in ev_names
    assert "done" in ev_names

    done_data = next(d for e, d in events if e == "done")
    plan_id = done_data["plan_id"]
    assert plan_id > 0

    # 列表
    lst = client.get(f"/api/plans?session_id={sid}", headers=auth_headers).json()
    assert lst["total"] >= 1
    assert any(p["id"] == plan_id for p in lst["items"])

    # 详情
    detail = client.get(f"/api/plans/{plan_id}", headers=auth_headers).json()
    assert detail["id"] == plan_id
    plan = detail["plan"]
    # plan 是 export_plan 输出 或 组装 dict
    assert isinstance(plan, dict)


def test_generate_no_provider_emits_error(client, auth_headers, monkeypatch):
    from app.api import plans as plans_mod

    def _raise():
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    monkeypatch.setattr(plans_mod, "get_provider", _raise)
    sid = _session(client, auth_headers)
    r = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "学 X"},
        headers=auth_headers,
    )
    assert r.status_code == 200  # SSE 流本身 200，错误在事件里
    events = _parse_sse(r.text)
    assert any(e == "error" for e, _ in events)


def test_generate_session_not_found(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.api.plans.get_provider", lambda: CannedProvider())
    r = client.post(
        "/api/plans/generate",
        json={"session_id": 999999, "raw_text": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_generate_emits_increasing_event_ids(client, auth_headers, monkeypatch):
    """SSE 每事件带递增 id（plan/10 §十二 续接游标），终态持久化 last_event_id。"""
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda: CannedProvider())
    sid = _session(client, auth_headers)
    r = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "学 React"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    # 解析所有 id: 行
    ids: list[int] = []
    for block in r.text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("id: "):
                ids.append(int(line[4:]))
    assert ids, "SSE 未发出任何 id 行"
    assert ids == sorted(ids), "id 非递增"
    assert len(ids) == len(set(ids)), "id 有重复"

    # done 事件应携带最大的 id
    events = _parse_sse(r.text)
    assert any(e == "done" for e, _ in events)

    # 终态 last_event_id 持久化到 orchestrator_state
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        st = db.get(models.OrchestratorState, sid)
        assert st is not None
        assert st.status == models.OrchestratorStatus.done
        assert st.last_event_id == str(ids[-1])
    finally:
        db.close()

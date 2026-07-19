"""失败态持久化 + POST /plans/{session_id}/retry 测试（iter 184 B4）。

覆盖：失败后 partial_plan.raw_text 与 last_error 落库 / retry 重跑到 done /
running·done 态 retry 409 / 旧失败会话（空 partial 无材料）409 / 跨用户 404 /
会话列表 error 字段暴露。
"""

from __future__ import annotations

import json

from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))


# 与 test_api_plans 同款罐装响应（5 个 LLM 工具；intent topic="React"）
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


def _session(client, auth_headers) -> int:
    return client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]


def _generate_no_provider(client, auth_headers, monkeypatch, sid: int, raw: str = "我想学 Rust"):
    """以无 provider 制造失败终态。"""
    from app.api import plans as plans_mod

    def _raise(*a, **k):
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    monkeypatch.setattr(plans_mod, "get_provider", _raise)
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": raw}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert any(e == "error" for e, _ in _parse_sse(r.text))


def _state(sid: int):
    from app.db.base import SessionLocal
    from app.db import models

    db = SessionLocal()
    try:
        st = db.get(models.OrchestratorState, sid)
        return None if st is None else {
            "status": st.status.value,
            "partial_plan": st.partial_plan,
            "last_error": st.last_error,
            "budget_used": st.budget_used,
        }
    finally:
        db.close()


def test_failure_persists_raw_text_and_last_error(client, auth_headers, monkeypatch):
    sid = _session(client, auth_headers)
    _generate_no_provider(client, auth_headers, monkeypatch, sid, raw="我想学 Go")
    st = _state(sid)
    assert st["status"] == "error"
    assert st["partial_plan"]["raw_text"] == "我想学 Go"
    assert st["last_error"]["code"] == "no_llm_provider"
    assert "ANTHROPIC_API_KEY" in st["last_error"]["message"]

    # 会话列表/详情暴露 error 字段
    d = client.get(f"/api/sessions/{sid}", headers=auth_headers).json()
    assert d["error"]["code"] == "no_llm_provider"
    lst = client.get("/api/sessions", headers=auth_headers).json()
    assert lst["items"][0]["error"]["code"] == "no_llm_provider"


def test_retry_reruns_to_done(client, auth_headers, monkeypatch):
    from app.api import plans as plans_mod

    # 标题取 raw_text 前缀截断（Home 同款自动标题），retry 成功后应被 topic 回填（B3 联动）
    raw = "我想学 Rust"
    sid = client.post("/api/sessions", json={"title": raw[:40]}, headers=auth_headers).json()["session_id"]
    _generate_no_provider(client, auth_headers, monkeypatch, sid, raw=raw)
    # 修复 provider -> retry 重跑成功
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: CannedProvider())
    r = client.post(f"/api/plans/{sid}/retry", headers=auth_headers)
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    assert any(e == "done" for e, _ in events), r.text
    st = _state(sid)
    assert st["status"] == "done"
    assert st["last_error"] == {}  # 成功清空
    d = client.get(f"/api/sessions/{sid}", headers=auth_headers).json()
    assert d["title"] == "React"  # CannedProvider intent.topic
    assert d["error"] is None


def test_retry_conflict_states(client, auth_headers, monkeypatch):
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: CannedProvider())
    # idle 会话（无状态行）-> 409
    sid = _session(client, auth_headers)
    assert client.post(f"/api/plans/{sid}/retry", headers=auth_headers).status_code == 409
    # done 会话 -> 409
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "x"}, headers=auth_headers)
    assert "event: done" in r.text
    assert client.post(f"/api/plans/{sid}/retry", headers=auth_headers).status_code == 409


def test_retry_lost_raw_text_409(client, auth_headers):
    """B4 前的旧失败会话（partial_plan 为空且无材料）-> 409 引导回首页。"""
    from app.db.base import SessionLocal
    from app.db import models

    sid = _session(client, auth_headers)
    db = SessionLocal()
    try:
        db.add(models.OrchestratorState(
            session_id=sid, status=models.OrchestratorStatus.error, trace_id="t", partial_plan={},
        ))
        db.commit()
    finally:
        db.close()
    r = client.post(f"/api/plans/{sid}/retry", headers=auth_headers)
    assert r.status_code == 409
    assert "原始需求已丢失" in r.json()["error"]["message"]


def test_retry_cross_user_404(client, auth_headers):
    from app.core.security import hash_password
    from app.db.base import SessionLocal
    from app.db import models

    # AUTH_TOKEN 解析取「库中第一个用户」——先任意认证请求落实默认用户，再建他人会话，
    # 否则默认用户是 other，404 断言不成立
    client.get("/api/sessions", headers=auth_headers)
    db = SessionLocal()
    try:
        other = models.User(name="other", username="other", password_hash=hash_password("pw123"))
        db.add(other)
        db.flush()
        sess = models.Session(user_id=other.id, title="t")
        db.add(sess)
        db.flush()
        db.add(models.OrchestratorState(
            session_id=sess.id, status=models.OrchestratorStatus.error,
            trace_id="t", partial_plan={"raw_text": "x", "outputs": {}},
        ))
        db.commit()
        sid = sess.id
    finally:
        db.close()
    assert client.post(f"/api/plans/{sid}/retry", headers=auth_headers).status_code == 404


def test_retry_requires_auth(client):
    assert client.post("/api/plans/1/retry").status_code == 401

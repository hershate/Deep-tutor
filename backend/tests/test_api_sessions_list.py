"""会话列表与状态只读端点测试（iter 184 B1，GET /sessions、GET /sessions/{id}）。

覆盖：空库/分页/排序（running 置顶+最近活动倒序）/q 过滤/latest_plan 提取/
无状态行 idle/详情 clarify_questions/跨用户 404（不泄露存在性）/
iter 185 B5 live 实时进度字段。
"""

from __future__ import annotations

from app.core.security import hash_password

BASE = "/api/sessions"


def _mk_user(username: str, password: str = "pw123") -> int:
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(name=username, username=username, password_hash=hash_password(password))
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _login(client, username: str, password: str = "pw123") -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _mk_session(user_id: int, title: str = "t") -> int:
    from app.db.base import SessionLocal
    from app.db import models

    db = SessionLocal()
    try:
        sess = models.Session(user_id=user_id, title=title)
        db.add(sess)
        db.commit()
        db.refresh(sess)
        return sess.id
    finally:
        db.close()


def _set_state(session_id: int, status: str, partial: dict | None = None) -> None:
    from app.db.base import SessionLocal
    from app.db import models

    db = SessionLocal()
    try:
        st = models.OrchestratorState(
            session_id=session_id,
            status=models.OrchestratorStatus(status),
            trace_id="trace",
            partial_plan=partial,
        )
        db.add(st)
        db.commit()
    finally:
        db.close()


def _mk_plan(session_id: int, topic: str = "React", version: int = 1) -> int:
    from app.db.base import SessionLocal
    from app.db import models

    db = SessionLocal()
    try:
        plan = models.Plan(
            session_id=session_id,
            content={"intent": {"topic": topic}, "stages": [], "knowledge_map": {"nodes": []}},
            version=version,
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
        return plan.id
    finally:
        db.close()


def _mk_material(session_id: int) -> None:
    from app.db.base import SessionLocal
    from app.db import models

    db = SessionLocal()
    try:
        db.add(models.Material(session_id=session_id, kind=models.MaterialKind.text, source="t", parsed_chars=5))
        db.commit()
    finally:
        db.close()


def test_list_empty(client, auth_headers):
    r = client.get(BASE, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"items": [], "total": 0}


def test_list_status_latest_plan_and_materials(client, auth_headers):
    # AUTH_TOKEN 回退自动建默认用户；经 API 建会话保证归属当前用户
    s1 = client.post(BASE, json={"title": "学 React"}, headers=auth_headers).json()["session_id"]
    s2 = client.post(BASE, json={"title": "学 Python"}, headers=auth_headers).json()["session_id"]
    _mk_plan(s1, topic="React 入门", version=2)
    _mk_material(s2)
    _set_state(s2, "running")

    r = client.get(BASE, headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 2
    items = {i["session_id"]: i for i in data["items"]}
    # running 置顶
    assert data["items"][0]["session_id"] == s2
    i1 = items[s1]
    assert i1["status"] == "idle"  # 无状态行
    assert i1["title"] == "学 React"
    assert i1["latest_plan"]["topic"] == "React 入门"
    assert i1["latest_plan"]["version"] == 2
    assert i1["has_materials"] is False
    assert i1["live"] is None
    i2 = items[s2]
    assert i2["status"] == "running"
    assert i2["latest_plan"] is None
    assert i2["has_materials"] is True
    assert i2["updated_at"] is not None


def test_list_pagination_and_q(client, auth_headers):
    for i in range(3):
        client.post(BASE, json={"title": f"主题{i}"}, headers=auth_headers)
    r = client.get(BASE, params={"limit": 2, "page": 2}, headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    assert len(data["items"]) == 1
    r = client.get(BASE, params={"q": "主题1"}, headers=auth_headers)
    assert [i["title"] for i in r.json()["items"]] == ["主题1"]
    r = client.get(BASE, params={"q": "  "}, headers=auth_headers)
    assert r.json()["total"] == 3  # 纯空白忽略


def test_detail_fields_and_clarify_questions(client, auth_headers):
    sid = client.post(BASE, json={"title": "x"}, headers=auth_headers).json()["session_id"]
    _mk_plan(sid, topic="T", version=1)
    partial = {"raw_text": "r", "outputs": {"understand_intent": {"status": "ok", "data": {"clarify_questions": ["q1", "q2"]}}}}
    _set_state(sid, "paused_clarify", partial=partial)

    r = client.get(f"{BASE}/{sid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["session_id"] == sid
    assert d["status"] == "paused_clarify"
    assert d["clarify_questions"] == ["q1", "q2"]
    assert d["latest_plan"]["topic"] == "T"

    # 非 paused_clarify -> 空问题
    sid2 = client.post(BASE, json={"title": "y"}, headers=auth_headers).json()["session_id"]
    _set_state(sid2, "done")
    d2 = client.get(f"{BASE}/{sid2}", headers=auth_headers).json()
    assert d2["clarify_questions"] == []


def test_cross_user_404(client, auth_headers):
    # 先经 API 落地默认用户（get_default_user 取首个用户——若先建 other，
    # AUTH_TOKEN 会解析到 other，列表就"看到"其会话，无法验证隔离）
    client.get(BASE, headers=auth_headers)
    other_id = _mk_user("other")
    sid = _mk_session(other_id, title="他人会话")
    _set_state(sid, "running")
    # 列表不含他人会话
    r = client.get(BASE, headers=auth_headers)
    assert r.json()["total"] == 0
    # 详情 404（不泄露存在性）
    assert client.get(f"{BASE}/{sid}", headers=auth_headers).status_code == 404
    # 登录态跨用户同样 404
    _mk_user("alice")
    ha = _login(client, "alice")
    assert client.get(f"{BASE}/{sid}", headers=ha).status_code == 404
    assert client.get(BASE, headers=ha).json()["total"] == 0


def test_live_progress_when_running(client, auth_headers):
    """iter 185 B5：running + 活动流 -> live 实时进度；流已终态（状态将翻转）-> live=None。"""
    from app.api.plans import _STREAMS, _SessionStream

    sid = client.post(BASE, json={"title": "t"}, headers=auth_headers).json()["session_id"]
    _set_state(sid, "running")
    stream = _SessionStream(sid, "trace")
    stream.current_tool = "design_stages"
    stream.step_index = 4
    stream.total_steps = 8
    stream.cost = 0.0123
    stream.publish("tool_start", {"tool": "design_stages", "step_index": 4, "total_steps": 8, "attempt": 1})
    _STREAMS[sid] = stream

    d = client.get(f"{BASE}/{sid}", headers=auth_headers).json()
    assert d["live"] == {
        "current_tool": "design_stages",
        "step_index": 4,
        "total_steps": 8,
        "cost": 0.0123,
        "last_event_id": 1,
    }
    # 列表同样暴露
    item = client.get(BASE, headers=auth_headers).json()["items"][0]
    assert item["live"]["step_index"] == 4

    # 流已标记终态（_finalize 置态窗口期）-> 不报 live
    stream.mark_terminal(1)
    d2 = client.get(f"{BASE}/{sid}", headers=auth_headers).json()
    assert d2["live"] is None


def test_requires_auth(client):
    assert client.get(BASE).status_code == 401
    assert client.get(f"{BASE}/1").status_code == 401

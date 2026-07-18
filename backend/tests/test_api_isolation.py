"""P4 数据隔离测试（iter 58）：跨用户访问一律 404（不泄露存在性）；providers/usage 按用户。

覆盖：sessions/plans/materials/progress/logs/quizzes/exports 归属校验、plans 列表按用户过滤、
providers CRUD/activate 按用户、usage 按用户统计、get_provider(user_id) 按用户解析档案。
"""

from __future__ import annotations

from app.core.security import hash_password

BASE_P = "/api/providers"


def _mk_user(username: str, password: str = "pw123", is_admin: bool = False) -> int:
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(name=username, username=username, password_hash=hash_password(password), is_admin=is_admin)
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


def _mk_plan_for(user_id: int) -> tuple[int, int]:
    """直接建 session + plan（含 content），返回 (session_id, plan_id)。"""
    from app.db.base import SessionLocal
    from app.db import models

    db = SessionLocal()
    try:
        sess = models.Session(user_id=user_id, title="t")
        db.add(sess)
        db.flush()
        plan = models.Plan(
            session_id=sess.id,
            content={"intent": {"topic": "x"}, "stages": [{"name": "s1"}], "knowledge_map": {"nodes": []}},
            version=1,
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
        return sess.id, plan.id
    finally:
        db.close()


def _two_users(client):
    a_id = _mk_user("alice")
    b_id = _mk_user("bob")
    return a_id, b_id, _login(client, "alice"), _login(client, "bob")


# --- sessions / plans 归属 ---


def test_session_cross_user_404(client):
    a_id, b_id, ha, hb = _two_users(client)
    sid, plan_id = _mk_plan_for(a_id)

    # B 对 A 的会话：generate / clarify / cancel / delete 一律 404
    assert client.post("/api/plans/generate", json={"session_id": sid}, headers=hb).status_code == 404
    assert client.post(f"/api/plans/{sid}/clarify", json={"answers": {}}, headers=hb).status_code == 404
    assert client.post(f"/api/plans/{sid}/cancel", headers=hb).status_code == 404
    assert client.delete(f"/api/sessions/{sid}", headers=hb).status_code == 404
    # 材料上传入口同样隔离
    assert client.post("/api/materials/text", json={"session_id": sid, "text": "hi"}, headers=hb).status_code == 404

    # A 自己可访问（generate 会进入流程而非 404；无 key -> SSE error 终态）
    r = client.get(f"/api/plans/{plan_id}", headers=ha)
    assert r.status_code == 200, r.text


def test_plan_cross_user_404(client):
    a_id, _b_id, ha, hb = _two_users(client)
    _sid, plan_id = _mk_plan_for(a_id)

    assert client.get(f"/api/plans/{plan_id}", headers=hb).status_code == 404
    assert client.post(f"/api/plans/{plan_id}/regenerate", json={"scope": "design_stages"}, headers=hb).status_code == 404
    assert client.post(f"/api/plans/{plan_id}/materials", json={}, headers=hb).status_code == 404
    assert client.get(f"/api/plans/{plan_id}/materials", headers=hb).status_code == 404
    assert client.post(f"/api/plans/{plan_id}/quiz", json={}, headers=hb).status_code == 404
    assert client.post(f"/api/export/{plan_id}", json={"format": "md"}, headers=hb).status_code == 404
    assert client.get(f"/api/progress/{plan_id}", headers=hb).status_code == 404
    assert (
        client.post(
            "/api/progress", json={"plan_id": plan_id, "node_id": "k1", "status": "done"}, headers=hb
        ).status_code
        == 404
    )
    assert (
        client.post("/api/progress/logs", json={"plan_id": plan_id, "minutes": 10}, headers=hb).status_code == 404
    )
    assert client.get(f"/api/progress/logs/{plan_id}", headers=hb).status_code == 404


def test_list_plans_filtered_by_user(client):
    a_id, b_id, ha, hb = _two_users(client)
    _sid_a, _p_a = _mk_plan_for(a_id)
    sid_b, _p_b = _mk_plan_for(b_id)

    ra = client.get("/api/plans", headers=ha).json()
    rb = client.get("/api/plans", headers=hb).json()
    assert ra["total"] == 1 and rb["total"] == 1
    assert ra["items"][0]["session_id"] != rb["items"][0]["session_id"]
    # 指定他人 session_id -> 404
    assert client.get(f"/api/plans?session_id={sid_b}", headers=ha).status_code == 404


def test_quiz_submit_cross_user_404(client):
    from app.db.base import SessionLocal
    from app.db import models

    a_id, _b_id, _ha, hb = _two_users(client)
    _sid, plan_id = _mk_plan_for(a_id)
    db = SessionLocal()
    try:
        q = models.Quiz(plan_id=plan_id, type=models.QuizType.choice, payload={"answer": "A"})
        db.add(q)
        db.commit()
        db.refresh(q)
        qid = q.id
    finally:
        db.close()
    r = client.post(f"/api/quizzes/{qid}/submit", json={"user_answer": {"selected": "A"}}, headers=hb)
    assert r.status_code == 404


def test_unit_content_cross_user_404(client, tmp_path):
    from app.db.base import SessionLocal
    from app.db import models

    a_id, _b_id, ha, hb = _two_users(client)
    _sid, plan_id = _mk_plan_for(a_id)
    content_file = tmp_path / "u1.md"
    content_file.write_text("# 内容", encoding="utf-8")
    db = SessionLocal()
    try:
        unit = models.StudyUnit(plan_id=plan_id, title="u", order=0)
        db.add(unit)
        db.flush()
        db.add(models.StudyMaterial(unit_id=unit.id, content_md_path=str(content_file), size=3))
        db.commit()
        uid = unit.id
    finally:
        db.close()

    assert client.get(f"/api/materials/{uid}", headers=hb).status_code == 404
    assert client.post(f"/api/materials/{uid}/done", json={"done": True}, headers=hb).status_code == 404
    assert client.get(f"/api/materials/{uid}", headers=ha).status_code == 200


# --- providers 按用户 ---


def test_providers_isolated_per_user(client):
    _a, _b, ha, hb = _two_users(client)
    r = client.post(BASE_P, json={"name": "中转", "api_key": "sk-a-1234"}, headers=ha)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    # B 看不到/改不了 A 的档案
    assert client.get(BASE_P, headers=hb).json()["providers"] == []
    assert client.put(f"{BASE_P}/{pid}", json={"model": "m"}, headers=hb).status_code == 404
    assert client.delete(f"{BASE_P}/{pid}", headers=hb).status_code == 404
    assert client.post(f"{BASE_P}/{pid}/activate", headers=hb).status_code == 404
    assert client.post(f"{BASE_P}/{pid}/test", headers=hb).status_code == 404

    # 同名档案跨用户不冲突
    r2 = client.post(BASE_P, json={"name": "中转", "api_key": "sk-b-5678"}, headers=hb)
    assert r2.status_code == 201, r2.text


def test_provider_activate_scoped_per_user(client):
    """A 启用档案不影响 B 的 active 状态（activate 仅取消同用户其他档案）。"""
    _a, _b, ha, hb = _two_users(client)
    pa = client.post(BASE_P, json={"name": "A1", "api_key": "sk-a"}, headers=ha).json()
    pb = client.post(BASE_P, json={"name": "B1", "api_key": "sk-b"}, headers=hb).json()
    client.post(f"{BASE_P}/{pb['id']}/activate", headers=hb)
    client.post(f"{BASE_P}/{pa['id']}/activate", headers=ha)
    # 各自仍保持自己的 active
    assert client.get(BASE_P, headers=ha).json()["providers"][0]["is_active"] is True
    assert client.get(BASE_P, headers=hb).json()["providers"][0]["is_active"] is True


def test_get_provider_per_user(monkeypatch):
    """get_provider(user_id)：仅解析该用户 active 档案；他人档案不可见 -> 回退 .env。"""
    from app.core import settings as settings_mod
    from app.db.base import SessionLocal
    from app.db import models
    from app.llm import get_provider

    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "sk-env")
    db = SessionLocal()
    try:
        ua = models.User(name="u1", username="u1")
        ub = models.User(name="u2", username="u2")
        db.add_all([ua, ub])
        db.flush()
        db.add(
            models.LlmProviderProfile(
                user_id=ua.id, name="p", base_url="https://relay.example.com", api_key="sk-profile", is_active=True
            )
        )
        db.commit()
        a_id, b_id = ua.id, ub.id
    finally:
        db.close()

    pa = get_provider(a_id)
    assert pa._client.api_key == "sk-profile"
    pb = get_provider(b_id)  # B 无 active 档案 -> .env（不越权用 A 的）
    assert pb._client.api_key == "sk-env"


# --- usage 按用户 ---


def test_usage_per_user(client):
    from app.db.base import SessionLocal
    from app.db import models

    a_id, b_id, ha, hb = _two_users(client)
    db = SessionLocal()
    try:
        db.add(models.ApiUsage(user_id=a_id, tokens_in=100, tokens_out=50, cost=0.01))
        db.add(models.ApiUsage(user_id=b_id, tokens_in=7, tokens_out=3, cost=0.001))
        db.commit()
    finally:
        db.close()

    ra = client.get("/api/usage", headers=ha).json()
    rb = client.get("/api/usage", headers=hb).json()
    assert ra["total_tokens"] == 150 and ra["calls"] == 1
    assert rb["total_tokens"] == 10 and rb["calls"] == 1


# --- 兼容：AUTH_TOKEN 回退自动建用户（旧单用户行为）---


def test_env_token_autocreates_default_user(client, auth_headers):
    """仅 AUTH_TOKEN 的本地部署：首次数据操作自动创建默认用户（P4 后旧调用方仍可用）。"""
    r = client.post("/api/sessions", json={"title": "x"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    # 再用 JWT 用户访问该会话 -> 404（归属默认用户，他人不可见）
    _mk_user("carol")
    hc = _login(client, "carol")
    assert client.delete(f"/api/sessions/{r.json()['session_id']}", headers=hc).status_code == 404

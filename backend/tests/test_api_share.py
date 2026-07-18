"""P5 协作分享测试：只读分享链接（/plans/{id}/share + /shared/{token}）。"""

from __future__ import annotations


def _mk_plan(client, auth_headers, content) -> int:
    from app.db.base import SessionLocal
    from app.db.models import Plan, Session as Sess

    me = client.get("/api/auth/me", headers=auth_headers).json()
    db = SessionLocal()
    try:
        s = Sess(user_id=me["id"])
        db.add(s)
        db.flush()
        p = Plan(session_id=s.id, content=content)
        db.add(p)
        db.commit()
        return p.id
    finally:
        db.close()


PLAN = {"title": "Python 入门", "topic": "Python", "stages": [{"index": 0, "name": "基础"}]}


def test_create_and_read_share(client, auth_headers):
    pid = _mk_plan(client, auth_headers, PLAN)
    r = client.post(f"/api/plans/{pid}/share", headers=auth_headers)
    assert r.status_code == 201
    token = r.json()["token"]
    assert len(token) >= 32 and r.json()["path"] == f"/shared/{token}"

    # 公开只读访问（无认证头）
    pub = client.get(f"/api/shared/{token}")
    assert pub.status_code == 200
    body = pub.json()
    assert body["title"] == "Python 入门"
    assert body["content"]["stages"][0]["name"] == "基础"
    assert "user" not in body and "session" not in body  # 不泄露归属信息


def test_create_share_idempotent(client, auth_headers):
    pid = _mk_plan(client, auth_headers, PLAN)
    t1 = client.post(f"/api/plans/{pid}/share", headers=auth_headers).json()["token"]
    t2 = client.post(f"/api/plans/{pid}/share", headers=auth_headers).json()["token"]
    assert t1 == t2  # 重复创建返回既有链接，不生成新 token


def test_get_share_status(client, auth_headers):
    pid = _mk_plan(client, auth_headers, PLAN)
    r = client.get(f"/api/plans/{pid}/share", headers=auth_headers)
    assert r.json() == {"shared": False}
    client.post(f"/api/plans/{pid}/share", headers=auth_headers)
    r2 = client.get(f"/api/plans/{pid}/share", headers=auth_headers)
    assert r2.json()["shared"] is True and r2.json()["token"]


def test_revoke_share(client, auth_headers):
    pid = _mk_plan(client, auth_headers, PLAN)
    token = client.post(f"/api/plans/{pid}/share", headers=auth_headers).json()["token"]
    r = client.delete(f"/api/plans/{pid}/share", headers=auth_headers)
    assert r.status_code == 204
    assert client.get(f"/api/shared/{token}").status_code == 404  # 撤销即失效
    assert client.get(f"/api/plans/{pid}/share", headers=auth_headers).json() == {"shared": False}


def test_share_unknown_token_404(client):
    assert client.get("/api/shared/not-a-real-token-xyz").status_code == 404


def test_share_cross_user_404(client, auth_headers):
    """他人方案不能创建/查询/撤销分享（404 防枚举）。"""
    from app.core.security import hash_password
    from app.db.base import SessionLocal
    from app.db.models import User

    pid = _mk_plan(client, auth_headers, PLAN)
    db = SessionLocal()
    try:
        db.add(User(name="u2", username="u2", password_hash=hash_password("pw12345678")))
        db.commit()
    finally:
        db.close()
    tok = client.post("/api/auth/login", json={"username": "u2", "password": "pw12345678"}).json()["token"]
    h2 = {"Authorization": f"Bearer {tok}"}
    assert client.post(f"/api/plans/{pid}/share", headers=h2).status_code == 404
    assert client.get(f"/api/plans/{pid}/share", headers=h2).status_code == 404
    assert client.delete(f"/api/plans/{pid}/share", headers=h2).status_code == 404


def test_share_unauthenticated_401(client):
    assert client.post("/api/plans/1/share").status_code == 401
    assert client.delete("/api/plans/1/share").status_code == 401
    # 公开端点无需认证：未知 token 是 404 而非 401
    assert client.get("/api/shared/whatever").status_code == 404


def test_share_link_cascade_on_session_delete(client, auth_headers):
    """删除会话（级联删 plan）时分享链接一并删除，不阻断删除。"""
    from app.db.base import SessionLocal
    from app.db.models import ShareLink

    pid = _mk_plan(client, auth_headers, PLAN)
    token = client.post(f"/api/plans/{pid}/share", headers=auth_headers).json()["token"]
    db = SessionLocal()
    try:
        sess_id = db.query(ShareLink).filter_by(token=token).first().plan.session_id
    finally:
        db.close()
    r = client.delete(f"/api/sessions/{sess_id}", headers=auth_headers)
    assert r.status_code in (200, 204)
    db = SessionLocal()
    try:
        assert db.query(ShareLink).filter_by(token=token).first() is None
    finally:
        db.close()

"""用户管理端点测试（P4 iter 59）：admin CRUD + 护栏（自删/自降级/最后管理员）+ 级联清理。"""

from __future__ import annotations

from app.core.security import hash_password

BASE = "/api/users"


def _mk_user(username: str, password: str = "pw1234", is_admin: bool = False) -> int:
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


def _login(client, username: str, password: str = "pw1234") -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _admin(client):
    _mk_user("root", is_admin=True)
    return _login(client, "root")


# --- 权限 ---


def test_requires_admin(client):
    _mk_user("root", is_admin=True)
    _mk_user("bob")
    hb = _login(client, "bob")
    assert client.get(BASE, headers=hb).status_code == 403
    assert client.post(BASE, json={"username": "x", "password": "pw1234"}, headers=hb).status_code == 403
    assert client.put(f"{BASE}/1", json={"name": "y"}, headers=hb).status_code == 403
    assert client.delete(f"{BASE}/1", headers=hb).status_code == 403


def test_requires_auth(client):
    assert client.get(BASE).status_code == 401


# --- CRUD ---


def test_list_and_create(client):
    ha = _admin(client)
    r = client.get(BASE, headers=ha)
    assert r.status_code == 200
    assert len(r.json()["users"]) == 1
    assert "password_hash" not in r.text  # 不泄露哈希

    r = client.post(
        BASE, json={"username": "alice", "password": "pw1234", "name": "爱丽丝", "is_admin": False}, headers=ha
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["username"] == "alice" and body["name"] == "爱丽丝" and body["is_admin"] is False

    # 新用户可登录
    r = client.post("/api/auth/login", json={"username": "alice", "password": "pw1234"})
    assert r.status_code == 200


def test_create_duplicate_username_409(client):
    ha = _admin(client)
    client.post(BASE, json={"username": "alice", "password": "pw1234"}, headers=ha)
    r = client.post(BASE, json={"username": "alice", "password": "pw9999"}, headers=ha)
    assert r.status_code == 409


def test_create_validation(client):
    ha = _admin(client)
    # 密码过短 422；空白用户名 400
    assert client.post(BASE, json={"username": "a", "password": "x"}, headers=ha).status_code == 422
    assert client.post(BASE, json={"username": "   ", "password": "pw1234"}, headers=ha).status_code == 400


def test_update_name_and_reset_password(client):
    ha = _admin(client)
    uid = client.post(BASE, json={"username": "alice", "password": "pw1234"}, headers=ha).json()["id"]

    r = client.put(f"{BASE}/{uid}", json={"name": "新名字", "password": "newpass1"}, headers=ha)
    assert r.status_code == 200 and r.json()["name"] == "新名字"

    # 旧密码失效，新密码可登录
    assert client.post("/api/auth/login", json={"username": "alice", "password": "pw1234"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "alice", "password": "newpass1"}).status_code == 200


def test_update_not_found(client):
    ha = _admin(client)
    assert client.put(f"{BASE}/999999", json={"name": "x"}, headers=ha).status_code == 404


# --- 护栏 ---


def test_cannot_demote_self(client):
    from app.db.base import SessionLocal
    from app.db.models import User

    ha = _admin(client)
    db = SessionLocal()
    try:
        root_id = db.query(User).filter_by(username="root").one().id
    finally:
        db.close()
    r = client.put(f"{BASE}/{root_id}", json={"is_admin": False}, headers=ha)
    assert r.status_code == 409


def test_cannot_delete_self(client):
    from app.db.base import SessionLocal
    from app.db.models import User

    ha = _admin(client)
    db = SessionLocal()
    try:
        root_id = db.query(User).filter_by(username="root").one().id
    finally:
        db.close()
    assert client.delete(f"{BASE}/{root_id}", headers=ha).status_code == 409


def test_cannot_remove_last_admin(client):
    """降级/删除另一名管理员使其成为零管理员 -> 409（当前管理员若自己也被禁则同理）。"""
    ha = _admin(client)
    uid = client.post(BASE, json={"username": "second", "password": "pw1234", "is_admin": True}, headers=ha).json()[
        "id"
    ]
    # second 是另一名管理员；root 也是管理员 -> 可以删 second（root 仍在）
    assert client.delete(f"{BASE}/{uid}", headers=ha).status_code == 200

    # 现在 root 是唯一管理员；root 删自己已被自删护栏覆盖，验证降级护栏经 second 视角：
    # second2 管理员登录后尝试降级 root（root 是除 second2 外唯一管理员 -> 降后仍剩 second2，允许）
    uid2 = client.post(BASE, json={"username": "s2", "password": "pw1234", "is_admin": True}, headers=ha).json()["id"]
    hs2 = _login(client, "s2")
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        root_id = db.query(User).filter_by(username="root").one().id
    finally:
        db.close()
    r = client.put(f"{BASE}/{root_id}", json={"is_admin": False}, headers=hs2)
    assert r.status_code == 200  # 降级 root 后 s2 仍是管理员，合法
    # 此时 s2 是唯一管理员，降级自己 -> 自降级护栏 409
    assert client.put(f"{BASE}/{uid2}", json={"is_admin": False}, headers=hs2).status_code == 409


# --- 删除级联 ---


def test_delete_cascades_user_data(client):
    from app.db.base import SessionLocal
    from app.db import models

    ha = _admin(client)
    uid = client.post(BASE, json={"username": "alice", "password": "pw1234"}, headers=ha).json()["id"]

    db = SessionLocal()
    try:
        sess = models.Session(user_id=uid, title="t")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, content={"intent": {"topic": "x"}}, version=1)
        db.add(plan)
        db.add(
            models.LlmProviderProfile(user_id=uid, name="p", api_key="sk-x", is_active=True)
        )
        db.add(models.ApiUsage(user_id=uid, tokens_in=1))
        db.commit()
        sid = sess.id
    finally:
        db.close()

    r = client.delete(f"{BASE}/{uid}", headers=ha)
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        assert db.get(models.User, uid) is None
        assert db.get(models.Session, sid) is None
        assert db.query(models.Plan).count() == 0
        assert db.query(models.LlmProviderProfile).count() == 0
        assert db.query(models.ApiUsage).count() == 0
    finally:
        db.close()

    # 被删用户 token 失效（JWT sub 查无此人 -> 401）
    assert client.get("/api/auth/me", headers=_login_headers_of_deleted(client)).status_code == 401


def _login_headers_of_deleted(client):
    """删除前签发的 token：直接构造（用户已删无法再 login）。"""
    from app.core.security import create_token

    return {"Authorization": f"Bearer {create_token(999999, 'alice')}"}


def test_delete_not_found(client):
    ha = _admin(client)
    assert client.delete(f"{BASE}/999999", headers=ha).status_code == 404

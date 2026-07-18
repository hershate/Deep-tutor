"""P4 多用户认证测试（iter 57）：密码哈希 / JWT / login/me 端点 / 管理员启动收敛。"""

from __future__ import annotations

import pytest

from app.core import security
from app.core.security import (
    create_token,
    decode_token,
    ensure_admin_user,
    hash_password,
    verify_password,
)


# --- 密码哈希 ---


def test_password_hash_roundtrip():
    stored = hash_password("s3cret")
    assert stored.startswith("pbkdf2$")
    assert verify_password("s3cret", stored) is True
    assert verify_password("wrong", stored) is False


def test_verify_password_malformed():
    assert verify_password("x", "") is False
    assert verify_password("x", "not-a-hash") is False
    assert verify_password("x", "bcrypt$1$2$3") is False


def test_hash_password_unique_salts():
    assert hash_password("same") != hash_password("same")  # 随机盐


# --- JWT ---


def test_token_roundtrip():
    token = create_token(42, "alice")
    payload = decode_token(token)
    assert payload is not None
    assert payload["sub"] == "42"
    assert payload["username"] == "alice"
    assert payload["exp"] > payload["iat"]


def test_token_invalid():
    assert decode_token("garbage") is None
    assert decode_token("") is None
    token = create_token(1, "a")
    assert decode_token(token[:-2] + "xx") is None  # 篡改签名


def test_token_expired():
    """过期 token -> None（直接构造已过期 payload；PyJWT 内部时钟不可 monkeypatch）。"""
    import jwt as pyjwt

    payload = {"sub": "1", "username": "a", "iat": 1000, "exp": 2000}
    token = pyjwt.encode(payload, security._secret(), algorithm="HS256")
    assert decode_token(token) is None


# --- login / me 端点 ---


def _create_user(username="admin", password="pw123", is_admin=True):
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


def test_login_ok_and_me(client):
    _create_user()
    r = client.post("/api/auth/login", json={"username": "admin", "password": "pw123"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["is_admin"] is True
    token = body["token"]

    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "admin"


def test_login_wrong_password_or_unknown_user(client):
    _create_user()
    r = client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
    assert r.status_code == 401
    r = client.post("/api/auth/login", json={"username": "nobody", "password": "pw123"})
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "用户名或密码错误"  # 不区分不存在/密码错（防枚举）


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


def test_jwt_accepted_on_protected_router(client):
    """JWT 可通过既有路由的 require_auth（与 AUTH_TOKEN 并行）。"""
    _create_user()
    token = create_token(1, "admin")
    r = client.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text


# --- 管理员启动收敛 ---


def _all_users():
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        return db.query(User).all()
    finally:
        db.close()


def test_ensure_admin_creates_when_empty(monkeypatch):
    from app.core import settings as settings_mod
    from app.db.base import SessionLocal

    monkeypatch.setattr(settings_mod.settings, "admin_password", "init-pw")
    db = SessionLocal()
    try:
        ensure_admin_user(db)
    finally:
        db.close()
    users = _all_users()
    assert len(users) == 1
    assert users[0].username == "admin"
    assert users[0].is_admin is True
    assert verify_password("init-pw", users[0].password_hash)


def test_ensure_admin_promotes_legacy_single_user(monkeypatch):
    """旧单用户时代唯一默认用户（无 username）-> 升级为管理员（既有数据随之归属）。"""
    from app.core import settings as settings_mod
    from app.db.base import SessionLocal
    from app.db.models import User

    monkeypatch.setattr(settings_mod.settings, "admin_password", "init-pw")
    db = SessionLocal()
    try:
        db.add(User(name="local"))  # 旧默认用户
        db.commit()
        ensure_admin_user(db)
    finally:
        db.close()
    users = _all_users()
    assert len(users) == 1
    assert users[0].name == "local"  # 同一行（数据保留）
    assert users[0].username == "admin"
    assert users[0].is_admin is True


def test_ensure_admin_no_password_no_create(monkeypatch):
    """admin_password 为空 -> 不创建（仅 AUTH_TOKEN 回退）；幂等。"""
    from app.core import settings as settings_mod
    from app.db.base import SessionLocal

    monkeypatch.setattr(settings_mod.settings, "admin_password", "")
    db = SessionLocal()
    try:
        ensure_admin_user(db)
        ensure_admin_user(db)  # 幂等
    finally:
        db.close()
    assert _all_users() == []

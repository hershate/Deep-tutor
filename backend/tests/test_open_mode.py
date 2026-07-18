"""零配置开放本机模式测试（iter 89）：passwordless_open 谓词 / 无 token 放行 /
懒创建管理员 / 设密码后开放模式自动关闭。

conftest 设 AUTH_TOKEN=test-token -> 其余测试默认不触发开放模式（零回归）；
本文件用 monkeypatch 将 settings.auth_token 置空模拟零配置环境。
"""

from __future__ import annotations

from app.core import settings as settings_mod
from app.core.security import get_or_create_open_admin, hash_password, passwordless_open


def _open_mode(monkeypatch):
    """模拟零配置：无 AUTH_TOKEN。"""
    monkeypatch.setattr(settings_mod.settings, "auth_token", "")


def _db():
    from app.db.base import SessionLocal

    return SessionLocal()


def _mk_user_with_password(username="admin", password="pw1234", is_admin=True):
    from app.db.models import User

    db = _db()
    try:
        u = User(name=username, username=username, password_hash=hash_password(password), is_admin=is_admin)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


# --- 谓词 ---


def test_passwordless_open_predicate(monkeypatch):
    db = _db()
    try:
        # 默认（conftest AUTH_TOKEN=test-token）-> 关闭
        assert passwordless_open(db) is False
        # 零配置 + 无任何密码 -> 开放
        _open_mode(monkeypatch)
        assert passwordless_open(db) is True
        # 任一用户设密码 -> 关闭
        _mk_user_with_password()
        assert passwordless_open(db) is False
    finally:
        db.close()


def test_get_or_create_open_admin_idempotent(monkeypatch):
    _open_mode(monkeypatch)
    db = _db()
    try:
        u1 = get_or_create_open_admin(db)
        assert u1.is_admin is True
        assert u1.username == settings_mod.settings.admin_username
        assert u1.password_hash == ""  # 空 hash：登录不可用，仅身份锚点
        u2 = get_or_create_open_admin(db)
        assert u2.id == u1.id  # 幂等，不重复创建
    finally:
        db.close()


# --- API 行为 ---


def test_open_mode_allows_requests_without_token(client, monkeypatch):
    _open_mode(monkeypatch)
    # 路由级 require_auth 放行
    assert client.get("/api/settings").status_code == 200
    # 数据端点可用且归属懒创建的 admin
    r = client.post("/api/sessions", json={"title": "零配置会话"})
    assert r.status_code == 200, r.text
    assert client.get("/api/plans").status_code == 200
    # /auth/me 解析为 admin
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["is_admin"] is True

    # 懒创建的管理员已落库，且数据归属它
    db = _db()
    try:
        from app.db.models import Session as SessionModel
        from app.db.models import User

        admin = db.query(User).filter_by(is_admin=True).one()
        assert admin.password_hash == ""
        sess = db.query(SessionModel).filter_by(id=r.json()["session_id"]).one()
        assert sess.user_id == admin.id
    finally:
        db.close()


def test_open_mode_closes_after_password_set(client, monkeypatch):
    _open_mode(monkeypatch)
    assert client.get("/api/plans").status_code == 200  # 开放中（已懒创建 admin）
    # 给已存在的 admin 设密码（模拟 POST /setup 的效果）-> 开放模式关闭
    db = _db()
    try:
        from app.db.models import User

        admin = db.query(User).filter_by(is_admin=True).one()
        admin.password_hash = hash_password("new-pw")
        db.commit()
    finally:
        db.close()
    assert client.get("/api/plans").status_code == 401
    assert client.get("/api/settings").status_code == 401


def test_open_mode_invalid_token_still_open(client, monkeypatch):
    """开放模式下携带无效 token 不应被拒绝（token 失败 -> 回退开放模式）。"""
    _open_mode(monkeypatch)
    r = client.get("/api/plans", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 200

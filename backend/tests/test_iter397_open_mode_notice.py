"""iter 397 回归：启动期「开放本机模式」告警（开放模式此前运维侧无任何信号）。

开放模式（无 AUTH_TOKEN / 无人设口令 / 无 active PAT）下后端**完全免认证**：
任何能连到本端口的客户端都拥有全部数据的读写权（含用户管理与数据导出）。
此前该状态只有前端横幅提示，无浏览器的服务端部署看不到任何信号——比「弱
AUTH_TOKEN」更严重的情形反而静默（启动期只对弱 token / 弱 JWT_SECRET 告警）。
"""

from __future__ import annotations

from app.core import credentials as cred_mod
from app.core.security import open_mode_notice
from app.db import models
from app.db.base import SessionLocal


def _notice() -> str | None:
    db = SessionLocal()
    try:
        return open_mode_notice(db)
    finally:
        db.close()


def test_open_mode_notice_present_when_fully_open(monkeypatch):
    """空库 + 未配 AUTH_TOKEN -> 必须给出告警文案。

    注：测试环境 conftest 固定设了 AUTH_TOKEN=test-token（否则全部用例 401），
    故此处须显式清空——这也解释了「开放模式此前无任何告警」为何长期未被发现：
    测试环境根本进不了开放模式。
    """
    from app.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "auth_token", "")
    notice = _notice()
    assert notice, "开放模式未给出告警文案"
    assert "开放本机模式" in notice
    assert "免认证" in notice
    assert "AUTH_TOKEN" in notice and "ADMIN_PASSWORD" in notice


def test_open_mode_notice_absent_when_password_set():
    """已有用户设过口令 -> 开放模式关闭 -> 不应再告警。"""
    db = SessionLocal()
    try:
        u = models.User(name="u", username="u", password_hash="pbkdf2$600000$aa$bb")
        db.add(u)
        db.commit()
    finally:
        db.close()
    assert _notice() is None


def test_open_mode_notice_absent_when_auth_token_configured(monkeypatch):
    """配置了 AUTH_TOKEN -> 开放模式关闭 -> 不应再告警。"""
    from app.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "auth_token", "some-real-token")
    assert _notice() is None


def test_open_mode_notice_absent_when_pat_exists():
    """存在 active 个人访问令牌 -> 开放模式关闭 -> 不应再告警。"""
    db = SessionLocal()
    try:
        u = models.User(name="p", username="p", password_hash="")
        db.add(u)
        db.flush()
        db.add(
            models.CredentialProfile(
                user_id=u.id,
                kind=cred_mod.KIND_AUTH_TOKEN,
                name="pat",
                api_key=cred_mod.hash_token("pat-token"),
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()
    assert _notice() is None

"""API 公共依赖：认证、数据库会话、默认用户（plan/03 §一、plan/09 §十八）。

认证：Authorization: Bearer <token>（plan/03）为主，X-Auth-Token 为兼容回退。
预留多用户 JWT（P4）。
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator

from fastapi import Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.base import SessionLocal
from app.db.models import User


async def require_auth(
    authorization: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None, alias="X-Auth-Token"),
) -> None:
    """校验本地单用户 token。Bearer 优先，X-Auth-Token 兼容。"""
    if not settings.auth_token:
        raise HTTPException(status_code=500, detail="auth not configured")
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_auth_token:
        token = x_auth_token
    # 常量时间比较防时序攻击（secrets.compare_digest）
    if not token or not secrets.compare_digest(token, settings.auth_token):
        raise HTTPException(status_code=401, detail="invalid auth token")


def get_db() -> Iterator[SessionLocal]:  # type: ignore[type-arg]
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_default_user(db: Session) -> User:
    """本地单用户：取首个用户，无则创建默认用户。"""
    user = db.query(User).first()
    if user is None:
        user = User(name="local")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

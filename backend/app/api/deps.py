"""API 公共依赖：认证、数据库会话、当前用户（plan/03 §一、plan/09 §十八）。

认证（P4 多用户，iter 57）：
- 首选 JWT（Authorization: Bearer <jwt>，/auth/login 签发）-> 解析为用户。
- 兼容回退 AUTH_TOKEN（.env 配置；Bearer 或 X-Auth-Token）-> 映射为首个管理员（无则首个用户）。
常量时间比较防时序攻击（secrets.compare_digest）。
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.core.settings import settings
from app.db.base import SessionLocal
from app.db.models import User


def _extract_token(authorization: str | None, x_auth_token: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if x_auth_token:
        return x_auth_token
    return None


def _is_env_token(token: str) -> bool:
    return bool(settings.auth_token) and secrets.compare_digest(token, settings.auth_token)


async def require_auth(
    authorization: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None, alias="X-Auth-Token"),
) -> None:
    """校验请求认证：JWT 或 AUTH_TOKEN 任一通过即可。"""
    token = _extract_token(authorization, x_auth_token)
    if not token:
        raise HTTPException(status_code=401, detail="invalid auth token")
    if decode_token(token) is not None:
        return
    if _is_env_token(token):
        return
    raise HTTPException(status_code=401, detail="invalid auth token")


def get_db() -> Iterator[SessionLocal]:  # type: ignore[type-arg]
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_default_user(db: Session) -> User:
    """本地单用户遗留：取首个用户，无则创建默认用户。"""
    user = db.query(User).first()
    if user is None:
        user = User(name="local")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def resolve_user(db: Session, authorization: str | None, x_auth_token: str | None) -> User | None:
    """解析请求身份为 User（JWT -> 对应用户；AUTH_TOKEN -> 首个管理员/首个用户）；失败 -> None。"""
    token = _extract_token(authorization, x_auth_token)
    if not token:
        return None
    payload = decode_token(token)
    if payload is not None:
        try:
            user_id = int(payload.get("sub", ""))
        except (TypeError, ValueError):
            return None
        return db.get(User, user_id)
    if _is_env_token(token):
        return db.query(User).filter_by(is_admin=True).first() or db.query(User).first()
    return None


async def get_current_user(
    authorization: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None, alias="X-Auth-Token"),
    db: Session = Depends(get_db),
) -> User:
    """当前登录用户（401 未认证）。数据隔离端点以此取归属。"""
    user = resolve_user(db, authorization, x_auth_token)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid auth token")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """管理员守卫（用户管理端点用）。"""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")
    return user

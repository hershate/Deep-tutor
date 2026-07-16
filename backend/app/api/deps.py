"""API 公共依赖：认证、数据库会话、配额（plan/09 §十八）。

本地单用户认证用简单 token（X-Auth-Token 头）；预留多用户 JWT（P4）。
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Header, HTTPException, status

from app.core.settings import settings
from app.db.base import SessionLocal


async def require_auth(
    x_auth_token: str | None = Header(default=None, alias="X-Auth-Token"),
) -> None:
    """校验本地单用户 token。"""
    if not settings.auth_token:
        # 未配置 token（不应发生，启动校验已拦截）；拒绝以防开放访问。
        raise HTTPException(status_code=500, detail="auth not configured")
    if x_auth_token != settings.auth_token:
        raise HTTPException(status_code=401, detail="invalid auth token")


def get_db() -> Iterator[SessionLocal]:  # type: ignore[type-arg]
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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
    """解析请求身份为 User（JWT -> 对应用户；AUTH_TOKEN -> 首个管理员/首个用户）；失败 -> None。

    AUTH_TOKEN 回退路径下若库中无任何用户 -> 自动创建默认用户（与旧单用户行为一致，
    保证仅配置 AUTH_TOKEN 的本地部署/既有调用方在 P4 后仍可用）。
    """
    token = _extract_token(authorization, x_auth_token)
    if not token:
        return None
    payload = decode_token(token)
    if payload is not None:
        try:
            user_id = int(payload.get("sub", ""))
        except (TypeError, ValueError):
            return None
        user = db.get(User, user_id)
        if user is None:
            return None
        # err.md A9：改密（tokens_valid_after 更新）前签发的 JWT 视为已吊销
        try:
            iat = float(payload.get("iat", 0))
        except (TypeError, ValueError):
            return None
        if iat < (user.tokens_valid_after or 0):
            return None
        return user
    if _is_env_token(token):
        return db.query(User).filter_by(is_admin=True).first() or get_default_user(db)
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


def check_daily_budget(db: Session | None, user_id: int) -> None:
    """日预算守卫（err.md A6：daily_budget_usd 配置此前全库零引用、从未强制）。

    当日（UTC）api_usage 累计 cost >= settings.daily_budget_usd -> 402 budget_exceeded；
    daily_budget_usd <= 0 视为不限。db 为 None 时使用短生命周期会话。
    触发 LLM 计费的端点（生成/材料/测验/幻灯片）应在执行前调用。
    """
    limit = settings.daily_budget_usd
    if limit <= 0:
        return
    if db is None:
        own = SessionLocal()
        try:
            return check_daily_budget(own, user_id)
        finally:
            own.close()
    from datetime import datetime, timezone

    from sqlalchemy import func

    from app.api.errors import ApiError
    from app.db import models

    today = datetime.now(timezone.utc).date().isoformat()
    spent = (
        db.query(func.coalesce(func.sum(models.ApiUsage.cost), 0.0))
        .filter(
            models.ApiUsage.user_id == user_id,
            func.date(models.ApiUsage.created_at) == today,
        )
        .scalar()
        or 0.0
    )
    if spent >= limit:
        raise ApiError(
            "budget_exceeded",
            f"当日 LLM 预算已用尽（${spent:.4f} / ${limit:.4f}），请明天再试或调整 DAILY_BUDGET_USD",
            status=402,
        )


# --- 数据归属守卫（P4 iter 58 按用户隔离）---
# 统一 404（而非 403）：不泄露资源是否存在（防枚举他人数据 id）。


def get_owned_session(db: Session, session_id: int, user: User):
    """加载当前用户拥有的会话；不存在或属他人 -> 404。"""
    from app.api.errors import ApiError
    from app.db.models import Session as SessionModel

    sess = db.get(SessionModel, session_id)
    if sess is None or sess.user_id != user.id:
        raise ApiError("not_found", f"会话 {session_id} 不存在")
    return sess


def get_owned_plan(db: Session, plan_id: int, user: User):
    """加载当前用户拥有的方案（经 plan.session.user_id 判定）；不存在或属他人 -> 404。"""
    from app.api.errors import ApiError
    from app.db.models import Plan

    plan = db.get(Plan, plan_id)
    if plan is None or plan.session is None or plan.session.user_id != user.id:
        raise ApiError("not_found", f"方案 {plan_id} 不存在")
    return plan

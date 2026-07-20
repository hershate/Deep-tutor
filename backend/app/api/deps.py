"""API 公共依赖：认证、数据库会话、当前用户（plan/03 §一、plan/09 §十八）。

认证（P4 多用户，iter 57；个人访问令牌 iter 223）：
- 首选 JWT（Authorization: Bearer <jwt>，/auth/login 签发）-> 解析为用户。
- 兼容回退 AUTH_TOKEN（.env 配置；Bearer 或 X-Auth-Token）-> 映射为首个管理员（无则首个用户）。
- 个人访问令牌（credential_profiles kind=auth_token 且 active）-> 映射为档案归属用户；
  .env AUTH_TOKEN 始终有效作回退（防切换令牌后锁死）。
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
    # auth_token_configured：历史模板占位值（change-me-please）不是有效凭证，
    # 不参与比较（否则人尽皆知的占位串即可认证，iter 90）
    return settings.auth_token_configured and secrets.compare_digest(token, settings.auth_token)


def _user_by_token_credential(db: Session, token: str) -> User | None:
    """个人访问令牌（iter 223）：token 命中 active auth_token 凭证档案 -> 档案归属用户。

    仅在 JWT 与 .env token 均失败后的路径调用；档案 user 已删（应被级联清理）-> None。
    """
    from app.core.credentials import match_auth_token_credential

    cred = match_auth_token_credential(db, token)
    if cred is None:
        return None
    return db.get(User, cred["user_id"])


def get_db() -> Iterator[SessionLocal]:  # type: ignore[type-arg]
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def require_auth(
    authorization: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None, alias="X-Auth-Token"),
    db: Session = Depends(get_db),
) -> None:
    """校验请求认证：JWT / AUTH_TOKEN / 个人访问令牌任一通过即可；
    零配置开放本机模式（passwordless_open，iter 89）直接放行。"""
    token = _extract_token(authorization, x_auth_token)
    if token:
        if decode_token(token) is not None:
            return
        if _is_env_token(token):
            return
        if _user_by_token_credential(db, token) is not None:
            return
    # 失败路径才查库：已认证请求不新增 DB 查询
    from app.core.security import passwordless_open

    if passwordless_open(db):
        return
    raise HTTPException(status_code=401, detail="invalid auth token")


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
    零配置开放本机模式（iter 89）：无有效 token 且 passwordless_open -> 首个管理员
    （懒创建空密码 admin）。
    """
    token = _extract_token(authorization, x_auth_token)
    if token:
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
        # 个人访问令牌（iter 223）：命中 active auth_token 档案 -> 档案归属用户
        token_user = _user_by_token_credential(db, token)
        if token_user is not None:
            return token_user
    # 零配置开放本机模式（iter 89）：无有效 token 且未设任何密码/未配 AUTH_TOKEN
    # -> 首个管理员（无则懒创建空密码 admin，数据自始归属 admin）
    from app.core.security import get_or_create_open_admin, passwordless_open

    if passwordless_open(db):
        return get_or_create_open_admin(db)
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


def get_daily_budget(db: Session, user_id: int) -> float:
    """有效日预算（iter 176）：用户覆盖（user_prefs.daily_budget_usd）优先，否则全局默认。

    覆盖值非法（手改库等非浮点文本）时静默回退默认，不让坏数据打死守卫。
    """
    from app.db import models

    raw = (
        db.query(models.UserPref.value)
        .filter_by(user_id=user_id, key="daily_budget_usd")
        .scalar()
    )
    if raw is None:
        return settings.daily_budget_usd
    try:
        return float(raw)
    except (TypeError, ValueError):
        return settings.daily_budget_usd


def check_daily_budget(db: Session | None, user_id: int) -> None:
    """日预算守卫（err.md A6：daily_budget_usd 配置此前全库零引用、从未强制）。

    当日（服务器本地日，iter 167 口径）api_usage 累计 cost >= 有效日预算
    （用户覆盖优先，iter 176）-> 402 budget_exceeded；预算 <= 0 视为不限。
    db 为 None 时使用短生命周期会话。
    触发 LLM 计费的端点（生成/材料/测验/幻灯片）应在执行前调用。
    """
    if db is None:
        own = SessionLocal()
        try:
            return check_daily_budget(own, user_id)
        finally:
            own.close()
    from datetime import date

    from sqlalchemy import func

    from app.api.errors import ApiError
    from app.db import models

    limit = get_daily_budget(db, user_id)
    if limit <= 0:
        return
    # iter 167 口径：本地日（'localtime' 修饰符），与 /usage today_cost 一致——
    # 此前用 UTC 日，UTC+8 0-8 点的花费被计入"昨天"，守卫与用量页显示不符
    today = date.today().isoformat()
    spent = (
        db.query(func.coalesce(func.sum(models.ApiUsage.cost), 0.0))
        .filter(
            models.ApiUsage.user_id == user_id,
            func.date(models.ApiUsage.created_at, "localtime") == today,
        )
        .scalar()
        or 0.0
    )
    if spent >= limit:
        raise ApiError(
            "budget_exceeded",
            f"当日 LLM 预算已用尽（${spent:.4f} / ${limit:.4f}），请明天再试或在设置页调整日预算",
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

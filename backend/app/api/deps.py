"""API 公共依赖：认证、数据库会话、当前用户（plan/03 §一、plan/09 §十八）。

认证（P4 多用户，iter 57；个人访问令牌 iter 223）：
- 首选 JWT（Authorization: Bearer <jwt>，/auth/login 签发）-> 解析为用户。
- 兼容回退 AUTH_TOKEN（.env 配置；Bearer 或 X-Auth-Token）-> 映射为首个管理员（无则首个用户）。
- 个人访问令牌（credential_profiles kind=auth_token 且 active）-> 映射为档案归属用户；
  .env AUTH_TOKEN 始终有效作回退（防切换令牌后锁死）。
常量时间比较防时序攻击（secrets.compare_digest）。
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.core.settings import settings
from app.db.base import SessionLocal
from app.db.models import User

log = logging.getLogger("deep_tutor.api.auth")

# 认证失败 per-IP 限流（iter 368）：失败计数才消耗桶——已认证请求不受影响；
# 30/min 仍宽于正常客户端多标签页恢复（token 吊销后各页重试），但足以把
# PAT/.env token 在线爆破约束到每年 ~10^7 量级组合以下
_AUTH_FAIL_PER_MIN = 30


def _client_host(request: Request | None) -> str:
    return request.client.host if request is not None and request.client else "unknown"


def _gate_auth_failure(request: Request | None) -> None:
    """认证失败统一出口：per-IP 失败限流 + 日志（ip + 路径，不含 token 原文）。"""
    from app.core.ratelimit import limiter

    host = _client_host(request)
    path = request.url.path if request is not None else ""
    if not limiter.check(f"auth_fail:{host}", _AUTH_FAIL_PER_MIN):
        log.warning("认证失败限流触发 ip=%s path=%s", host, path)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="认证失败次数过多，请稍后再试",
        )
    log.warning("认证失败 ip=%s path=%s", host, path)


def _extract_token(authorization: str | None, x_auth_token: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if x_auth_token:
        return x_auth_token
    return None


def _is_env_token(token: str) -> bool:
    # auth_token_configured：历史模板占位值（change-me-please）不是有效凭证，
    # 不参与比较（否则人尽皆知的占位串即可认证，iter 90）。
    # iter 368：encode 后按字节比较——compare_digest 对非 ASCII str 抛 TypeError，
    # 恶意 Bearer 头（多字节字符）会把 401 变 500
    return settings.auth_token_configured and secrets.compare_digest(
        token.encode(), settings.auth_token.encode()
    )


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


def _jwt_payload_valid(db: Session, payload: dict) -> bool:
    """JWT 载荷的业务有效性：签发用户仍存在 + 未被吊销（tokens_valid_after，err.md A9）。

    与 resolve_user 的 JWT 分支同口径（含 +1s iat 容差，iter 246）。require_auth 此前
    只做密码学校验即放行——用户已删/改密后的旧 JWT 仍可通过仅挂 require_auth 的路由
    （iter 367 批次五）。单 PK 读，成本相对每请求已开的会话可忽略。
    """
    try:
        user_id = int(payload.get("sub", ""))
        iat = float(payload.get("iat", 0))
    except (TypeError, ValueError):
        return False
    user = db.get(User, user_id)
    if user is None:
        return False
    if iat + 1 <= (user.tokens_valid_after or 0):
        return False
    return True


async def require_auth(
    authorization: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None, alias="X-Auth-Token"),
    db: Session = Depends(get_db),
    request: Request = None,  # type: ignore[assignment]  # FastAPI 注入；测试直调可传 None
) -> None:
    """校验请求认证：JWT / AUTH_TOKEN / 个人访问令牌任一通过即可；
    零配置开放本机模式（passwordless_open，iter 89）直接放行。
    JWT 须同时通过业务校验（用户存在 + 未吊销，iter 367）。
    失败路径 per-IP 限流 + 日志（iter 368，防 PAT 在线爆破）。"""
    token = _extract_token(authorization, x_auth_token)
    if token:
        payload = decode_token(token)
        if payload is not None:
            if _jwt_payload_valid(db, payload):
                return
        if _is_env_token(token):
            return
        if _user_by_token_credential(db, token) is not None:
            return
    # 失败路径才查库：已认证请求不新增 DB 查询
    from app.core.security import passwordless_open

    if passwordless_open(db):
        return
    _gate_auth_failure(request)
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


def resolve_user(
    db: Session,
    authorization: str | None,
    x_auth_token: str | None,
    *,
    create_missing: bool = True,
) -> User | None:
    """解析请求身份为 User（JWT -> 对应用户；AUTH_TOKEN -> 首个管理员/首个用户）；失败 -> None。

    AUTH_TOKEN 回退路径下若库中无任何用户 -> 自动创建默认用户（与旧单用户行为一致，
    保证仅配置 AUTH_TOKEN 的本地部署/既有调用方在 P4 后仍可用）。
    零配置开放本机模式（iter 89）：无有效 token 且 passwordless_open -> 首个管理员
    （懒创建空密码 admin）。

    iter 394：``create_missing=False`` 时**不创建**任何用户（.env token 回退的默认用户、
    开放模式的懒创建 admin 一律只查不建）——供公开只读端点（如 GET /setup/status）
    解析调用者身份使用，避免公开 GET 产生写副作用。
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
            if iat + 1 <= (user.tokens_valid_after or 0):  # iter 246：+1s 容差——iat 为
                # int() 截断值而 tva 为 float，同秒"撤销后重登录"会被小数位误杀（born-revoked）；
                # 同秒放行（同秒内"撤销前签发"概率对本地单用户可忽略），跨秒判定不变
                return None
            return user
        if _is_env_token(token):
            admin = db.query(User).filter_by(is_admin=True).first()
            if admin is not None:
                return admin
            return get_default_user(db) if create_missing else None
        # 个人访问令牌（iter 223）：命中 active auth_token 档案 -> 档案归属用户
        token_user = _user_by_token_credential(db, token)
        if token_user is not None:
            return token_user
    # 零配置开放本机模式（iter 89）：无有效 token 且未设任何密码/未配 AUTH_TOKEN
    # -> 首个管理员（无则懒创建空密码 admin，数据自始归属 admin）
    from app.core.security import get_or_create_open_admin, passwordless_open

    if passwordless_open(db):
        if create_missing:
            return get_or_create_open_admin(db)
        admin = db.query(User).filter_by(is_admin=True).first()
        return admin  # 可能为 None（开放模式尚未懒创建 admin）
    return None


async def get_current_user(
    authorization: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None, alias="X-Auth-Token"),
    db: Session = Depends(get_db),
    request: Request = None,  # type: ignore[assignment]  # FastAPI 注入；测试直调可传 None
) -> User:
    """当前登录用户（401 未认证）。数据隔离端点以此取归属。

    失败路径 per-IP 限流 + 日志（iter 368）——部分仅挂本依赖的路由（/auth/me、
    setup 等）不经过 require_auth，爆破同样在此被约束。"""
    user = resolve_user(db, authorization, x_auth_token)
    if user is None:
        _gate_auth_failure(request)
        raise HTTPException(status_code=401, detail="invalid auth token")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """管理员守卫（用户管理端点用）。"""
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")
    return user


async def get_current_user_id(
    authorization: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None, alias="X-Auth-Token"),
    request: Request = None,  # type: ignore[assignment]  # FastAPI 注入；测试直调可传 None
) -> int:
    """SSE / 长连接端点用：解析当前用户 id 并**立即释放** DB 会话（iter 366）。

    与 get_current_user 的区别：后者返回 User ORM 对象，其 get_db 会话直至请求结束
    （含 StreamingResponse 整个流期）才关闭——SSE 长连接会因此钉死一条连接池槽位
    数分钟。100 用户的 EventsBridge（每标签一条常驻 SSE）即耗尽连接池。本依赖解析
    user.id 后在 finally 内关闭会话，流期不再占用连接（流内需要查库时各自开短会话）。
    失败路径同 get_current_user：per-IP 限流 + 日志（iter 368）。
    """
    db = SessionLocal()
    try:
        user = resolve_user(db, authorization, x_auth_token)
        if user is None:
            _gate_auth_failure(request)
            raise HTTPException(status_code=401, detail="invalid auth token")
        return user.id
    finally:
        db.close()


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
    from datetime import date, datetime, time, timedelta

    from sqlalchemy import func

    from app.api.errors import ApiError
    from app.db import models

    limit = get_daily_budget(db, user_id)
    if limit <= 0:
        return
    # iter 167 口径：本地日，与 /usage today_cost 一致——此前用 UTC 日，UTC+8 0-8 点的
    # 花费被计入"昨天"，守卫与用量页显示不符。
    # iter 369：谓词改可索引形式（created_at >= 本地 0 点换算 UTC naive，与 usage.py
    # since_utc 同源换算）——原 func.date(col,'localtime') 对列套函数，无法走
    # (user_id, created_at) 复合索引，每个计费请求全扫该用户历史 usage 行（线性劣化）。
    local_offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    since_utc = datetime.combine(date.today(), time.min) - local_offset
    spent = (
        db.query(func.coalesce(func.sum(models.ApiUsage.cost), 0.0))
        .filter(
            models.ApiUsage.user_id == user_id,
            models.ApiUsage.created_at >= since_utc,
        )
        .scalar()
        or 0.0
    )
    if spent >= limit:
        raise ApiError(
            "budget_exceeded",
            # iter 371：币种口径统一 CNY（前端全链￥，$ 残留修正）
            f"当日 LLM 预算已用尽（￥{spent:.4f} / ￥{limit:.4f}），请明天再试或在设置页调整日预算",
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

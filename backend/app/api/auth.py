"""认证 API（P4 多用户，iter 57）。

POST /api/auth/login  用户名+密码 -> JWT（前端存 localStorage 后随 Bearer 发送）
GET  /api/auth/me     当前用户信息（JWT 或 AUTH_TOKEN 回退身份）

login 不设 require_auth（认证本身）；失败统一 401（不区分用户不存在/密码错，防枚举）。
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.errors import ApiError
from app.core.concurrency import PasswordVerifyBusy, password_pool, password_verify_slot
from app.core.ratelimit import limiter
from app.core.security import create_token, dummy_verify, verify_password
from app.db.models import User

log = logging.getLogger("deep_tutor.api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

# 登录限流（err.md A8：防在线爆破；按 IP+用户名维度，每分钟 10 次）
_LOGIN_RATE_PER_MIN = 10
# 账号维度聚合桶：ip+username 桶可被多 IP 分布式爆破绕过（每桶独立计数），
# 账号维度再设一道较宽上限（正常用户不会从 >20 个不同 IP 一分钟内登录同一账号）
_LOGIN_ACCT_RATE_PER_MIN = 20
# iter 368：per-IP 聚合桶——每次登录尝试无论命中哪个用户名都执行一次 pbkdf2（600k 轮，
# 实测 ~0.19s CPU），单 IP 高频轮换用户名可绕过上面两个桶白耗 CPU；60/min 仍宽于共享
# 出口 NAT 的正常使用
_LOGIN_IP_RATE_PER_MIN = 60
# iter 397：并发口令校验上限（超出者等待超时后按 429 快速失败，不无限排队）
_LOGIN_VERIFY_TIMEOUT = 5.0


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def _user_tag(username: str) -> str:
    """用户名的稳定摘要（12 hex）——用于限流键与日志（不落明文）。

    iter 397（E13）：限流键改用摘要而非原文——键长度有界（防内存膨胀），
    也不把明文用户名留在进程内存里。
    """
    return hashlib.sha256(username.encode()).hexdigest()[:12]


def _acct_bucket_hit(user_tag: str) -> None:
    """账号维度失败桶：命中上限时按 429 快失败（文案与 IP 级统一）。

    iter 397（E12）：只计**失败**尝试；口令正确不受该桶影响（账号不会被锁死）。
    文案与 IP 级统一——此前账号级 429 文案不同，可用于确认某账号存在。
    """
    if not limiter.check(f"login:acct:{user_tag}", _LOGIN_ACCT_RATE_PER_MIN):
        raise ApiError("rate_limited", "登录尝试过于频繁，请稍后再试", status=429)


def _user_out(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username or "",
        "name": user.name,
        "is_admin": bool(user.is_admin),
    }


async def _verify_password_async(password: str, stored: str) -> bool:
    """口令校验：独立小线程池 + 并发闸（iter 397）。

    iter 397 背景：login 此前是**同步端点** -> 跑在 Starlette 的 anyio 工作线程池
    （默认 40 槽）。每次尝试一次 pbkdf2-hmac-sha256 600k 轮（实测 ~0.19s CPU），
    40 个并发登录即可占满全部槽位，使**所有同步端点**（用户管理/会话/导出/分享）
    排队——未认证的 CPU 放大器。改 async 端点后，口令校验移到独立小池（不占
    anyio 槽位），并加并发闸：超额并发等待超时后按 429 快速失败而非无限排队。
    """
    loop = asyncio.get_running_loop()
    try:
        async with password_verify_slot(_LOGIN_VERIFY_TIMEOUT):
            return await loop.run_in_executor(
                password_pool(), functools.partial(verify_password, password, stored)
            )
    except PasswordVerifyBusy:
        raise ApiError("rate_limited", "登录请求过多，请稍后再试", status=429) from None


async def _dummy_verify_async(password: str) -> None:
    """用户不存在时的等耗时路径（防用户名枚举），口径同 _verify_password_async。"""
    loop = asyncio.get_running_loop()
    try:
        async with password_verify_slot(_LOGIN_VERIFY_TIMEOUT):
            await loop.run_in_executor(password_pool(), functools.partial(dummy_verify, password))
    except PasswordVerifyBusy:
        raise ApiError("rate_limited", "登录请求过多，请稍后再试", status=429) from None


@router.post("/login")
async def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> dict:
    # err.md A8：限流 + 时序拉平（不存在的用户也执行一次 pbkdf2 运算，
    # 消除「用户不存在短路 vs 600k 轮校验」的响应时间差，防用户名枚举）
    # iter 397：端点改 async——同步端点会在 anyio 工作线程池里占住槽位做纯 CPU
    # 的 pbkdf2（未认证即可触发），见 _verify_password_async 的说明。
    client_host = request.client.host if request.client else "unknown"
    # iter 397（E9）：用户名归一化——建号时 strip（users.create_user），登录时不 strip
    # -> 带首尾空白的账号（API 客户端常见）永远登不上，且两个限流桶键会因空白变体
    # 被分裂成多桶。归一后查库与限流键同口径。
    username = body.username.strip()
    # iter 397（E13）：用户名摘要（限流键与日志共用，不落明文）
    user_tag = _user_tag(username)
    if not limiter.check(f"login:ip:{client_host}", _LOGIN_IP_RATE_PER_MIN):
        raise ApiError("rate_limited", "登录尝试过于频繁，请稍后再试", status=429)
    if not limiter.check(f"login:{client_host}:{user_tag}", _LOGIN_RATE_PER_MIN):
        raise ApiError("rate_limited", "登录尝试过于频繁，请稍后再试", status=429)
    # iter 397（E13）：限流键用**用户名的摘要**而非原文——键长度有界（防内存
    # 膨胀）、不把明文用户名留在进程内存里；摘要仍稳定（同用户名同桶）。
    #
    # iter 397（E12）：**账号维度桶改为「只计失败」且成功放行**。原实现把该桶
    # 放在校验之前 -> 任何人对某用户名连打 20 次即可把该账号**全局**锁 1 分钟
    # （跨 IP，429 文案还不与 IP 级不同 -> 可用于确认某账号存在且正被攻击），
    # 真实用户被挡在门外。现：账号桶只在**校验失败**时命中（把分布式爆破的
    # 每分钟失败次数钳在全局上限内），而**口令正确一律放行**——账号主人不会被
    # 别人锁死，暴力破解的失败速率仍被全局封顶。
    # 用户名查库走索引点查（快），留在事件循环；耗 CPU 的 pbkdf2 才进独立池
    user = db.query(User).filter_by(username=username).first()
    if user is None:
        await _dummy_verify_async(body.password)
        _acct_bucket_hit(user_tag)
        log.warning("登录失败（用户不存在）ip=%s user=%s", client_host, user_tag)
        raise ApiError("unauthorized", "用户名或密码错误", status=401)
    if not await _verify_password_async(body.password, user.password_hash):
        _acct_bucket_hit(user_tag)
        log.warning("登录失败（密码错误）ip=%s user=%s uid=%s", client_host, user_tag, user.id)
        raise ApiError("unauthorized", "用户名或密码错误", status=401)
    return {"token": create_token(user.id, user.username or ""), "user": _user_out(user)}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return _user_out(user)

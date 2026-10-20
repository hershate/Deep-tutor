"""首次设置向导 API（iter 89 零配置开箱即用）。

无 .env 零配置部署时，前端首访进入设置向导（可跳过）：
- GET  /setup/status  公开：返回向导所需的状态标志（granular flags，
  后续新增向导步骤只需加字段）
- POST /setup         开放模式或 admin：一次性完成「设访问密码 / 配 LLM API Key」
- POST /setup/dismiss 同上门禁（_setup_caller，iter 367 收紧——此前完全公开）：
  仅标记已跳过（纯 UI 引导开关）

设计要点：
- body 字段全可选 + pydantic extra=ignore -> 后续新增配置项不破坏旧客户端。
- 权限判定收敛为 _setup_caller 依赖（开放模式 -> 当前管理员；否则需 admin JWT），
  未来「管理员重配置」类端点可直接复用。
- iter 394：开放模式（无人设过口令）下 POST /setup 与 /setup/dismiss **仅回环
  客户端可调用**——这两个端点是唯一的「认领管理员」入口，开放给任意可达客户端
  等于允许远程抢占管理员口令/翻转全局 dismiss 状态。LAN 部署改用 .env 的
  ADMIN_PASSWORD / AUTH_TOKEN（.env.example 有模板）。
- 密码设置到调用者本人账号（开放模式即懒创建的 admin），并吊销其旧 JWT
  （tokens_valid_after，err.md A9 模式）；响应带新 JWT，前端立即登录无感切换。
- API Key 以固定名档案 upsert 并激活（供应商档案体系，iter 50-53）；
  任何响应不回显明文 key。
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, resolve_user
from app.api.errors import ApiError
from app.api.providers import _validate_base_url
from app.core.ratelimit import limiter
from app.core.security import create_token, hash_password
from app.core.settings import settings
from app.core.setup_state import is_dismissed, mark_dismissed
from app.db import models
from app.db.models import User
from app.llm import get_active_profile

router = APIRouter(prefix="/setup", tags=["setup"])  # 公开路由：不挂 require_auth

_DEFAULT_PROFILE_NAME = "默认"
# 写操作 per-IP 限流（iter 368）：开放模式下 POST /setup 是唯一"接管"入口，
# 限流拉长首个来访者竞态窗口（正常使用一次即成，不受影响）
_SETUP_WRITE_PER_MIN = 10


def _is_loopback_client(request: Request | None) -> bool:
    """客户端是否为回环地址（127.0.0.0/8 / ::1 / ::ffff:127.x）。

    只看**直连对端**（request.client.host），不信任 X-Forwarded-For——该头可被
    客户端伪造，用它判定会把「可伪造的头」变成绕过回环限制的通路。
    经反向代理部署时直连对端是代理地址（非回环）-> 开放模式下 setup 端点
    不可达（须在 .env 配 ADMIN_PASSWORD / AUTH_TOKEN 后走认证路径）。
    """
    if request is None or request.client is None:
        return False
    host = (request.client.host or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "::1", "0:0:0:0:0:0:0:1"}:
        return True
    if host.startswith("::ffff:"):  # IPv4-mapped IPv6
        host = host[len("::ffff:") :]
    try:
        import ipaddress

        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _setup_caller(
    db: Annotated[Session, Depends(get_db)],
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_auth_token: Annotated[str | None, Header(alias="X-Auth-Token")] = None,
) -> User:
    """设置操作调用者：resolve_user 已覆盖开放模式（返回懒创建 admin）；
    未认证 -> 401；非管理员 -> 403。

    iter 394：开放模式下本端点组是**唯一的「认领管理员」入口**——未认证方可抢先
    写 password_hash（并吊销 admin 的 JWT/PAT）或翻转全局 dismiss 状态。开放模式
    按定义「无人设过口令」，此时只允许**回环客户端**调用（LAN 部署须用 .env 的
    ADMIN_PASSWORD / AUTH_TOKEN，或经 SSH 隧道访问本机）。
    判定置于 resolve_user 之前：避免远程未认证调用触发「懒创建 admin」的写副作用。
    """
    from app.core.security import passwordless_open

    if passwordless_open(db) and not _is_loopback_client(request):
        raise ApiError("forbidden", "开放模式下首次设置仅允许本机（回环地址）访问", status=403)
    user = resolve_user(db, authorization, x_auth_token)
    if user is None:
        raise ApiError("unauthorized", "未认证", status=401)
    if not user.is_admin:
        raise ApiError("forbidden", "首次设置需要管理员权限", status=403)
    return user


class SetupIn(BaseModel):
    """向导提交：全字段可选，仅执行提供了的项（extra=ignore 向前兼容）。"""

    model_config = ConfigDict(extra="ignore")

    password: str | None = Field(default=None, min_length=8, max_length=256)  # iter 368：4 -> 8
    api_key: str | None = Field(default=None, min_length=1, max_length=256)
    base_url: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, max_length=128)

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        if v:
            _validate_base_url(v)
        return v


def _llm_configured(db: Session, user_id: int | None) -> bool:
    """「get_provider(user_id) 可用」语义（**按调用者**）：env key（模板占位值不算，iter 90）、
    该用户的 active 供应商档案、或该用户的 active Anthropic 回退 Key 凭证档案（iter 223）。

    iter 394：此前用全局查询（任一用户的 active 档案即算已配置）——多用户下
    所有用户看到同一个值：甲配了 key，乙没配也被告知「已配置」，横幅不出现
    而调用必失败。user_id=None -> 实例级（任一用户的 active 档案），供 needs_setup（实例级流程）判定。
    """
    from app.core.credentials import KIND_ANTHROPIC, get_active_credential

    if settings.anthropic_key_configured:
        return True
    return (
        get_active_profile(user_id, db=db) is not None
        or get_active_credential(user_id, KIND_ANTHROPIC, db) is not None
    )


@router.get("/status")
def setup_status(
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
    x_auth_token: Annotated[str | None, Header(alias="X-Auth-Token")] = None,
) -> dict:
    """向导状态（公开端点）。

    iter 394：llm_configured 改**按调用者**解析——多用户下全局口径会让未配 key 的
    用户被告知「已配置」（横幅不出现，调用却失败）。调用者身份解析用
    resolve_user(create_missing=False)：公开 GET 不产生建用户的写副作用。
    needs_setup 仍是**实例级**判定（向导是实例级一次性流程）：按调用者判定会把
    「本用户没配 key」误判成「实例待初始化」，把普通用户弹去 /setup。
    """
    from app.core.security import passwordless_open

    open_mode = passwordless_open(db)
    user = resolve_user(db, authorization, x_auth_token, create_missing=False)
    llm_ok = _llm_configured(db, user.id if user is not None else None)
    llm_ok_instance = _llm_configured(db, None)
    dismissed = is_dismissed()
    return {
        "open_mode": open_mode,
        "llm_configured": llm_ok,
        "dismissed": dismissed,
        "needs_setup": (not dismissed) and (open_mode or not llm_ok_instance),
    }


@router.post("")
def setup(
    body: SetupIn,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(_setup_caller)],
    request: Request,
) -> dict:
    client_host = request.client.host if request.client else "unknown"
    if not limiter.check(f"setup_write:{client_host}", _SETUP_WRITE_PER_MIN):
        raise ApiError("rate_limited", "操作过于频繁，请稍后再试", status=429)
    password_set = False
    provider_configured = False

    if body.password:
        user.password_hash = hash_password(body.password)
        # 吊销改密前签发的 JWT（err.md A9）。取整秒：本端点随即签发新 token，
        # JWT iat 为 int 秒——若用 float 秒，新 token iat 被截断后 < tokens_valid_after
        # 会被立即误判为已吊销（测试实证）；代价是与改密同一秒内签发的旧 token
        # 不被吊销（攻击者无法在该秒级窗口内先取得合法 JWT，可接受）
        user.tokens_valid_after = float(int(time.time()))
        # iter 368：改密同时吊销该用户全部 active PAT（同 users.update_user 口径）
        from app.core.credentials import KIND_AUTH_TOKEN

        db.query(models.CredentialProfile).filter_by(
            user_id=user.id, kind=KIND_AUTH_TOKEN, is_active=True
        ).update({"is_active": False})
        password_set = True

    if body.api_key:
        profile = (
            db.query(models.LlmProviderProfile)
            .filter_by(user_id=user.id, name=_DEFAULT_PROFILE_NAME)
            .first()
        )
        if profile is None:
            profile = models.LlmProviderProfile(
                user_id=user.id, name=_DEFAULT_PROFILE_NAME, api_key="", is_active=False
            )
            db.add(profile)
        profile.api_key = body.api_key
        if body.base_url is not None:
            profile.base_url = body.base_url
        if body.model is not None:
            profile.model = body.model
        # 激活该档案（同一用户内至多一个 active，同 providers.activate 模式）
        for other in db.query(models.LlmProviderProfile).filter_by(user_id=user.id, is_active=True).all():
            other.is_active = False
        profile.is_active = True
        provider_configured = True

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError("conflict", "供应商档案名称冲突，请重试", status=409) from None

    mark_dismissed()
    out = {"ok": True, "password_set": password_set, "provider_configured": provider_configured}
    if password_set:
        out["token"] = create_token(user.id, user.username or "")
        out["user"] = {
            "id": user.id,
            "username": user.username or "",
            "name": user.name,
            "is_admin": bool(user.is_admin),
        }
    return out


@router.post("/dismiss")
def dismiss(
    user: Annotated[User, Depends(_setup_caller)],
    request: Request,
) -> dict:
    """iter 367（批次五）：补 _setup_caller 门禁——此前完全公开，认证已配置时任何
    未认证调用方可翻全局 dismiss 状态（向导从此不再弹出，诱导部署停留在未配置态）。
    开放模式下与 POST /setup 同语义（resolve_user 懒创建 admin，首个来访者）。"""
    client_host = request.client.host if request.client else "unknown"
    if not limiter.check(f"setup_write:{client_host}", _SETUP_WRITE_PER_MIN):
        raise ApiError("rate_limited", "操作过于频繁，请稍后再试", status=429)
    mark_dismissed()
    return {"ok": True}

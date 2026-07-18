"""首次设置向导 API（iter 89 零配置开箱即用）。

无 .env 零配置部署时，前端首访进入设置向导（可跳过）：
- GET  /setup/status  公开：返回向导所需的状态标志（granular flags，
  后续新增向导步骤只需加字段）
- POST /setup         开放模式或 admin：一次性完成「设访问密码 / 配 LLM API Key」
- POST /setup/dismiss 公开：仅标记已跳过（纯 UI 引导开关，无安全面）

设计要点：
- body 字段全可选 + pydantic extra=ignore -> 后续新增配置项不破坏旧客户端。
- 权限判定收敛为 _setup_caller 依赖（开放模式 -> 当前管理员；否则需 admin JWT），
  未来「管理员重配置」类端点可直接复用。
- 密码设置到调用者本人账号（开放模式即懒创建的 admin），并吊销其旧 JWT
  （tokens_valid_after，err.md A9 模式）；响应带新 JWT，前端立即登录无感切换。
- API Key 以固定名档案 upsert 并激活（供应商档案体系，iter 50-53）；
  任何响应不回显明文 key。
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, resolve_user
from app.api.errors import ApiError
from app.api.providers import _validate_base_url
from app.core.security import create_token, hash_password
from app.core.settings import settings
from app.core.setup_state import is_dismissed, mark_dismissed
from app.db import models
from app.db.models import User
from app.llm import get_active_profile

router = APIRouter(prefix="/setup", tags=["setup"])  # 公开路由：不挂 require_auth

_DEFAULT_PROFILE_NAME = "默认"


def _setup_caller(
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
    x_auth_token: Annotated[str | None, Header(alias="X-Auth-Token")] = None,
) -> User:
    """设置操作调用者：resolve_user 已覆盖开放模式（返回懒创建 admin）；
    未认证 -> 401；非管理员 -> 403。"""
    user = resolve_user(db, authorization, x_auth_token)
    if user is None:
        raise ApiError("unauthorized", "未认证", status=401)
    if not user.is_admin:
        raise ApiError("forbidden", "首次设置需要管理员权限", status=403)
    return user


class SetupIn(BaseModel):
    """向导提交：全字段可选，仅执行提供了的项（extra=ignore 向前兼容）。"""

    model_config = ConfigDict(extra="ignore")

    password: str | None = Field(default=None, min_length=4, max_length=256)
    api_key: str | None = Field(default=None, min_length=1, max_length=256)
    base_url: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, max_length=128)

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        if v:
            _validate_base_url(v)
        return v


def _llm_configured() -> bool:
    """「get_provider() 可用」语义：env key 或任一 active 供应商档案。"""
    return bool(settings.anthropic_api_key) or get_active_profile() is not None


@router.get("/status")
def setup_status(db: Annotated[Session, Depends(get_db)]) -> dict:
    from app.core.security import passwordless_open

    open_mode = passwordless_open(db)
    llm_ok = _llm_configured()
    dismissed = is_dismissed()
    return {
        "open_mode": open_mode,
        "llm_configured": llm_ok,
        "dismissed": dismissed,
        "needs_setup": (not dismissed) and (open_mode or not llm_ok),
    }


@router.post("")
def setup(body: SetupIn, db: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(_setup_caller)]) -> dict:
    password_set = False
    provider_configured = False

    if body.password:
        user.password_hash = hash_password(body.password)
        # 吊销改密前签发的 JWT（err.md A9）。取整秒：本端点随即签发新 token，
        # JWT iat 为 int 秒——若用 float 秒，新 token iat 被截断后 < tokens_valid_after
        # 会被立即误判为已吊销（测试实证）；同秒旧 token 宽限 1s 可接受
        user.tokens_valid_after = float(int(time.time()))
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
def dismiss() -> dict:
    mark_dismissed()
    return {"ok": True}

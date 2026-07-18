"""LLM 供应商档案 API（用户自定义供应商，类 CC-switch，iter 51）。

多档案切换：CRUD + activate（同一用户内同时至多一个 active）+ deactivate（回退 .env）。
P4 按用户隔离（iter 58）：档案归属用户（user_id），一切操作仅作用于当前用户的档案；
同名冲突仅在同用户内判定。跨用户访问一律 404（不泄露存在性）。
安全：api_key 只写不读——任何响应不回显明文，仅返回掩码末 4 位；
更新时不传 api_key 则保留原值。base_url 仅允许 http(s)（防 file:// 等协议注入）。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth
from app.api.errors import ApiError
from app.core.settings import settings
from app.db import models
from app.db.models import User
from app.llm.anthropic_provider import AnthropicProvider

router = APIRouter(prefix="/providers", tags=["providers"], dependencies=[Depends(require_auth)])

_HTTPS_RE = re.compile(r"^https?://", re.IGNORECASE)


def _mask(key: str) -> str:
    """密钥掩码：仅末 4 位可见（不回显明文）。"""
    return f"...{key[-4:]}" if len(key) >= 4 else "****"


def _out(p: models.LlmProviderProfile) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "base_url": p.base_url or "",
        "model": p.model or "",
        "is_active": bool(p.is_active),
        "api_key_masked": _mask(p.api_key),
        "created_at": str(p.created_at),
    }


def _validate_base_url(v: str) -> str:
    if v and not _HTTPS_RE.match(v):
        raise ValueError("base_url 必须以 http:// 或 https:// 开头")
    return v


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=1, max_length=256)
    base_url: str = Field(default="", max_length=512)
    model: str = Field(default="", max_length=128)

    _check_url = field_validator("base_url")(_validate_base_url)


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    api_key: str | None = Field(default=None, min_length=1, max_length=256)  # None -> 保留原值
    base_url: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, max_length=128)

    _check_url = field_validator("base_url")(_validate_base_url)


def _owned_or_404(db: Session, profile_id: int, user: User) -> models.LlmProviderProfile:
    """当前用户拥有的档案；不存在/属他人 -> 404（不泄露存在性）。"""
    p = db.get(models.LlmProviderProfile, profile_id)
    if p is None or p.user_id != user.id:
        raise ApiError("not_found", f"供应商档案 {profile_id} 不存在")
    return p


@router.get("")
def list_providers(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    rows = (
        db.query(models.LlmProviderProfile)
        .filter_by(user_id=user.id)
        .order_by(models.LlmProviderProfile.id)
        .all()
    )
    return {"providers": [_out(p) for p in rows]}


@router.post("", status_code=201)
def create_provider(
    body: ProviderIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    dup = db.query(models.LlmProviderProfile).filter_by(user_id=user.id, name=body.name).first()
    if dup is not None:
        raise ApiError("conflict", f"供应商名称已存在: {body.name}", status=409)
    p = models.LlmProviderProfile(
        user_id=user.id,
        name=body.name,
        base_url=body.base_url,
        api_key=body.api_key,
        model=body.model,
        is_active=False,
    )
    db.add(p)
    try:
        db.commit()
    except IntegrityError:
        # err.md A16：并发创建撞 uq_provider_user_name -> 409（而非 500）
        db.rollback()
        raise ApiError("conflict", f"供应商名称已存在: {body.name}", status=409) from None
    db.refresh(p)
    return _out(p)


@router.put("/{profile_id}")
def update_provider(
    profile_id: int,
    body: ProviderUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    p = _owned_or_404(db, profile_id, user)
    if body.name is not None and body.name != p.name:
        dup = db.query(models.LlmProviderProfile).filter_by(user_id=user.id, name=body.name).first()
        if dup is not None:
            raise ApiError("conflict", f"供应商名称已存在: {body.name}", status=409)
        p.name = body.name
    if body.base_url is not None:
        p.base_url = body.base_url
    if body.model is not None:
        p.model = body.model
    if body.api_key is not None:  # 不传 -> 保留原值
        p.api_key = body.api_key
    try:
        db.commit()
    except IntegrityError:
        # err.md A16：并发改名撞 uq_provider_user_name -> 409（而非 500）
        db.rollback()
        raise ApiError("conflict", f"供应商名称已存在: {body.name}", status=409) from None
    db.refresh(p)
    return _out(p)


@router.delete("/{profile_id}")
def delete_provider(
    profile_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    p = _owned_or_404(db, profile_id, user)
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.post("/{profile_id}/activate")
def activate_provider(
    profile_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """启用档案（同一用户内同时至多一个 active；启用后 get_provider(user_id) 走该档案）。"""
    p = _owned_or_404(db, profile_id, user)
    for other in db.query(models.LlmProviderProfile).filter_by(user_id=user.id, is_active=True).all():
        other.is_active = False
    p.is_active = True
    db.commit()
    return _out(p)


@router.post("/deactivate")
def deactivate_providers(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """取消当前用户所有档案启用 -> get_provider 回退 .env（ANTHROPIC_API_KEY）。"""
    for p in db.query(models.LlmProviderProfile).filter_by(user_id=user.id, is_active=True).all():
        p.is_active = False
    db.commit()
    return {"ok": True}


@router.post("/{profile_id}/test")
async def test_provider(
    profile_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """连通性测试（iter 53）：用档案凭据发起一次最小 LLM 调用，返回 ok/error。

    不激活档案、不改任何状态；错误信息截断（防回显敏感内容）。
    """
    p = _owned_or_404(db, profile_id, user)
    model = p.model or settings.anthropic_model
    provider = AnthropicProvider(api_key=p.api_key, model=model, base_url=p.base_url or None)
    try:
        await provider.chat("你是连通性测试探针。", "ping", max_tokens=8, cache_system=False)
    except Exception as exc:  # noqa: BLE001  任何失败均作为测试结果返回（非 500）
        return {"ok": False, "model": model, "error": str(exc)[:200]}
    return {"ok": True, "model": model}

"""API Key 凭证档案 API（iter 223：设置页可视化管理全部 Key，多档案卡片切换）。

三类 kind（app.core.credentials）：
- anthropic（Anthropic 回退主 Key，单活）：get_provider 优先级
  LLM 供应商档案 > 本类 active 档案 > .env；全部停用回退 .env。
- bing（Bing 搜索 Key，单活）：资源检索取 active 档案 key，无 -> .env（再无 -> 免 Key 网页版）。
- auth_token（个人访问令牌 PAT，多活）：任一 active 令牌以归属用户身份通过认证；
  .env AUTH_TOKEN 始终有效作回退（防锁死）。

安全约定（同 providers.py）：
- api_key 只写不读——任何响应不回显明文，仅返回掩码末 4 位；更新不传 api_key 则保留原值。
- P4 按用户隔离：一切操作仅作用于当前用户的档案；跨用户访问一律 404（不泄露存在性）。
- auth_token 类 key 最小 8 位（认证凭证，防弱口令式令牌）。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth
from app.api.errors import ApiError
from app.core.credentials import CREDENTIAL_KINDS, KIND_AUTH_TOKEN, SINGLE_ACTIVE_KINDS
from app.db import models
from app.db.models import User

router = APIRouter(prefix="/credentials", tags=["credentials"], dependencies=[Depends(require_auth)])

_MAX_PER_KIND = 20  # 每用户每类档案上限（与其他资源配额同族）
Kind = Literal["anthropic", "bing", "auth_token"]


def _mask(key: str) -> str:
    """密钥掩码：仅末 4 位可见（不回显明文）。"""
    return f"...{key[-4:]}" if len(key) >= 4 else "****"


def _out(p: models.CredentialProfile) -> dict:
    return {
        "id": p.id,
        "kind": p.kind,
        "name": p.name,
        "is_active": bool(p.is_active),
        "api_key_masked": _mask(p.api_key),
        "created_at": str(p.created_at),
    }


def _check_key_strength(kind: str, api_key: str) -> None:
    """auth_token 类为认证凭证，最小 8 位防弱令牌（其余类同供应商档案 min_length=1）。"""
    if kind == KIND_AUTH_TOKEN and len(api_key) < 8:
        raise ApiError("bad_request", "访问令牌至少 8 位（它可直接用于登录你的账号）")


class CredentialIn(BaseModel):
    kind: Kind
    name: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=1, max_length=256)
    activate: bool = False  # 创建后立即启用（单活类会互斥停用同类其他档案）


class CredentialUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    api_key: str | None = Field(default=None, min_length=1, max_length=256)  # None -> 保留原值


def _owned_or_404(db: Session, credential_id: int, user: User) -> models.CredentialProfile:
    """当前用户拥有的档案；不存在/属他人 -> 404（不泄露存在性）。"""
    p = db.get(models.CredentialProfile, credential_id)
    if p is None or p.user_id != user.id:
        raise ApiError("not_found", f"凭证档案 {credential_id} 不存在")
    return p


def _check_dup_name(db: Session, user: User, kind: str, name: str) -> None:
    dup = (
        db.query(models.CredentialProfile)
        .filter_by(user_id=user.id, kind=kind, name=name)
        .first()
    )
    if dup is not None:
        raise ApiError("conflict", f"同类凭证名称已存在: {name}", status=409)


@router.get("")
def list_credentials(
    kind: Kind | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    q = db.query(models.CredentialProfile).filter_by(user_id=user.id)
    if kind is not None:
        q = q.filter_by(kind=kind)
    rows = q.order_by(models.CredentialProfile.kind, models.CredentialProfile.id).all()
    return {"credentials": [_out(p) for p in rows], "kinds": list(CREDENTIAL_KINDS)}


@router.post("", status_code=201)
def create_credential(
    body: CredentialIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    count = (
        db.query(models.CredentialProfile)
        .filter_by(user_id=user.id, kind=body.kind)
        .count()
    )
    if count >= _MAX_PER_KIND:
        raise ApiError(
            "quota_exceeded", f"每用户每类凭证档案上限 {_MAX_PER_KIND} 个", status=429
        )
    _check_dup_name(db, user, body.kind, body.name)
    _check_key_strength(body.kind, body.api_key)
    p = models.CredentialProfile(
        user_id=user.id, kind=body.kind, name=body.name, api_key=body.api_key, is_active=False
    )
    if body.activate:
        _activate(db, user, p)
    db.add(p)
    try:
        db.commit()
    except IntegrityError:
        # 并发创建撞 uq_credential_user_kind_name -> 409（同 err.md A16 模式）
        db.rollback()
        raise ApiError("conflict", f"同类凭证名称已存在: {body.name}", status=409) from None
    db.refresh(p)
    return _out(p)


@router.put("/{credential_id}")
def update_credential(
    credential_id: int,
    body: CredentialUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    p = _owned_or_404(db, credential_id, user)
    if body.name is not None and body.name != p.name:
        _check_dup_name(db, user, p.kind, body.name)
        p.name = body.name
    if body.api_key is not None:  # 不传 -> 保留原值
        _check_key_strength(p.kind, body.api_key)
        p.api_key = body.api_key
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError("conflict", "同类凭证名称已存在", status=409) from None
    db.refresh(p)
    return _out(p)


@router.delete("/{credential_id}")
def delete_credential(
    credential_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    p = _owned_or_404(db, credential_id, user)
    db.delete(p)
    db.commit()
    return {"ok": True}


def _activate(db: Session, user: User, p: models.CredentialProfile) -> None:
    """启用档案：单活类（anthropic/bing）互斥停用同类其他 active；auth_token 仅置位。"""
    if p.kind in SINGLE_ACTIVE_KINDS:
        for other in (
            db.query(models.CredentialProfile)
            .filter_by(user_id=user.id, kind=p.kind, is_active=True)
            .all()
        ):
            other.is_active = False
    p.is_active = True


@router.post("/{credential_id}/activate")
def activate_credential(
    credential_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    p = _owned_or_404(db, credential_id, user)
    _activate(db, user, p)
    db.commit()
    return _out(p)


@router.post("/{credential_id}/deactivate")
def deactivate_credential(
    credential_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    p = _owned_or_404(db, credential_id, user)
    p.is_active = False
    db.commit()
    return _out(p)


@router.post("/deactivate-all")
def deactivate_all(
    kind: Kind,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """停用当前用户该类全部档案 -> anthropic/bing 回退 .env；auth_token 全部令牌失效。"""
    for p in (
        db.query(models.CredentialProfile)
        .filter_by(user_id=user.id, kind=kind, is_active=True)
        .all()
    ):
        p.is_active = False
    db.commit()
    return {"ok": True}

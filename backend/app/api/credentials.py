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
- auth_token 类 key 最小 16 位（认证凭证，防弱口令式令牌；iter 394：8 -> 16）。
- iter 394：auth_token（PAT）以 sha256 摘要入库（明文入库 -> 库/备份泄露即令牌泄露），
  掩码显示「****（摘要）」；anthropic/bing 两类仍存明文（须原样发往供应商）。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_auth, require_local_in_open_mode
from app.api.errors import ApiError
from app.core.credentials import (
    CREDENTIAL_KINDS,
    KIND_AUTH_TOKEN,
    SINGLE_ACTIVE_KINDS,
    hash_token,
    is_hashed,
)
from app.core.quota import quota_slot
from app.core.ratelimit import limiter
from app.db import models
from app.db.models import User

router = APIRouter(prefix="/credentials", tags=["credentials"], dependencies=[Depends(require_auth)])

_MAX_PER_KIND = 20  # 每用户每类档案上限（与其他资源配额同族）
Kind = Literal["anthropic", "bing", "auth_token"]


def _check_write_rate(user: User) -> None:
    """凭证写操作限流（iter 365）：auth_token 类 PAT 可作持久化后门、api_key 明文可外带，
    JWT 泄露后若无写限流即可批量种令牌/外带密钥（PAT outlives JWT，改密吊销不清）。
    30/min：明显高于每类 20 的配额上限，让配额检查先生效、限流只防突发自动化
    （test_quota_per_kind_429 连续 22 次写不应被限流误伤）。"""
    if not limiter.check(f"cred_write:{user.id}", 30):
        raise ApiError("rate_limited", "操作过于频繁，请稍后再试", status=429)


_PAT_LOCAL_ONLY = "开放模式下个人访问令牌（PAT）仅允许本机（回环地址）创建或修改"


def _guard_pat_write(db: Session, request: Request | None, kind: str) -> None:
    """开放模式（零配置免认证）下 PAT 写入限回环（iter 397）。

    PAT 是**可认证的持久凭据**：开放模式下远程未认证客户端可建一个 active PAT，
    而 PAT 在管理员随后设密/配 AUTH_TOKEN（关闭开放模式）后**依然有效**——
    即「以为已经加固、实则留有后门」。改密路径会吊销 active PAT
    （users.update_user / setup 同口径），但 .env 配 AUTH_TOKEN 的收敛路径不会，
    后门更长命。故开放模式下的 PAT 建立/改写/启用一律限回环客户端。

    其余 kind（anthropic/bing 的供应商 Key）不建立对本实例的认证能力，不受限。
    """
    if kind != KIND_AUTH_TOKEN:
        return
    require_local_in_open_mode(db, request, _PAT_LOCAL_ONLY)


def _mask(key: str) -> str:
    """密钥掩码：仅末 4 位可见（不回显明文）。

    iter 394：长度 <8 时整体掩码——此前 len>=4 即回显末 4 位，4~7 位密钥会被
    **完整回显**（与「不回显明文」的口径矛盾；api_key 下限为 1 位）。
    iter 394：PAT 以 sha256 摘要存储（明文不入库），无「明文末 4 位」可显——
    统一显示「****（摘要）」，凭档案名区分（摘要前缀不可泄露，不可逆推）。
    """
    if is_hashed(key):
        return "****（摘要）"
    return f"...{key[-4:]}" if len(key) >= 8 else "****"


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
    """auth_token 类为认证凭证，最小 16 位防弱令牌（其余类同供应商档案 min_length=1）。

    iter 394：8 -> 16。认证失败限流为 per-IP 30/min（iter 368）——多 IP 分布式
    爆破下弱令牌空间（8 位自选口令常见 <10^8 有效组合）可在数日内穷举；
    16 位随机串空间 10^16 量级，爆破不可行。存量令牌不受影响（仅新建/改写校验）。
    """
    if kind == KIND_AUTH_TOKEN and len(api_key) < 16:
        raise ApiError("bad_request", "访问令牌至少 16 位（它可直接用于登录你的账号）")


def _stored_key(kind: str, api_key: str) -> str:
    """入库值：PAT 存 sha256 摘要（明文不入库），其余类存原值。"""
    if kind == KIND_AUTH_TOKEN:
        return hash_token(api_key)
    return api_key


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
    body: CredentialIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _check_write_rate(user)
    _guard_pat_write(db, request, body.kind)
    # iter 394：配额判定是「先查后写」——计数与插入之间若无互斥，25 个并发创建请求
    # 可同时通过「<20 上限」检查并写入 25 行（每类上限 20 被并发绕过）。
    # 单进程部署下进程内互斥即可（app/core/quota.py，分段锁，无界增长避免）。
    with quota_slot(f"credential:{user.id}:{body.kind}"):
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
            user_id=user.id,
            kind=body.kind,
            name=body.name,
            api_key=_stored_key(body.kind, body.api_key),
            is_active=False,
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
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _check_write_rate(user)
    p = _owned_or_404(db, credential_id, user)
    if body.api_key is not None:  # 改写 PAT 值 = 换一把攻击者已知的钥匙
        _guard_pat_write(db, request, p.kind)
    if body.name is not None and body.name != p.name:
        _check_dup_name(db, user, p.kind, body.name)
        p.name = body.name
    if body.api_key is not None:  # 不传 -> 保留原值
        _check_key_strength(p.kind, body.api_key)
        p.api_key = _stored_key(p.kind, body.api_key)
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
    _check_write_rate(user)
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
    credential_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    _check_write_rate(user)
    p = _owned_or_404(db, credential_id, user)
    # iter 397：启用 PAT = 把「已埋下的令牌」变成可用凭据——同样限回环
    _guard_pat_write(db, request, p.kind)
    _activate(db, user, p)
    db.commit()
    return _out(p)


@router.post("/{credential_id}/deactivate")
def deactivate_credential(
    credential_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    _check_write_rate(user)
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
    _check_write_rate(user)
    for p in (
        db.query(models.CredentialProfile)
        .filter_by(user_id=user.id, kind=kind, is_active=True)
        .all()
    ):
        p.is_active = False
    db.commit()
    return {"ok": True}

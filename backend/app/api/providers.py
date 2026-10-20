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

from app.api.deps import check_daily_budget, get_current_user, get_db, require_auth
from app.api.errors import ApiError
from app.core.quota import quota_slot
from app.core.ratelimit import limiter
from app.core.settings import settings
from app.db import models
from app.db.models import User
from app.llm.anthropic_provider import AnthropicProvider

router = APIRouter(prefix="/providers", tags=["providers"], dependencies=[Depends(require_auth)])

_HTTPS_RE = re.compile(r"^https?://", re.IGNORECASE)


def _mask(key: str) -> str:
    """密钥掩码：仅末 4 位可见（不回显明文）。

    iter 394：长度 <8 时整体掩码——此前 len>=4 即回显末 4 位，4~7 位密钥会被
    **完整回显**（与「不回显明文」的口径矛盾；api_key 下限为 1 位）。
    """
    return f"...{key[-4:]}" if len(key) >= 8 else "****"


def _out(p: models.LlmProviderProfile) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "base_url": p.base_url or "",
        "model": p.model or "",
        "kind": (p.kind or "anthropic").strip().lower(),  # iter 313：协议（NULL -> 'anthropic'）
        "is_active": bool(p.is_active),
        "api_key_masked": _mask(p.api_key),
        "created_at": str(p.created_at),
    }


def _validate_base_url(v: str) -> str:
    if v and not _HTTPS_RE.match(v):
        raise ValueError("base_url 必须以 http:// 或 https:// 开头")
    return v


def _normalize_kind(v: str | None) -> str:
    """规范化协议 kind（小写、去空白）；'' / None -> ''（存 NULL，get_provider 视作 'anthropic'）。

    仅用于**创建**（ProviderIn）：创建时未指定协议即默认 anthropic。
    """
    if v is None:
        return ""
    k = v.strip().lower()
    return k


def _normalize_kind_update(v: str | None) -> str | None:
    """更新语义（iter 394）：None（含未传）-> 保留原值；'' -> 重置为 anthropic。

    iter 394 修复：此前 ProviderUpdate 复用创建用的 _normalize_kind，None 被归一为 ''，
    而更新分支判据是 `if body.kind is not None`（归一后恒真）-> **不传 kind 也会把档案
    协议重置为 anthropic**：插件协议（iter 313 kind 扩展点）被静默改掉，与本字段
    注释「None -> 保留原值」直接矛盾。更新须保留 None 语义，故与创建用归一函数分离。
    """
    if v is None:
        return None
    return v.strip().lower()


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=1, max_length=256)
    base_url: str = Field(default="", max_length=512)
    model: str = Field(default="", max_length=128)
    kind: str = Field(default="", max_length=64)  # iter 313：协议（'' 或 'anthropic' 内置；插件协议名）

    _check_url = field_validator("base_url")(_validate_base_url)
    _norm_kind = field_validator("kind")(_normalize_kind)


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    api_key: str | None = Field(default=None, min_length=1, max_length=256)  # None -> 保留原值
    base_url: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, max_length=128)
    kind: str | None = Field(default=None, max_length=64)  # None/未传 -> 保留原值；'' -> 重置为 anthropic

    _check_url = field_validator("base_url")(_validate_base_url)
    _norm_kind = field_validator("kind")(_normalize_kind_update)


def _owned_or_404(db: Session, profile_id: int, user: User) -> models.LlmProviderProfile:
    """当前用户拥有的档案；不存在/属他人 -> 404（不泄露存在性）。"""
    p = db.get(models.LlmProviderProfile, profile_id)
    if p is None or p.user_id != user.id:
        raise ApiError("not_found", f"供应商档案 {profile_id} 不存在")
    return p


# iter 368：写操作限流（30/min）+ 每用户档案数上限（防行数无界增长拖慢
# get_provider 的 active 扫描与档案列表渲染；正常用量远达不到）
_PROVIDER_WRITE_PER_MIN = 30
_MAX_PROVIDERS_PER_USER = 50


def _check_provider_write(user: User) -> None:
    if not limiter.check(f"provider_write:{user.id}", _PROVIDER_WRITE_PER_MIN):
        raise ApiError("rate_limited", "操作过于频繁，请稍后再试", status=429)


@router.get("")
def list_providers(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    rows = (
        db.query(models.LlmProviderProfile)
        .filter_by(user_id=user.id)
        .order_by(models.LlmProviderProfile.id)
        .all()
    )
    return {"providers": [_out(p) for p in rows]}


@router.get("/kinds")
def list_provider_kinds() -> dict:
    """发现端点（v1.6.0）：列出本实例可用的 LLM 协议——内置 'anthropic' + 插件注册的协议
    （api.providers）。供前端/外部客户端动态渲染协议选择，或插件能力发现。认证同 router 级。"""
    from app.plugins import providers as plugin_providers

    return {
        # 内置协议：Anthropic Messages API（宿主原生，档案 base_url 指向其端点）
        "builtin": [{"kind": plugin_providers.KIND_ANTHROPIC, "label": "Anthropic（内置）"}],
        # 插件协议：随插件装载/卸载变化（未装插件时为空）
        "plugins": [
            {
                "kind": e.kind,
                "label": e.label or e.kind,
                "plugin_id": e.plugin_id,
                "default_model": e.default_model,
            }
            for e in plugin_providers.all_entries()
        ],
    }


@router.post("", status_code=201)
def create_provider(
    body: ProviderIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    _check_provider_write(user)
    # iter 394：配额判定是「先查后写」——并发创建可同时通过「<50 上限」检查，
    # 实际写入数按并发数突破（app/core/quota.py：单进程部署下进程内互斥）。
    with quota_slot(f"provider:{user.id}"):
        if (
            db.query(models.LlmProviderProfile).filter_by(user_id=user.id).count()
            >= _MAX_PROVIDERS_PER_USER
        ):
            raise ApiError(
                "quota_exceeded",
                f"供应商档案数量已达上限（{_MAX_PROVIDERS_PER_USER}），请先删除不再使用的档案",
            )
        dup = (
            db.query(models.LlmProviderProfile)
            .filter_by(user_id=user.id, name=body.name)
            .first()
        )
        if dup is not None:
            raise ApiError("conflict", f"供应商名称已存在: {body.name}", status=409)
        p = models.LlmProviderProfile(
            user_id=user.id,
            name=body.name,
            base_url=body.base_url,
            api_key=body.api_key,
            model=body.model,
            kind=body.kind or None,  # iter 313：'' -> NULL（视作 anthropic）
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
    _check_provider_write(user)
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
    if body.kind is not None:  # iter 313/394：不传或 None -> 保留；'' -> NULL（重置为 anthropic）
        p.kind = body.kind or None
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
    _check_provider_write(user)
    p = _owned_or_404(db, profile_id, user)
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.post("/{profile_id}/activate")
def activate_provider(
    profile_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """启用档案（同一用户内同时至多一个 active；启用后 get_provider(user_id) 走该档案）。"""
    _check_provider_write(user)
    p = _owned_or_404(db, profile_id, user)
    for other in db.query(models.LlmProviderProfile).filter_by(user_id=user.id, is_active=True).all():
        other.is_active = False
    p.is_active = True
    db.commit()
    return _out(p)


@router.post("/deactivate")
def deactivate_providers(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    """取消当前用户所有档案启用 -> get_provider 回退 .env（ANTHROPIC_API_KEY）。"""
    _check_provider_write(user)
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

    iter 394：补齐计费口径——本端点发起的是**真实计费 LLM 调用**（供应商照常计费），
    但此前既不过 check_daily_budget 也不落 api_usage（其余计费端点均有）->
    日预算对该调用失明、用量页/日统计不显示，属「计量不可绕过」原则的缺口。
    绝对金额极小（max_tokens=8、5 次/min），但口径必须与其他计费端点一致。
    """
    # iter 364：限流（LLM 计费调用，120s 上限——防用户自我消耗额度）
    if not limiter.check(f"provider_test:{user.id}", 5):
        raise ApiError("rate_limited", "测试过于频繁，请稍后再试", status=429)
    p = _owned_or_404(db, profile_id, user)
    model = p.model or settings.anthropic_model
    # iter 313：按档案 kind 选协议——插件协议分发到 PluginProvider（档案 api_key/base_url 作凭证），
    # 否则内置 AnthropicProvider。未知/未注册 kind 在此给明确测试错误（不激活、不改状态）。
    kind = (p.kind or "").strip().lower() or "anthropic"
    if kind != "anthropic":
        from app.llm.plugin_provider import PluginProvider

        from app.plugins import providers as plugin_providers

        entry = plugin_providers.find_provider(kind)
        if entry is None:
            return {"ok": False, "model": model, "error": f"协议 {kind!r} 无对应插件 Provider（未注册或插件未加载）"}
        provider = PluginProvider(entry, model=model, api_key=p.api_key, base_url=p.base_url or "")
    else:
        provider = AnthropicProvider(api_key=p.api_key, model=model, base_url=p.base_url or None)
    # iter 394：日预算守卫（err.md A6，与生成/材料/测验/幻灯片同口径）——
    # 在**发起调用前**判定；预算耗尽 -> 402，不再白跑一次计费请求。
    # 放线程执行：check_daily_budget 自开短会话做聚合查询，同步调用会占住事件循环。
    import asyncio

    await asyncio.to_thread(check_daily_budget, None, user.id)

    from app.core.concurrency import llm_task_slot

    try:
        # 显式 120s 上限（与 Timeouts.llm 一致）：SDK 默认超时 600s，慢/挂死端点
        # 会让测试请求悬挂过久
        # iter 394：补全局 LLM 背压槽（iter 368 口径）——此前本端点可绕开信号量，
        # 高负载下并发测试请求会与生成链路叠加冲击供应商限流。
        async with llm_task_slot():
            resp = await asyncio.wait_for(
                provider.chat("你是连通性测试探针。", "ping", max_tokens=8, cache_system=False),
                timeout=120.0,
            )
    except Exception as exc:  # noqa: BLE001  任何失败均作为测试结果返回（非 500）
        if isinstance(exc, asyncio.TimeoutError):
            msg = "连接超时（120s）"  # TimeoutError 的 str() 为空串，须显式给消息
        else:
            # 安全：不回显异常原文——SDK 的 APIStatusError 消息含响应正文片段，
            # 自定义 base_url 指向内网时会成为「服务器向内网发请求 + 回读响应」的探测通道。
            # 给异常类型名 + HTTP 状态码（401/404 等对用户排障足够），不含正文。
            status = getattr(exc, "status_code", None)
            msg = type(exc).__name__ + (f"（HTTP {status}）" if isinstance(status, int) else "")
        return {"ok": False, "model": model, "error": msg}
    # iter 394：用量计入 api_usage（日预算统计与用量页口径完整；计量失败不阻断业务）
    from app.agent.cost import build_metadata, record_metadata_usage
    from app.db.base import SessionLocal

    metadata = build_metadata(resp.usage, provider=getattr(provider, "kind", "anthropic"))
    udb = SessionLocal()
    try:
        # 记量含 DB 提交（fsync）——放线程，避免占住事件循环（iter 393 口径）
        await asyncio.to_thread(record_metadata_usage, udb, metadata, user.id)
    finally:
        udb.close()
    return {"ok": True, "model": model}

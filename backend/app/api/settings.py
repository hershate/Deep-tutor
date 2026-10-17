"""设置 API（plan/06 §四.7 设置页）。

GET /api/settings        只读返回当前后端配置（非敏感）：模型/预算/限额/Key 是否已设。
PUT /api/settings/prefs  用户级偏好覆盖（iter 176 参数配置）：目前支持 daily_budget_usd。
不返回任何原始密钥（plan 假设 4：API Key 本地 .env 配置，不入库/不下发）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_daily_budget, get_db, require_auth
from app.api.errors import ApiError
from app.core.settings import effective_search_engine, settings
from app.db import models
from app.db.models import User
from app.llm.pricing import PRICING_FIELDS, default_pricing_table

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_auth)])


class PrefsIn(BaseModel):
    """用户偏好覆盖（iter 176/355）。值为 None = 清除覆盖回退全局默认；0 = 不限预算。

    仅请求中显式包含的键会被更新（exclude_unset），未传键不动——避免改 A 误清 B。
    """

    daily_budget_usd: float | None = Field(default=None, ge=0, le=100000)
    llm_max_tokens: int | None = Field(default=None, ge=1, le=200000, description="LLM 单次输出 token 上限覆盖")


@router.get("")
def get_settings(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """只读配置快照（掩码：仅返回 Key 是否已设，不返回明文）。

    多模态（P3，R9）：ocr_available/whisper_available 为运行时依赖检测结果，
    False 时对应上传入口将报 dependency_missing（前端可提示安装）。
    iter 176：daily_budget_usd 为全局默认，另附用户覆盖后的有效值。
    """
    from app.tools.parse_material import ocr_available, whisper_available

    from app.core.credentials import (
        KIND_ANTHROPIC,
        KIND_AUTH_TOKEN,
        KIND_BING,
        get_active_credential,
        resolve_bing_api_key,
    )
    from app.llm import get_active_max_tokens, get_active_profile

    effective = get_daily_budget(db, user.id)
    has_override = (
        db.query(models.UserPref.id)
        .filter_by(user_id=user.id, key="daily_budget_usd")
        .first()
        is not None
    )
    # iter 355：LLM 输出上限的用户偏好（无 -> 回退默认 100k）
    max_tok_override = get_active_max_tokens(user.id)
    # iter 223：Key 生效状态纳入凭证档案（该用户 active 档案优先，回退 .env）
    anthropic_cred = get_active_credential(user.id, KIND_ANTHROPIC, db)
    if get_active_profile(user.id) is not None:
        anthropic_source = "provider_profile"
    elif anthropic_cred is not None:
        anthropic_source = "credential"
    elif settings.anthropic_key_configured:
        anthropic_source = "env"
    else:
        anthropic_source = "none"
    bing_key = resolve_bing_api_key(user.id, db)
    bing_cred = get_active_credential(user.id, KIND_BING, db)
    bing_source = "credential" if bing_cred is not None else ("env" if settings.bing_api_key else "none")
    auth_cred = get_active_credential(user.id, KIND_AUTH_TOKEN, db)
    auth_source = (
        "credential" if auth_cred is not None else ("env" if settings.auth_token_configured else "none")
    )
    return {
        "anthropic_model": settings.anthropic_model,
        "budget_per_session_usd": settings.budget_per_session_usd,
        "daily_budget_usd": settings.daily_budget_usd,
        "daily_budget_effective": effective,
        "daily_budget_is_override": has_override,
        # iter 355：LLM 输出 token 上限（默认 100k，用户可在设置页调节；防 generate_quiz 截断）
        "llm_max_tokens": max_tok_override if max_tok_override is not None else settings.llm_max_tokens,
        "llm_max_tokens_default": settings.llm_max_tokens,
        "llm_max_tokens_is_override": max_tok_override is not None,
        "max_tool_calls": settings.max_tool_calls,
        "max_upload_mb": settings.max_upload_mb,
        "search_engine": settings.search_engine,
        # iter 222：实际生效引擎（auto 三值语义 + key 配置的解析结果，供设置页展示）；
        # iter 223：key 含凭证档案解析值
        "search_engine_effective": effective_search_engine(bing_key),
        "anthropic_api_key_set": anthropic_source != "none",
        "anthropic_key_source": anthropic_source,
        "bing_api_key_set": bool(bing_key),
        "bing_key_source": bing_source,
        "auth_token_set": auth_source != "none",
        "auth_token_source": auth_source,
        "ocr_available": ocr_available(),
        "whisper_available": whisper_available(),
        "whisper_model": settings.whisper_model,
        "media_max_minutes": settings.media_max_minutes,
    }


@router.put("/prefs")
def update_prefs(
    body: PrefsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """设置/清除用户级偏好覆盖（iter 176 日预算 / iter 355 LLM 输出上限）。

    仅请求中显式包含的键（exclude_unset）被处理：值为 null -> 删除覆盖（回退全局默认）；
    为数值 -> upsert 覆盖行。未传的键不动（避免改 A 误清 B）。幂等：重复 PUT 同值一致。
    """
    unset = body.model_dump(exclude_unset=True)
    from app.llm import get_active_max_tokens  # 局部导入避循环（同 get_settings）

    def _upsert(key: str, value: object) -> None:
        row = db.query(models.UserPref).filter_by(user_id=user.id, key=key).one_or_none()
        if value is None:
            if row is not None:
                db.delete(row)
        else:
            if row is None:
                row = models.UserPref(user_id=user.id, key=key)
                db.add(row)
            row.value = repr(value)

    if "daily_budget_usd" in unset:
        _upsert("daily_budget_usd", body.daily_budget_usd)
    if "llm_max_tokens" in unset:
        _upsert("llm_max_tokens", body.llm_max_tokens)
    db.commit()
    max_tok_override = get_active_max_tokens(user.id)
    has_budget_override = (
        db.query(models.UserPref.id).filter_by(user_id=user.id, key="daily_budget_usd").first() is not None
    )
    return {
        "daily_budget_usd": get_daily_budget(db, user.id),
        "daily_budget_default": settings.daily_budget_usd,
        "daily_budget_is_override": has_budget_override,
        "llm_max_tokens": max_tok_override if max_tok_override is not None else settings.llm_max_tokens,
        "llm_max_tokens_default": settings.llm_max_tokens,
        "llm_max_tokens_is_override": max_tok_override is not None,
    }


# iter 356：用户级「模型计费表」（设置页维护，模型名 -> 4 单价字段，¥/1M tokens）。
# 替代旧的族级覆盖（iter 353）与每卡定价（iter 354）。默认表据主流国产模型公开计价，
# 可能随官方调整过时——用户可按实际账单编辑/增删。
_VALID_PRICING_FIELDS = PRICING_FIELDS  # (input/output/cache_read/cache_write)_per_mtok


def _clean_model_pricing(table: dict) -> dict:
    """清洗模型计费表输入：模型名非空 + 仅保留已知 4 数值字段（非负、有限）。

    条目缺失的字段补 0（计费表条目须 4 字段齐全，避免 resolve 时静默缺省）。
    iter 368：条目数 ≤200、模型名 ≤128 字符（防无界 JSON 落 UserPref）；
    NaN/inf 经 JSON 解析可达且 `num < 0` 判不出，须 isfinite 显式拒。
    """
    import math

    cleaned: dict = {}
    if not isinstance(table, dict):
        return cleaned
    for model, fields in table.items():
        if not isinstance(model, str) or not model.strip() or not isinstance(fields, dict):
            continue
        if len(cleaned) >= 200 or len(model) > 128:
            continue
        entry: dict = {}
        for key in _VALID_PRICING_FIELDS:
            if key in fields:
                try:
                    num = float(fields[key])
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(num):
                    continue  # NaN/inf 不入表（会毒化成本累计）
                if num < 0:
                    continue  # 单价非负
                entry[key] = num
        if entry:
            for key in _VALID_PRICING_FIELDS:
                entry.setdefault(key, 0.0)  # 缺失字段补 0
            cleaned[model.strip()] = entry
    return cleaned


class ModelPricingIn(BaseModel):
    """模型计费表（CNY / 1M tokens）。pricing 为空 -> 清空回退默认。"""

    pricing: dict = Field(default_factory=dict)
    # iter 379（R22）：可选装载基线（GET 返回的 updated_at 原样回传），不匹配 -> 409——
    # 计费表是整表读改写（设置页载入->编辑->全量 PUT），双标签后保存者静默吞掉先保存者
    # 的条目；不传保持旧行为（插件/外部调用方兼容）。秒粒度限制同笔记先例。
    expected_updated_at: str | None = Field(default=None, max_length=64)


@router.get("/pricing")
def get_pricing_settings(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """模型计费表：默认（内置国产模型参考价）+ 用户已保存的表（设置页维护，iter 356）。

    单位 CNY（人民币）/ 1M tokens；用户表精确匹配优先，未命中回退族默认。
    计费时按此表 resolve_pricing 重算 api_usage.cost（用量页 / 日预算即用户实际单价）。
    """
    from app.agent.cost import MODEL_PRICING_PREF_KEY, load_model_pricing

    row = (
        db.query(models.UserPref)
        .filter_by(user_id=user.id, key=MODEL_PRICING_PREF_KEY)
        .one_or_none()
    )
    return {
        "defaults": default_pricing_table(),
        "table": load_model_pricing(db, user.id),
        # iter 379（R22）：装载基线——全表覆盖写面的乐观并发依据（同笔记 expected_updated_at 先例）
        "updated_at": row.updated_at.isoformat() if (row is not None and row.updated_at) else None,
        "note": "单价为 CNY（人民币）/1M tokens。默认值据主流国产模型公开计价，可能随官方"
        "调整过时；建议按你的实际账单编辑。核对链接见后端 app/llm/pricing.py 模块说明。",
    }


@router.put("/pricing")
def update_pricing_settings(
    body: ModelPricingIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """设置/清空用户「模型计费表」（iter 356）。

    pricing 空对象 -> 删除该用户计费表行（回退默认）；否则清洗后 upsert model_pricing JSON。
    幂等：重复 PUT 同值结果一致。仅非空模型名与已知 4 字段（非负）被保留，缺失字段补 0。
    """
    import json

    from app.agent.cost import MODEL_PRICING_PREF_KEY, load_model_pricing

    from app.core.ratelimit import limiter

    if not limiter.check(f"pricing_write:{user.id}", 30):
        raise ApiError("rate_limited", "操作过于频繁，请稍后再试", status=429)
    cleaned = _clean_model_pricing(body.pricing)
    row = (
        db.query(models.UserPref)
        .filter_by(user_id=user.id, key=MODEL_PRICING_PREF_KEY)
        .one_or_none()
    )
    # iter 379（R22）：装载基线不匹配（含他窗口已清空删行 -> cur=None）-> 409 拒绝盲覆盖
    if body.expected_updated_at is not None:
        cur = row.updated_at.isoformat() if (row is not None and row.updated_at) else None
        if cur != body.expected_updated_at:
            raise ApiError("conflict", "计费表已在其他窗口被修改，请刷新后重试", status=409)
    if not cleaned:
        if row is not None:
            db.delete(row)
            db.commit()
        new_ts = None
    else:
        if row is None:
            row = models.UserPref(user_id=user.id, key=MODEL_PRICING_PREF_KEY)
            db.add(row)
        row.value = json.dumps(cleaned, ensure_ascii=False)
        db.commit()
        db.refresh(row)
        new_ts = row.updated_at.isoformat() if row.updated_at else None
    return {
        "defaults": default_pricing_table(),
        "table": load_model_pricing(db, user.id),
        # iter 379（R22）：保存后新基线（清空删行 -> None），前端随回读表一起采纳
        "updated_at": new_ts,
    }

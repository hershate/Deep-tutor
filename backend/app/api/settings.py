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
from app.core.settings import effective_search_engine, settings
from app.db import models
from app.db.models import User
from app.llm.pricing import default_pricing_table

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


# iter 353：用户级价目覆盖（设置页可配置）。默认价目据 Anthropic 公开价目，可能过时
# （如 sonnet-5 促销、Opus 4.5+ 降价、Haiku 4.5 提价）；用户可按实际账单覆盖每族单价。
_VALID_PRICING_FAMILIES = ("opus", "sonnet", "haiku")
_VALID_PRICING_FIELDS = (
    "input_per_mtok",
    "output_per_mtok",
    "cache_read_per_mtok",
    "cache_write_per_mtok",
)


def _clean_pricing_overrides(pricing: dict) -> dict:
    """清洗价目覆盖输入：只保留已知族 + 已知数值字段（防注入 / 非法值）。"""
    cleaned: dict = {}
    if not isinstance(pricing, dict):
        return cleaned
    for family, fields in pricing.items():
        if family not in _VALID_PRICING_FAMILIES or not isinstance(fields, dict):
            continue
        fam_clean: dict = {}
        for key, val in fields.items():
            if key in _VALID_PRICING_FIELDS:
                try:
                    num = float(val)
                except (TypeError, ValueError):
                    continue
                if num < 0:
                    continue  # 单价非负
                fam_clean[key] = num
        if fam_clean:
            cleaned[family] = fam_clean
    return cleaned


class PricingOverridesIn(BaseModel):
    """价目覆盖（USD / 1M tokens）。pricing 为空 -> 清除全部覆盖回退默认。"""

    pricing: dict = Field(default_factory=dict)


@router.get("/pricing")
def get_pricing_settings(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """价目表：默认（内置）+ 用户覆盖（设置页可配置，iter 353）。

    单位 USD / 1M tokens；覆盖优先于默认。计费时按用户覆盖重算 api_usage.cost
    （用量页 / 日预算即用户实际单价）。
    """
    from app.agent.cost import load_pricing_overrides

    return {
        "defaults": default_pricing_table(),
        "overrides": load_pricing_overrides(db, user.id),
        "note": "单价为 USD/1M tokens。默认值据 Anthropic 公开价目，可能随官方调整过时；"
        "建议按你的实际账单核对后覆盖。核对：https://www.anthropic.com/pricing",
    }


@router.put("/pricing")
def update_pricing_settings(
    body: PricingOverridesIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """设置/清除用户级价目覆盖（iter 353）。

    pricing 空对象 -> 删除覆盖行（回退默认）；否则清洗后 upsert pricing_overrides JSON。
    幂等：重复 PUT 同值结果一致。仅已知族（opus/sonnet/haiku）与已知字段被保留。
    """
    import json

    from app.agent.cost import load_pricing_overrides

    cleaned = _clean_pricing_overrides(body.pricing)
    row = (
        db.query(models.UserPref)
        .filter_by(user_id=user.id, key="pricing_overrides")
        .one_or_none()
    )
    if not cleaned:
        if row is not None:
            db.delete(row)
            db.commit()
    else:
        if row is None:
            row = models.UserPref(user_id=user.id, key="pricing_overrides")
            db.add(row)
        row.value = json.dumps(cleaned, ensure_ascii=False)
        db.commit()
    return {
        "defaults": default_pricing_table(),
        "overrides": load_pricing_overrides(db, user.id),
    }

"""设置 API（plan/06 §四.7 设置页）。

GET /api/settings        只读返回当前后端配置（非敏感）：模型/预算/限额/Key 是否已设。
PUT /api/settings/prefs  用户级偏好覆盖（iter 171 参数配置）：目前支持 daily_budget_usd。
不返回任何原始密钥（plan 假设 4：API Key 本地 .env 配置，不入库/不下发）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_daily_budget, get_db, require_auth
from app.core.settings import settings
from app.db import models
from app.db.models import User

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_auth)])


class PrefsIn(BaseModel):
    """用户偏好覆盖（iter 171）。值为 None = 清除覆盖回退全局默认；0 = 不限预算。"""

    daily_budget_usd: float | None = Field(default=None, ge=0, le=100000)


@router.get("")
def get_settings(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> dict:
    """只读配置快照（掩码：仅返回 Key 是否已设，不返回明文）。

    多模态（P3，R9）：ocr_available/whisper_available 为运行时依赖检测结果，
    False 时对应上传入口将报 dependency_missing（前端可提示安装）。
    iter 171：daily_budget_usd 为全局默认，另附用户覆盖后的有效值。
    """
    from app.tools.parse_material import ocr_available, whisper_available

    effective = get_daily_budget(db, user.id)
    has_override = (
        db.query(models.UserPref.id)
        .filter_by(user_id=user.id, key="daily_budget_usd")
        .first()
        is not None
    )
    return {
        "anthropic_model": settings.anthropic_model,
        "budget_per_session_usd": settings.budget_per_session_usd,
        "daily_budget_usd": settings.daily_budget_usd,
        "daily_budget_effective": effective,
        "daily_budget_is_override": has_override,
        "max_tool_calls": settings.max_tool_calls,
        "max_upload_mb": settings.max_upload_mb,
        "search_engine": settings.search_engine,
        "anthropic_api_key_set": settings.anthropic_key_configured,
        "bing_api_key_set": bool(settings.bing_api_key),
        "auth_token_set": settings.auth_token_configured,
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
    """设置/清除用户级日预算覆盖（iter 171）。

    daily_budget_usd 为 null -> 删除覆盖（回退全局默认）；为数值 -> upsert 覆盖行。
    幂等：重复 PUT 同值结果一致。
    """
    row = (
        db.query(models.UserPref)
        .filter_by(user_id=user.id, key="daily_budget_usd")
        .one_or_none()
    )
    if body.daily_budget_usd is None:
        if row is not None:
            db.delete(row)
            db.commit()
    else:
        if row is None:
            row = models.UserPref(user_id=user.id, key="daily_budget_usd")
            db.add(row)
        row.value = repr(body.daily_budget_usd)
        db.commit()
    return {
        "daily_budget_usd": get_daily_budget(db, user.id),
        "daily_budget_default": settings.daily_budget_usd,
        "daily_budget_is_override": body.daily_budget_usd is not None,
    }

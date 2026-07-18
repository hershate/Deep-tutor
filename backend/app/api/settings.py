"""设置 API（plan/06 §四.7 设置页）。

GET /api/settings  只读返回当前后端配置（非敏感）：模型/预算/限额/Key 是否已设。
不返回任何原始密钥（plan 假设 4：API Key 本地 .env 配置，不入库/不下发）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import require_auth
from app.core.settings import settings

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_auth)])


@router.get("")
def get_settings() -> dict:
    """只读配置快照（掩码：仅返回 Key 是否已设，不返回明文）。

    多模态（P3，R9）：ocr_available/whisper_available 为运行时依赖检测结果，
    False 时对应上传入口将报 dependency_missing（前端可提示安装）。
    """
    from app.tools.parse_material import ocr_available, whisper_available

    return {
        "anthropic_model": settings.anthropic_model,
        "budget_per_session_usd": settings.budget_per_session_usd,
        "daily_budget_usd": settings.daily_budget_usd,
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

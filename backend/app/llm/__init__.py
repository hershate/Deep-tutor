"""LLM Provider 抽象层（plan/01 §三、plan/04 P0 LLM 与 Agent 核心）。

- LLMProvider 抽象接口（app/llm/base.py）
- Anthropic 实现（app/llm/anthropic_provider.py）：缓存优先 + 成本计量
- 计价表（app/llm/pricing.py）
- get_provider() 工厂：按 settings 构造（缺失 key 抛错）
"""

from __future__ import annotations

from app.core.settings import settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMProvider, LLMResponse, LLMUsage

__all__ = ["LLMProvider", "LLMResponse", "LLMUsage", "AnthropicProvider", "get_provider"]


def get_active_profile() -> "dict | None":
    """读 DB 中 is_active 的供应商档案（iter 50 多档案切换）；无 -> None。

    DB 读取失败（如表不存在/连接异常）-> None 回退 .env，不阻断调用方。
    """
    try:
        from app.db import models
        from app.db.base import SessionLocal

        db = SessionLocal()
        try:
            p = db.query(models.LlmProviderProfile).filter_by(is_active=True).first()
            if p is None:
                return None
            return {"name": p.name, "base_url": p.base_url or "", "api_key": p.api_key, "model": p.model or ""}
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return None


def get_provider() -> LLMProvider:
    """构造 LLM Provider：DB active 供应商档案优先（iter 50），否则 .env（ANTHROPIC_API_KEY）。

    档案的 base_url/model 为空时分别回退官方端点 / settings.anthropic_model。
    两者皆无 -> 抛 RuntimeError（功能级必填，见 settings.validate_feature_keys）。
    """
    profile = get_active_profile()
    if profile is not None:
        return AnthropicProvider(
            api_key=profile["api_key"],
            model=profile["model"] or settings.anthropic_model,
            base_url=profile["base_url"] or None,
        )
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 未配置且无生效供应商档案，无法构造 LLM Provider")
    return AnthropicProvider(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
    )

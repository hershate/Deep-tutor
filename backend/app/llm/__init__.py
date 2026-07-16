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


def get_provider() -> LLMProvider:
    """按 settings 构造 LLM Provider。

    缺失 ANTHROPIC_API_KEY 时抛 RuntimeError（功能级必填，见 settings.validate_feature_keys）。
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 未配置，无法构造 LLM Provider")
    return AnthropicProvider(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
    )

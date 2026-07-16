"""LLM Provider 抽象接口。

设计要点（plan/01 §三 三支柱）：
- chat() 返回 LLMResponse（content + usage + stop_reason），usage 含 cache 指标（Pillar 1/3）。
- cache_system：把 system 作为稳定前缀并标记 cache_control（缓存优先，跨调用复用）。
- 具体 Provider（Anthropic）在 step 2 实现；后续可扩展 DeepSeek 等。
"""

from __future__ import annotations

from pydantic import BaseModel


class LLMUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    model: str = ""


class LLMResponse(BaseModel):
    content: str
    usage: LLMUsage
    stop_reason: str | None = None


class LLMProvider:
    """LLM Provider 抽象基类。"""

    async def chat(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        cache_system: bool = True,
    ) -> LLMResponse:
        raise NotImplementedError

    def estimate_cost(self, usage: LLMUsage) -> float:
        """按模型计价估算费用（USD）。子类按模型价目表覆盖。"""
        return 0.0

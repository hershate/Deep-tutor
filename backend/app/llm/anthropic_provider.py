"""Anthropic Claude Provider 实现（plan/01 §三 三支柱）。

- 缓存优先（Pillar 1）：system 作为稳定前缀，cache_system=True 时打 cache_control(ephemeral)。
- 成本计量（Pillar 3）：解析 usage（input/output/cache_read/cache_creation）填 LLMUsage，
  estimate_cost 按 app/llm/pricing.py 计价。
- 后续 DeepSeek 等 Provider 实现 LLMProvider 接口即可接入。
"""

from __future__ import annotations

from anthropic import AsyncAnthropic
from anthropic.types import Message

from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.llm.pricing import estimate_cost


class AnthropicProvider(LLMProvider):
    def __init__(
        self, api_key: str, model: str, *, base_url: str | None = None, client: AsyncAnthropic | None = None
    ) -> None:
        self._model = model
        # 允许注入 client（测试用）；否则按 api_key 创建（base_url 支持自定义 Anthropic 兼容端点，iter 50）
        self._client = client or AsyncAnthropic(api_key=api_key, base_url=base_url or None)

    async def chat(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        cache_system: bool = True,
    ) -> LLMResponse:
        used_model = model or self._model
        system_param = self._build_system(system, cache_system)
        resp: Message = await self._client.messages.create(
            model=used_model,
            max_tokens=max_tokens,
            system=system_param,
            messages=[{"role": "user", "content": user}],
        )
        return self._to_response(resp, used_model)

    @staticmethod
    def _build_system(system: str, cache_system: bool):
        """构造 system 参数。空 system 返回 None；cache_system 时打 cache_control。"""
        if not system:
            return None
        if cache_system:
            return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        return system

    @staticmethod
    def _to_response(resp: Message, model: str) -> LLMResponse:
        # 拼接所有 text 块（忽略 tool_use 等非文本块；工具调用在 step 3 的 Agent 循环另处理）
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        u = resp.usage
        usage = LLMUsage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_input_tokens=u.cache_read_input_tokens or 0,
            cache_creation_input_tokens=u.cache_creation_input_tokens or 0,
            model=model,
        )
        return LLMResponse(content=text, usage=usage, stop_reason=resp.stop_reason)

    def estimate_cost(self, usage: LLMUsage) -> float:
        return estimate_cost(usage, usage.model or self._model)

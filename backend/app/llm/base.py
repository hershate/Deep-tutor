"""LLM Provider 抽象接口。

设计要点（plan/01 §三 三支柱）：
- chat() 返回 LLMResponse（content + usage + stop_reason），usage 含 cache 指标（Pillar 1/3）。
- cache_system：把 system 作为稳定前缀并标记 cache_control（缓存优先，跨调用复用）。
- 具体 Provider（Anthropic）在 step 2 实现；后续可扩展 DeepSeek 等。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

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


class TransientLLMError(Exception):
    """LLM API 瞬态错误（限流/连接失败/5xx/overloaded），可退避重试（err.md B1）。

    由具体 Provider 把 SDK 的瞬态异常映射为本类型；agent 层据此做指数退避，
    非瞬态错误（4xx 请求错误等）不包装、直接抛出。
    """


@dataclass
class StreamChunk:
    """流式补全的一个分片：text（文本增量）或 usage（最终用量，流的末尾发一次）。

    api.llm.stream 消费：text 分片 yield 给插件，usage 分片用于成本计量。
    """

    text: str = ""
    usage: LLMUsage | None = None


# ---------------------------------------------------------------------------
# 记忆系统（iter 345）：原生 tool_use 多轮 Agent 能力（chat_agentic / stream_agentic）
# 与单轮 chat/stream 并存（additive，既有调用零影响）。
# ---------------------------------------------------------------------------


@dataclass
class ToolDefinition:
    """Agent 可调用工具定义（直传 Anthropic tools[].input_schema）。"""

    name: str
    description: str
    input_schema: dict  # JSON schema dict


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict


@dataclass
class ThinkingBlock:
    text: str


@dataclass
class AgenticResponse:
    """chat_agentic 返回：content 为有序块列表（保 text/thinking/tool_use 交织），
    stop_reason=="tool_use" 表示需执行工具并回灌 tool_result 后续循环。"""

    content: list  # list[TextBlock | ToolUseBlock | ThinkingBlock]
    usage: LLMUsage
    stop_reason: str | None = None


@dataclass
class AgenticStreamChunk:
    """stream_agentic 流式分片：文本增量 / 思考增量 / 工具调用开始 / 工具入参增量 /
    最终用量+stop_reason（流的末尾发一次，usage 非空）。"""

    text: str = ""
    thinking: str = ""
    tool_use_start: tuple[str, str] | None = None  # (tool_use_id, name)
    tool_use_delta: tuple[str, str] | None = None  # (tool_use_id, partial_input_json)
    usage: LLMUsage | None = None
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

    async def stream(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        cache_system: bool = True,
    ) -> AsyncIterator[StreamChunk]:
        """流式补全（v1.14.0）。默认实现：非流式 chat 后一次性 yield 全文 + usage
        （子类如 AnthropicProvider 覆写为真流式——逐 token yield + 末尾 usage）。"""
        resp = await self.chat(system, user, model=model, max_tokens=max_tokens, cache_system=cache_system)
        yield StreamChunk(text=resp.content, usage=resp.usage)

    # --- 原生 tool_use 多轮（iter 345 记忆系统）-----------------------------
    # messages：原生 Anthropic 消息形态 list[dict]（caller 须把 tool_result 按原样回灌）。
    # 默认 NotImplementedError——仅 AnthropicProvider 实现；PluginProvider 暂不支持
    # （agent loop 检测并在 plugin provider 上拒绝启动 agentic run）。
    async def chat_agentic(
        self,
        messages: list[dict],
        *,
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        cache_system: bool = True,
        tool_choice: dict | None = None,
        thinking: bool = False,
    ) -> AgenticResponse:
        raise NotImplementedError

    async def stream_agentic(
        self,
        messages: list[dict],
        *,
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        cache_system: bool = True,
        tool_choice: dict | None = None,
        thinking: bool = False,
    ) -> AsyncIterator[AgenticStreamChunk]:
        raise NotImplementedError
        yield  # 令本方法成为异步生成器（iterating 时抛 NotImplementedError；子类覆写真实现）

    def estimate_cost(self, usage: LLMUsage) -> float:
        """按模型计价估算费用（USD）。子类按模型价目表覆盖。"""
        return 0.0

"""Anthropic Claude Provider 实现（plan/01 §三 三支柱）。

- 缓存优先（Pillar 1）：system 作为稳定前缀，cache_system=True 时打 cache_control(ephemeral)。
- 成本计量（Pillar 3）：解析 usage（input/output/cache_read/cache_creation）填 LLMUsage，
  estimate_cost 按 app/llm/pricing.py 计价。
- 后续 DeepSeek 等 Provider 实现 LLMProvider 接口即可接入。
"""

from __future__ import annotations

import asyncio

import anthropic
from anthropic import AsyncAnthropic
from anthropic.types import Message

from app.llm.base import (
    AgenticResponse,
    AgenticStreamChunk,
    LLMProvider,
    LLMResponse,
    LLMUsage,
    StreamChunk,
    TextBlock,
    ThinkingBlock,
    ToolDefinition,
    ToolUseBlock,
    TransientLLMError,
)
from app.llm.pricing import estimate_cost

# iter 243：AsyncAnthropic 进程内复用——此前每次 get_provider()（每次生成/测评/
# 供应商连通性测试）新建 client 且从不 aclose；每个 client 一个 httpx 连接池 +
# SSL 上下文，长期运行 fd/内存缓涨。按 (api_key, base_url) 有界缓存共享；
# 记录创建时事件循环，跨循环使用（测试逐用例 asyncio.run 新循环）自动重建，
# 防 "Event loop is closed"。FIFO 有界淘汰（淘汰条目由 GC 回收连接池）。
_CLIENT_CACHE_MAX = 8
_clients: dict[tuple[str, str | None], tuple[AsyncAnthropic, "asyncio.AbstractEventLoop | None"]] = {}


def _shared_client(api_key: str, base_url: str | None) -> AsyncAnthropic:
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    key = (api_key, base_url)
    hit = _clients.get(key)
    if hit is not None and hit[1] is loop:
        return hit[0]
    if key not in _clients and len(_clients) >= _CLIENT_CACHE_MAX:
        _clients.pop(next(iter(_clients)))  # FIFO 淘汰最旧
    client = AsyncAnthropic(api_key=api_key, base_url=base_url)
    _clients[key] = (client, loop)
    return client


def reset_client_cache() -> None:
    """测试隔离（conftest isolate）/ 需要强制重建时调用。"""
    _clients.clear()


def _map_transient(exc: Exception) -> Exception:
    """把 anthropic SDK 的瞬态异常映射为 TransientLLMError（err.md B1）；其余原样返回。"""
    if isinstance(exc, (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError)):
        return TransientLLMError(str(exc))
    # 529 overloaded 等 5xx 状态错误
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return TransientLLMError(str(exc))
    return exc


class AnthropicProvider(LLMProvider):
    def __init__(
        self, api_key: str, model: str, *, base_url: str | None = None, client: AsyncAnthropic | None = None
    ) -> None:
        self._model = model
        # 允许注入 client（测试用）；否则共享缓存 client（iter 243：防连接池累积泄漏）
        self._client = client or _shared_client(api_key, base_url or None)

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
        try:
            resp: Message = await self._client.messages.create(
                model=used_model,
                max_tokens=max_tokens,
                system=system_param,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIError as exc:
            mapped = _map_transient(exc)
            raise mapped from exc
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

    async def stream(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        cache_system: bool = True,
    ):
        """真流式补全（v1.14.0）：raw event stream → text_delta 逐块 yield，message_start/
        message_delta 摄取 input/output usage，末尾 yield 完整 usage 供计量。"""
        used_model = model or self._model
        system_param = self._build_system(system, cache_system)
        input_tokens = 0
        output_tokens = 0
        cache_read = 0
        cache_creation = 0
        try:
            event_stream = await self._client.messages.create(
                model=used_model,
                max_tokens=max_tokens,
                system=system_param,
                messages=[{"role": "user", "content": user}],
                stream=True,
            )
            async for event in event_stream:
                et = event.type
                if et == "message_start":
                    u = event.message.usage
                    input_tokens = u.input_tokens
                    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
                    cache_creation = getattr(u, "cache_creation_input_tokens", 0) or 0
                elif et == "content_block_delta" and getattr(event.delta, "type", None) == "text_delta":
                    yield StreamChunk(text=event.delta.text)
                elif et == "message_delta":
                    u = event.usage
                    output_tokens = getattr(u, "output_tokens", 0) or 0
        except anthropic.APIError as exc:
            raise _map_transient(exc) from exc
        yield StreamChunk(usage=LLMUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_creation,
            model=used_model,
        ))

    # ------------------------------------------------------------------
    # 原生 tool_use 多轮（iter 345 记忆系统 / Agent 循环）
    # ------------------------------------------------------------------
    @staticmethod
    def _tool_def(t: ToolDefinition) -> dict:
        return {"name": t.name, "description": t.description, "input_schema": t.input_schema}

    @staticmethod
    def _thinking_budget(max_tokens: int) -> int:
        """thinking budget_tokens 须 < max_tokens；至少 1024，上限 8000。"""
        return max(1024, min(8000, max_tokens - 1024))

    def _agentic_kwargs(
        self,
        messages: list[dict],
        *,
        system: str,
        tools: list[ToolDefinition] | None,
        model: str | None,
        max_tokens: int,
        cache_system: bool,
        tool_choice: dict | None,
        thinking: bool,
    ) -> tuple[str, dict]:
        used_model = model or self._model
        system_param = self._build_system(system, cache_system)
        kwargs: dict = {"model": used_model, "max_tokens": max_tokens, "messages": messages}
        if system_param is not None:
            kwargs["system"] = system_param
        if tools:
            kwargs["tools"] = [self._tool_def(t) for t in tools]
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        if thinking:
            # thinking 须 max_tokens >= 2048（budget 至少 1024 且 < max_tokens）
            if max_tokens < 2048:
                kwargs["max_tokens"] = 2048
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": self._thinking_budget(kwargs["max_tokens"])}
        return used_model, kwargs

    @staticmethod
    def _to_agentic_response(resp: Message, model: str) -> AgenticResponse:
        """把含 text/thinking/tool_use 块的响应映射为 AgenticResponse（保交织顺序）。

        与单轮 _to_response 分离——后者拼接纯文本（工具调用在 Agent 循环另处理）。
        """
        blocks: list = []
        for block in resp.content:
            bt = getattr(block, "type", None)
            if bt == "text":
                blocks.append(TextBlock(text=block.text))
            elif bt == "tool_use":
                raw_input = block.input
                blocks.append(
                    ToolUseBlock(
                        id=block.id,
                        name=block.name,
                        input=dict(raw_input) if isinstance(raw_input, dict) else raw_input,
                    )
                )
            elif bt == "thinking":
                blocks.append(ThinkingBlock(text=getattr(block, "thinking", "") or ""))
        u = resp.usage
        usage = LLMUsage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_input_tokens=u.cache_read_input_tokens or 0,
            cache_creation_input_tokens=u.cache_creation_input_tokens or 0,
            model=model,
        )
        return AgenticResponse(content=blocks, usage=usage, stop_reason=resp.stop_reason)

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
        used_model, kwargs = self._agentic_kwargs(
            messages,
            system=system,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            cache_system=cache_system,
            tool_choice=tool_choice,
            thinking=thinking,
        )
        try:
            resp: Message = await self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            raise _map_transient(exc) from exc
        return self._to_agentic_response(resp, used_model)

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
    ):
        """原生 tool_use 流式：text_delta / thinking_delta / tool_use_start /
        input_json_delta 逐块 yield，message_start/message_delta 摄取 usage + stop_reason，
        末尾 yield 完整 usage 供计量。"""
        used_model, kwargs = self._agentic_kwargs(
            messages,
            system=system,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            cache_system=cache_system,
            tool_choice=tool_choice,
            thinking=thinking,
        )
        input_tokens = output_tokens = cache_read = cache_creation = 0
        stop_reason: str | None = None
        index_to_id: dict[int, str] = {}  # content_block index -> tool_use_id（input_json_delta 归属）
        try:
            event_stream = await self._client.messages.create(stream=True, **kwargs)
            async for event in event_stream:
                et = event.type
                if et == "message_start":
                    u = event.message.usage
                    input_tokens = u.input_tokens
                    cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
                    cache_creation = getattr(u, "cache_creation_input_tokens", 0) or 0
                elif et == "content_block_start":
                    cb = event.content_block
                    if getattr(cb, "type", None) == "tool_use":
                        idx = getattr(event, "index", None)
                        if idx is not None:
                            index_to_id[idx] = cb.id
                        yield AgenticStreamChunk(tool_use_start=(cb.id, cb.name))
                elif et == "content_block_delta":
                    dt = getattr(event.delta, "type", None)
                    if dt == "text_delta":
                        yield AgenticStreamChunk(text=event.delta.text)
                    elif dt == "input_json_delta":
                        idx = getattr(event, "index", None)
                        tid = index_to_id.get(idx, str(idx))
                        yield AgenticStreamChunk(tool_use_delta=(tid, event.delta.partial_json))
                    elif dt == "thinking_delta":
                        yield AgenticStreamChunk(thinking=getattr(event.delta, "thinking", "") or "")
                elif et == "message_delta":
                    u = event.usage
                    output_tokens = getattr(u, "output_tokens", 0) or 0
                    stop_reason = getattr(event.delta, "stop_reason", None) or stop_reason
        except anthropic.APIError as exc:
            raise _map_transient(exc) from exc
        yield AgenticStreamChunk(
            usage=LLMUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_creation,
                model=used_model,
            ),
            stop_reason=stop_reason,
        )

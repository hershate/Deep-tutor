"""LLM 层单元测试（mock Anthropic 响应，不联网）。

覆盖：usage 解析、文本拼接、cache_control 标记、空 system、成本估算、get_provider 守卫。
"""

from __future__ import annotations

import asyncio

import pytest

from app.llm import get_provider
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import (
    AgenticStreamChunk,
    LLMUsage,
    TextBlock,
    ToolDefinition,
    ToolUseBlock,
)
from app.llm.pricing import estimate_cost, get_pricing


# --- fakes ---


class FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class FakeToolUseBlock:
    def __init__(self, id: str, name: str, inp: dict) -> None:
        self.type = "tool_use"
        self.id = id
        self.name = name
        self.input = inp


class FakeThinkingBlock:
    def __init__(self, text: str) -> None:
        self.type = "thinking"
        self.thinking = text


class FakeStreamMessages:
    """流式 fake：create(stream=True) 返回事件的异步迭代器；否则返回 message。"""

    def __init__(self, events, message=None) -> None:
        self._events = events
        self._message = message
        self.last_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        if kwargs.get("stream"):
            return self._aiter()
        return self._message

    async def _aiter(self):
        for e in self._events:
            yield e


class FakeStreamClient:
    def __init__(self, events, message=None) -> None:
        self.messages = FakeStreamMessages(events, message)


class FakeUsage:
    def __init__(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_input_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class FakeMessage:
    def __init__(self, content, usage, stop_reason="end_turn", model="claude-sonnet-5"):
        self.content = content
        self.usage = usage
        self.stop_reason = stop_reason
        self.model = model


class FakeMessages:
    def __init__(self, message: FakeMessage) -> None:
        self._message = message
        self.last_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._message


class FakeClient:
    def __init__(self, message: FakeMessage) -> None:
        self.messages = FakeMessages(message)


def _provider(message: FakeMessage) -> AnthropicProvider:
    return AnthropicProvider(api_key="fake", model="claude-sonnet-5", client=FakeClient(message))


# --- tests ---


def test_chat_parses_usage_and_text():
    msg = FakeMessage(
        [FakeBlock("hello "), FakeBlock("world")],
        FakeUsage(input_tokens=100, output_tokens=50, cache_read_input_tokens=80, cache_creation_input_tokens=20),
    )
    p = _provider(msg)
    resp = asyncio.run(p.chat("sys", "usr"))
    assert resp.content == "hello world"
    assert resp.usage.input_tokens == 100
    assert resp.usage.output_tokens == 50
    assert resp.usage.cache_read_input_tokens == 80
    assert resp.usage.cache_creation_input_tokens == 20
    assert resp.stop_reason == "end_turn"


def test_cache_system_true_marks_cache_control():
    p = _provider(FakeMessage([FakeBlock("ok")], FakeUsage()))
    asyncio.run(p.chat("SYS-PROMPT", "u", cache_system=True))
    assert p._client.messages.last_kwargs["system"] == [
        {"type": "text", "text": "SYS-PROMPT", "cache_control": {"type": "ephemeral"}}
    ]


def test_cache_system_false_passes_plain_string():
    p = _provider(FakeMessage([FakeBlock("ok")], FakeUsage()))
    asyncio.run(p.chat("SYS-PROMPT", "u", cache_system=False))
    assert p._client.messages.last_kwargs["system"] == "SYS-PROMPT"


def test_empty_system_passes_none():
    p = _provider(FakeMessage([FakeBlock("ok")], FakeUsage()))
    asyncio.run(p.chat("", "u"))
    assert p._client.messages.last_kwargs["system"] is None


def test_cost_sonnet_tier():
    u = LLMUsage(input_tokens=1_000_000, output_tokens=1_000_000, model="claude-sonnet-5")
    # iter 356 族默认改 ¥：21.6 (input) + 108 (output) = 129.6 CNY per 1M each
    assert estimate_cost(u) == 129.6


def test_cost_includes_cache():
    u = LLMUsage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
        cache_creation_input_tokens=1_000_000,
        model="claude-sonnet-5",
    )
    # iter 356 ¥：21.6 + 108 + 2.16 + 27 = 158.76
    assert estimate_cost(u) == pytest.approx(158.76)


def test_unknown_model_cost_zero():
    u = LLMUsage(input_tokens=1_000_000, model="some-unknown-model")
    assert estimate_cost(u) == 0.0


def test_pricing_prefix_match():
    assert get_pricing("claude-sonnet-5") is not None
    assert get_pricing("claude-opus-4-xxx") is not None
    assert get_pricing("gpt-4") is None


def test_get_provider_raises_without_key(monkeypatch):
    from app.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "")
    with pytest.raises(RuntimeError):
        get_provider()


# --- iter 345：原生 tool_use 多轮（chat_agentic / stream_agentic）---


def test_chat_agentic_parses_text_and_tool_use():
    msg = FakeMessage(
        [FakeBlock("先搜一下"), FakeToolUseBlock("tu_1", "web_search", {"query": "x"})],
        FakeUsage(input_tokens=10, output_tokens=5),
        stop_reason="tool_use",
    )
    p = _provider(msg)
    resp = asyncio.run(
        p.chat_agentic(
            [{"role": "user", "content": "hi"}],
            system="sys",
            tools=[ToolDefinition("web_search", "s", {"type": "object"})],
        )
    )
    assert resp.stop_reason == "tool_use"
    assert len(resp.content) == 2
    assert isinstance(resp.content[0], TextBlock) and resp.content[0].text == "先搜一下"
    tu = resp.content[1]
    assert isinstance(tu, ToolUseBlock) and tu.id == "tu_1" and tu.name == "web_search" and tu.input == {"query": "x"}
    assert resp.usage.input_tokens == 10 and resp.usage.output_tokens == 5
    kw = p._client.messages.last_kwargs
    assert kw["tools"] == [{"name": "web_search", "description": "s", "input_schema": {"type": "object"}}]
    assert kw["messages"] == [{"role": "user", "content": "hi"}]
    # system 走 cache_control（cache_system 默认 True）
    assert kw["system"] == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]


def test_chat_agentic_passes_tool_choice_and_thinking():
    p = _provider(FakeMessage([FakeBlock("ok")], FakeUsage()))
    asyncio.run(
        p.chat_agentic(
            [{"role": "user", "content": "x"}],
            tools=[ToolDefinition("t", "d", {})],
            tool_choice={"type": "auto"},
            thinking=True,
        )
    )
    kw = p._client.messages.last_kwargs
    assert kw["tool_choice"] == {"type": "auto"}
    assert kw["thinking"]["type"] == "enabled"
    assert kw["thinking"]["budget_tokens"] < kw["max_tokens"]


def test_stream_agentic_yields_text_tool_thinking_usage():
    from types import SimpleNamespace as NS

    events = [
        NS(type="message_start", message=NS(usage=NS(input_tokens=12, cache_read_input_tokens=0, cache_creation_input_tokens=0))),
        NS(type="content_block_start", index=0, content_block=NS(type="tool_use", id="tu_9", name="web_fetch")),
        NS(type="content_block_delta", index=0, delta=NS(type="input_json_delta", partial_json='{"url":"a"')),
        NS(type="content_block_delta", index=1, delta=NS(type="thinking_delta", thinking="hmm")),
        NS(type="content_block_delta", index=2, delta=NS(type="text_delta", text="hello ")),
        NS(type="content_block_delta", index=2, delta=NS(type="text_delta", text="world")),
        NS(type="message_delta", usage=NS(output_tokens=7), delta=NS(stop_reason="tool_use")),
    ]
    p = AnthropicProvider(api_key="fake", model="claude-sonnet-5", client=FakeStreamClient(events))
    chunks: list[AgenticStreamChunk] = []

    async def go():
        async for c in p.stream_agentic([{"role": "user", "content": "hi"}], system="sys"):
            chunks.append(c)

    asyncio.run(go())
    assert "".join(c.text for c in chunks if c.text) == "hello world"
    assert "".join(c.thinking for c in chunks if c.thinking) == "hmm"
    assert [c.tool_use_start for c in chunks if c.tool_use_start] == [("tu_9", "web_fetch")]
    assert [c.tool_use_delta for c in chunks if c.tool_use_delta] == [("tu_9", '{"url":"a"')]
    final = [c for c in chunks if c.usage]
    assert final and final[-1].usage.input_tokens == 12 and final[-1].usage.output_tokens == 7
    assert final[-1].stop_reason == "tool_use"


def test_base_provider_agentic_not_implemented():
    """基类与 PluginProvider（继承基类不覆写）应抛 NotImplementedError——agent loop 据此拒绝。"""
    from app.llm.base import LLMProvider
    from app.llm.plugin_provider import PluginProvider

    class Bare(LLMProvider):
        pass

    with pytest.raises(NotImplementedError):
        asyncio.run(Bare().chat_agentic([]))
    # stream_agentic 是异步生成器——迭代时抛
    async def drain():
        async for _ in Bare().stream_agentic([]):
            pass

    with pytest.raises(NotImplementedError):
        asyncio.run(drain())
    # PluginProvider 继承基类未覆写 agentic 方法 -> 同一函数对象（phase 1 拒绝 agentic run）
    assert PluginProvider.chat_agentic is LLMProvider.chat_agentic
    assert PluginProvider.stream_agentic is LLMProvider.stream_agentic


# --- iter 50：get_provider 多档案切换（DB active 档案优先，回退 .env）---


def _insert_profile(name="自定义", base_url="https://relay.example.com", api_key="sk-profile", model="", is_active=True):
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        p = models.LlmProviderProfile(
            name=name, base_url=base_url, api_key=api_key, model=model, is_active=is_active
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        return p.id
    finally:
        db.close()


def test_get_provider_prefers_active_profile(monkeypatch):
    """active 档案优先：key/base_url 取档案；model 空 -> settings.anthropic_model。"""
    from app.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "sk-env")
    monkeypatch.setattr(settings_mod.settings, "anthropic_model", "claude-sonnet-5")
    _insert_profile()
    p = get_provider()
    assert p._client.api_key == "sk-profile"
    assert "relay.example.com" in str(p._client.base_url)
    assert p._model == "claude-sonnet-5"  # 档案 model 空 -> 回退 settings


def test_get_provider_profile_model_override():
    """档案 model 非空 -> 覆盖 settings 默认模型。"""
    _insert_profile(model="claude-opus-4-8")
    p = get_provider()
    assert p._model == "claude-opus-4-8"


def test_get_provider_inactive_profile_falls_back_to_env(monkeypatch):
    """无 active 档案 -> .env 路径（行为同前）。"""
    from app.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "sk-env")
    _insert_profile(is_active=False)
    p = get_provider()
    assert p._client.api_key == "sk-env"


def test_get_active_profile_db_failure_returns_none(monkeypatch):
    """DB 读取异常 -> None（回退 .env，不阻断）。"""
    import app.llm as llm_mod
    from app.db import base as db_base

    monkeypatch.setattr(db_base, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    # SessionLocal 在函数内局部导入，需 patch 源头模块
    assert llm_mod.get_active_profile() is None

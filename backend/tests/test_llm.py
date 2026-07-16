"""LLM 层单元测试（mock Anthropic 响应，不联网）。

覆盖：usage 解析、文本拼接、cache_control 标记、空 system、成本估算、get_provider 守卫。
"""

from __future__ import annotations

import asyncio

import pytest

from app.llm import get_provider
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMUsage
from app.llm.pricing import estimate_cost, get_pricing


# --- fakes ---


class FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


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
    # 3 (input) + 15 (output) = 18 USD per 1M each
    assert estimate_cost(u) == 18.0


def test_cost_includes_cache():
    u = LLMUsage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
        cache_creation_input_tokens=1_000_000,
        model="claude-sonnet-5",
    )
    # 3 + 15 + 0.30 + 3.75 = 22.05
    assert estimate_cost(u) == pytest.approx(22.05)


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

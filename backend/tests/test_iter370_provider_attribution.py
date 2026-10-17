"""iter 370（B20③）：api_usage.provider 计量归属闭合回归。

此前全链 12 处 record_metadata_usage 调用无一传 provider、_persist_tool_calls 硬编码，
插件协议用户（OpenAI 兼容/Ollama 等）的用量行 100% 错标 anthropic。
通道：LLMProvider.kind（PluginProvider=插件协议名）→ build_metadata(provider=) →
ToolMetadata.provider → record_metadata_usage/_persist_tool_calls 落行。
"""

from __future__ import annotations

from app.agent.cost import build_metadata, record_metadata_usage
from app.db import models
from app.db.base import SessionLocal
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMProvider, LLMUsage
from app.llm.plugin_provider import PluginProvider
from app.plugins import providers as provider_reg
from app.plugins.providers import ProviderEntry
from app.tools.base import ToolOutput


def test_provider_kind_attributes():
    assert LLMProvider.kind == "anthropic"  # 基类缺省
    assert AnthropicProvider(api_key="k", model="m").kind == "anthropic"
    entry = ProviderEntry(plugin_id="p1", kind="openai_compat", handler=None, default_model="gpt-x")
    assert PluginProvider(entry, model="m").kind == "openai_compat"  # 实例 kind=插件协议名


def test_build_metadata_carries_provider():
    usage = LLMUsage(input_tokens=10, output_tokens=5, model="m")
    assert build_metadata(usage).provider == "anthropic"  # 缺省
    assert build_metadata(usage, provider="openai_compat").provider == "openai_compat"


def test_record_metadata_usage_writes_metadata_provider():
    db = SessionLocal()
    try:
        u = models.User(name="u", username="prov-attr", password_hash="x")
        db.add(u)
        db.commit()
        meta = build_metadata(LLMUsage(input_tokens=7, output_tokens=3, model="m"), provider="ollama")
        record_metadata_usage(db, meta, u.id)
        row = db.query(models.ApiUsage).filter_by(user_id=u.id).one()
        assert row.provider == "ollama"  # metadata 归属优先，不再恒 anthropic
        db.rollback()
    finally:
        db.close()


def test_persist_tool_calls_uses_metadata_provider():
    """主生成管线落账点（plans._persist_tool_calls）按元数据归属写 provider。"""
    from app.api.plans import _persist_tool_calls

    db = SessionLocal()
    try:
        u = models.User(name="u", username="prov-attr2", password_hash="x")
        db.add(u)
        db.flush()
        sess = models.Session(user_id=u.id, title="s")
        db.add(sess)
        db.commit()
        out = ToolOutput(
            tool="t1",
            status="ok",
            data={},
            metadata=build_metadata(LLMUsage(input_tokens=5, output_tokens=2, model="m"), provider="openai_compat"),
        )
        _persist_tool_calls(db, sess.id, "tr", executed=[out])
        row = db.query(models.ApiUsage).filter_by(user_id=u.id).one()
        assert row.provider == "openai_compat"
        db.rollback()
    finally:
        db.close()


def test_plugin_provider_registry_roundtrip():
    """注册表往返：entry.kind 即 PluginProvider.kind 来源（热启停后归属不漂）。"""

    async def _handler(system, prompt, **kw):  # noqa: ARG001
        return None

    try:
        provider_reg.register(ProviderEntry(plugin_id="p1", kind="prov_x", handler=_handler, default_model="mx"))
        entry = provider_reg.find_provider("prov_x")
        assert entry is not None and PluginProvider(entry, model="m").kind == "prov_x"
    finally:
        provider_reg.reset()

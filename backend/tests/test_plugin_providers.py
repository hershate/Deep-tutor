"""后端插件 API v1.6.0 LLM Provider 扩展点测试（API.md「§LLM Provider 扩展点」）。

覆盖：
- 注册表语义：normalize_kind / valid_kind_name / register+find / 'anthropic' 内置保留拒绝 /
  同 kind 后注册覆盖（warn）/ unregister / reset / all_entries 排序
- PluginProvider：async handler / sync handler(线程池) / 返回契约校验（缺字段/类型错）/
  usage cache 令牌缺省 0 / stop_reason 可选 / 瞬态分类（transient 标记 / 网络异常名 / 其余原样）/
  estimate_cost（已知模型族>0 / 未知 0）
- 门面 api.providers：register+kinds / 拒绝 'anthropic' / 拒绝非法名 / 回滚清理
- api.llm.complete(provider_kind=)：成功+计量 api_usage / 未知 kind no_llm_provider(503) /
  handler 瞬态 server_busy(503) / handler 其他错误 bad_gateway(502) / 预算 budget_exceeded(402) /
  max_tokens 收敛 + system/prompt/model 透传 handler
- 发现端点：GET /providers/kinds（内置 anthropic + 插件协议）
- 状态可观测：PluginStatus.providers + 装载回滚
"""

from __future__ import annotations

import asyncio

import pytest

from app.db.base import SessionLocal
from app.plugins.api import BackendPluginAPI
from app.plugins.providers import ProviderEntry
from app.plugins.runtime import PluginStatus


# --- 通用辅助 ---


def _facade(plugin_id: str = "provtest") -> BackendPluginAPI:
    from fastapi import FastAPI

    status = PluginStatus(id=plugin_id, name=plugin_id, version="", file="", state="loaded")
    return BackendPluginAPI(app=FastAPI(), plugin_id=plugin_id, data_dir="/tmp", status=status)


def _facade_with_status(plugin_id: str = "provtest"):
    """返回 (api, status)：需断言 status.providers 的用例用此（门面不保留 status 引用）。"""
    from fastapi import FastAPI

    status = PluginStatus(id=plugin_id, name=plugin_id, version="", file="", state="loaded")
    api = BackendPluginAPI(app=FastAPI(), plugin_id=plugin_id, data_dir="/tmp", status=status)
    return api, status


def _no_budget_block(monkeypatch):
    monkeypatch.setattr("app.api.deps.check_daily_budget", lambda db, user_id: None)


def _good_handler(content: str = "OPENAI-REPLY", *, exc: Exception | None = None, model: str = "gpt-test", record_creds: bool = False):
    """返回合规 dict 的 handler（async）；记录入参；可选抛异常。

    record_creds=True 时把 api_key/base_url 也记入 seen（profile 凭证传递验证用）。
    """

    async def h(system, user, *, model: str = model, max_tokens: int = 0, api_key: str = "", base_url: str = ""):  # noqa: B008
        seen: dict = {"system": system, "user": user, "model": model, "max_tokens": max_tokens}
        if record_creds:
            seen["api_key"] = api_key
            seen["base_url"] = base_url
        h.seen = seen  # type: ignore[attr-defined]
        if exc is not None:
            raise exc
        return {
            "content": content,
            "model": model,
            "stop_reason": "stop",
            "usage": {"input_tokens": 13, "output_tokens": 9},
        }

    h.seen = None  # type: ignore[attr-defined]
    return h


# --- 注册表语义 ---


def test_normalize_kind():
    from app.plugins.providers import normalize_kind

    assert normalize_kind("OpenAI") == "openai"
    assert normalize_kind(" Ollama ") == "ollama"
    with pytest.raises(ValueError):
        normalize_kind("")


def test_valid_kind_name():
    from app.plugins.providers import valid_kind_name

    assert valid_kind_name("openai")
    assert valid_kind_name("deepseek-chat")
    assert not valid_kind_name("Anthropic")  # 大写非法（normalize 后 'anthropic' 是保留名）
    assert not valid_kind_name("bad name")  # 含空格
    assert not valid_kind_name("")  # 空


def test_register_and_find():
    from app.plugins import providers

    e = ProviderEntry(plugin_id="p1", kind="openai", handler=lambda *a, **k: {})
    providers.register(e)
    assert providers.find_provider("openai") is e
    assert providers.find_provider("OPENAI") is e  # 大小写不敏感
    assert providers.find_provider("nope") is None
    providers.reset()


def test_anthropic_kind_reserved():
    """'anthropic' 为内置保留协议——register 直接拒绝。"""
    from app.plugins import providers

    e = ProviderEntry(plugin_id="p1", kind="anthropic", handler=lambda *a, **k: {})
    with pytest.raises(ValueError, match="内置保留"):
        providers.register(e)
    assert providers.find_provider("anthropic") is None  # 内置处理，注册表不收
    providers.reset()


def test_register_overrides_with_warn(caplog):
    from app.plugins import providers

    e1 = ProviderEntry(plugin_id="p1", kind="openai", handler=lambda *a, **k: {})
    e2 = ProviderEntry(plugin_id="p2", kind="openai", handler=lambda *a, **k: {})
    providers.register(e1)
    with caplog.at_level("WARNING"):
        providers.register(e2)
    assert providers.find_provider("openai") is e2  # 后者覆盖
    assert any("覆盖既有注册" in r.message for r in caplog.records)
    providers.reset()


def test_unregister_and_reset():
    from app.plugins import providers

    e = ProviderEntry(plugin_id="p1", kind="ollama", handler=lambda *a, **k: {})
    providers.register(e)
    providers.unregister(e)
    assert providers.find_provider("ollama") is None
    providers.register(e)
    providers.reset()
    assert providers.all_entries() == []


def test_all_entries_sorted():
    from app.plugins import providers

    providers.register(ProviderEntry(plugin_id="p", kind="zeta", handler=lambda *a, **k: {}))
    providers.register(ProviderEntry(plugin_id="p", kind="alpha", handler=lambda *a, **k: {}))
    kinds = [e.kind for e in providers.all_entries()]
    assert kinds == ["alpha", "zeta"]
    providers.reset()


# --- PluginProvider 包装器 ---


def test_plugin_provider_async_handler():
    from app.llm.plugin_provider import PluginProvider

    h = _good_handler("hello")
    entry = ProviderEntry(plugin_id="p", kind="openai", handler=h, default_model="gpt-test")
    resp = asyncio.run(PluginProvider(entry, model="gpt-test").chat("S", "U", max_tokens=100))
    assert resp.content == "hello"
    assert resp.usage.input_tokens == 13 and resp.usage.output_tokens == 9
    assert resp.usage.model == "gpt-test"
    assert resp.stop_reason == "stop"
    assert h.seen == {"system": "S", "user": "U", "model": "gpt-test", "max_tokens": 100}


def test_plugin_provider_sync_handler_runs_in_thread():
    """sync handler 经线程池执行（不阻塞事件循环），返回映射正确。"""
    from app.llm.plugin_provider import PluginProvider

    def sync_h(system, user, *, model, max_tokens, api_key="", base_url=""):
        sync_h.seen = (system, user, model, max_tokens)  # type: ignore[attr-defined]
        return {"content": "sync-reply", "model": model, "usage": {"input_tokens": 1, "output_tokens": 2}}

    sync_h.seen = None  # type: ignore[attr-defined]
    entry = ProviderEntry(plugin_id="p", kind="ollama", handler=sync_h)
    resp = asyncio.run(PluginProvider(entry, model="llama3").chat("s", "u", max_tokens=50))
    assert resp.content == "sync-reply"
    assert resp.usage.output_tokens == 2
    assert sync_h.seen == ("s", "u", "llama3", 50)  # type: ignore[attr-defined]


def test_plugin_provider_contract_validation():
    from app.llm.plugin_provider import PluginProvider, PluginProviderError

    async def bad_content(system, user, *, model, max_tokens, api_key="", base_url=""):
        return {"content": 123, "model": "m", "usage": {"input_tokens": 1, "output_tokens": 1}}

    async def missing_usage(system, user, *, model, max_tokens, api_key="", base_url=""):
        return {"content": "x", "model": "m"}

    async def bad_int(system, user, *, model, max_tokens, api_key="", base_url=""):
        return {"content": "x", "model": "m", "usage": {"input_tokens": "many", "output_tokens": 1}}

    for h in (bad_content, missing_usage, bad_int):
        e = ProviderEntry(plugin_id="p", kind="openai", handler=h)
        with pytest.raises(PluginProviderError):
            asyncio.run(PluginProvider(e, model="m").chat("", "u"))


def test_plugin_provider_usage_cache_defaults_zero():
    from app.llm.plugin_provider import PluginProvider

    async def h(system, user, *, model, max_tokens, api_key="", base_url=""):
        return {"content": "x", "model": model, "usage": {"input_tokens": 5, "output_tokens": 5}}

    entry = ProviderEntry(plugin_id="p", kind="openai", handler=h)
    resp = asyncio.run(PluginProvider(entry, model="m").chat("", "u"))
    assert resp.usage.cache_read_input_tokens == 0
    assert resp.usage.cache_creation_input_tokens == 0


def test_plugin_provider_transient_classification():
    from app.llm.base import TransientLLMError
    from app.llm.plugin_provider import _classify

    # 约定：异常上设 transient=True -> 瞬态
    e1 = ValueError("rate limited")
    e1.transient = True  # type: ignore[attr-defined]
    assert isinstance(_classify(e1), TransientLLMError)
    # 网络瞬态类型名（无硬依赖：用同名异常模拟）-> 瞬态
    ConnectErr = type("ConnectError", (Exception,), {})
    assert isinstance(_classify(ConnectErr("refused")), TransientLLMError)
    # 其余原样（非瞬态）
    other = ValueError("bad request")
    assert _classify(other) is other


def test_plugin_provider_estimate_cost_known_and_unknown():
    from app.llm.plugin_provider import PluginProvider
    from app.llm.base import LLMUsage

    entry = ProviderEntry(plugin_id="p", kind="openai", handler=lambda *a, **k: {})
    # 已知族名（claude-sonnet-5）命中价目表 -> >0
    known = PluginProvider(entry, model="claude-sonnet-5")
    assert known.estimate_cost(LLMUsage(input_tokens=10, output_tokens=10, model="claude-sonnet-5")) > 0
    # 未知模型 -> 0（用户用量页可见、不计费）
    unknown = PluginProvider(entry, model="gpt-unknown-xyz")
    assert unknown.estimate_cost(LLMUsage(input_tokens=10, output_tokens=10, model="gpt-unknown-xyz")) == 0


# --- 门面 api.providers ---


def test_facade_register_and_kinds():
    api = _facade()
    h = _good_handler()
    api.providers.register("OpenAI", h, default_model="gpt-4o", label="OpenAI 兼容")
    assert api.providers.kinds() == ["openai"]  # 规范化为小写
    assert _facade("other").providers.kinds() == []  # 另一插件 id 视角隔离


def test_facade_register_rejects_anthropic_and_bad_name():
    api = _facade()
    with pytest.raises(ValueError, match="内置保留"):
        api.providers.register("anthropic", _good_handler())
    with pytest.raises(ValueError, match="非法"):
        api.providers.register("bad name!", _good_handler())
    with pytest.raises(TypeError):
        api.providers.register("openai", "not-callable")  # type: ignore[arg-type]


def test_facade_register_rollback_clears_registry():
    api = _facade()
    from app.plugins import providers

    api.providers.register("openai", _good_handler())
    assert providers.find_provider("openai") is not None
    api._rollback()  # 模拟 loader 在 register 中途失败时逆序回滚
    assert providers.find_provider("openai") is None  # 注册表已清（status.providers 为尽力观测，回滚不追溯）


# --- api.llm.complete(provider_kind=) ---


def test_complete_with_provider_kind_success(monkeypatch):
    _no_budget_block(monkeypatch)
    h = _good_handler("总结完成")
    api = _facade()
    api.providers.register("openai", h, default_model="gpt-4o")
    resp = asyncio.run(api.llm.complete(1, "材料", system="你是助教", max_tokens=300, provider_kind="openai"))
    assert resp["content"] == "总结完成"
    assert resp["usage"]["input_tokens"] == 13
    assert h.seen == {"system": "你是助教", "user": "材料", "model": "gpt-4o", "max_tokens": 300}


def test_complete_with_provider_kind_records_usage(monkeypatch):
    """插件 Provider 用量同样落 api_usage（日预算/用量页口径完整）。"""
    _no_budget_block(monkeypatch)
    from app.db.models import ApiUsage, User

    db = SessionLocal()
    try:
        db.add(User(name="u", username="up", password_hash="", is_admin=False))
        db.commit()
        uid = db.query(User).first().id
    finally:
        db.close()
    api = _facade()
    api.providers.register("openai", _good_handler(model="gpt-test"))
    asyncio.run(api.llm.complete(uid, "hi", provider_kind="openai"))
    db = SessionLocal()
    try:
        rows = db.query(ApiUsage).all()
        assert len(rows) == 1
        assert rows[0].user_id == uid
        assert rows[0].tokens_in == 13 and rows[0].tokens_out == 9
    finally:
        db.close()


def test_complete_with_provider_kind_clamps_and_passes(monkeypatch):
    _no_budget_block(monkeypatch)
    h = _good_handler()
    api = _facade()
    api.providers.register("openai", h, default_model="gpt-4o")
    asyncio.run(api.llm.complete(1, "x", max_tokens=999_999, provider_kind="openai"))
    assert h.seen["max_tokens"] == 8192  # 上限收敛透传给 handler


def test_complete_unknown_kind_is_no_provider(monkeypatch):
    from app.api.errors import ApiError

    _no_budget_block(monkeypatch)
    with pytest.raises(ApiError) as ei:
        asyncio.run(_facade().llm.complete(1, "x", provider_kind="nope"))
    assert ei.value.code == "no_llm_provider" and ei.value.status == 503


def test_complete_provider_kind_transient_is_server_busy(monkeypatch):
    from app.api.errors import ApiError
    from app.llm.base import TransientLLMError

    _no_budget_block(monkeypatch)
    api = _facade()

    async def bad(system, user, *, model, max_tokens, api_key="", base_url=""):
        raise TransientLLMError("upstream 429")

    api.providers.register("openai", bad)
    with pytest.raises(ApiError) as ei:
        asyncio.run(api.llm.complete(1, "x", provider_kind="openai"))
    assert ei.value.code == "server_busy" and ei.value.status == 503


def test_complete_provider_kind_other_error_is_bad_gateway(monkeypatch):
    from app.api.errors import ApiError

    _no_budget_block(monkeypatch)
    api = _facade()

    async def wrong(system, user, *, model, max_tokens, api_key="", base_url=""):
        raise ValueError("malformed upstream response")

    api.providers.register("openai", wrong)
    with pytest.raises(ApiError) as ei:
        asyncio.run(api.llm.complete(1, "x", provider_kind="openai"))
    assert ei.value.code == "bad_gateway" and ei.value.status == 502


def test_complete_provider_kind_respects_budget(monkeypatch):
    from app.api.errors import ApiError

    api = _facade()
    api.providers.register("openai", _good_handler())

    def _block(db, user_id):
        raise ApiError("budget_exceeded", "日预算已用尽")

    monkeypatch.setattr("app.api.deps.check_daily_budget", _block)
    with pytest.raises(ApiError) as ei:
        asyncio.run(api.llm.complete(1, "x", provider_kind="openai"))
    assert ei.value.code == "budget_exceeded" and ei.value.status == 402


# --- 发现端点 ---


def test_discovery_endpoint_lists_builtin_and_plugins(client, auth_headers):
    from app.plugins import providers

    providers.register(
        ProviderEntry(plugin_id="myplug", kind="openai", handler=lambda *a, **k: {}, label="OpenAI", default_model="gpt-4o")
    )
    try:
        r = client.get("/api/providers/kinds", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["builtin"] == [{"kind": "anthropic", "label": "Anthropic（内置）"}]
        assert body["plugins"] == [
            {"kind": "openai", "label": "OpenAI", "plugin_id": "myplug", "default_model": "gpt-4o"}
        ]
    finally:
        providers.reset()


def test_discovery_endpoint_empty_when_no_plugins(client, auth_headers):
    r = client.get("/api/providers/kinds", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["builtin"] and body["plugins"] == []


# --- get_provider 分发（iter 313 profile 集成）---


def _patch_active_profile(monkeypatch, *, kind: str, api_key: str = "sk-x", base_url: str = "", model: str = ""):
    """让 app.llm.get_active_profile 返回指定 kind 的档案（绕过 DB）。"""
    monkeypatch.setattr(
        "app.llm.get_active_profile",
        lambda user_id=None, db=None: {"name": "p", "base_url": base_url, "api_key": api_key, "model": model, "kind": kind},
    )


def test_get_provider_anthropic_when_kind_empty(monkeypatch):
    """kind=None/空 -> AnthropicProvider（既有档案零行为变化）。"""
    from app.llm import get_provider
    from app.llm.anthropic_provider import AnthropicProvider

    for k in ("", "   ", "anthropic", "Anthropic"):
        _patch_active_profile(monkeypatch, kind=k)
        assert isinstance(get_provider(1), AnthropicProvider)


def test_get_provider_dispatches_to_plugin_provider(monkeypatch):
    """档案 kind=插件协议 -> get_provider 返回 PluginProvider（方案生成主线即用新协议）。"""
    from app.llm import get_provider
    from app.llm.plugin_provider import PluginProvider

    api = _facade()
    h = _good_handler()
    api.providers.register("openai", h, default_model="gpt-4o")
    _patch_active_profile(monkeypatch, kind="openai", model="gpt-4o")
    p = get_provider(1)
    assert isinstance(p, PluginProvider)
    # 调用一次确认分发后可正常 chat
    resp = asyncio.run(p.chat("s", "u", max_tokens=10))
    assert resp.content == "OPENAI-REPLY"


def test_get_provider_passes_profile_creds_to_handler(monkeypatch):
    """档案 api_key/base_url 经 PluginProvider 传入 handler（profile 统一管凭证）。"""
    from app.llm import get_provider

    api = _facade()
    h = _good_handler(record_creds=True)
    api.providers.register("openai", h, default_model="gpt-4o")
    _patch_active_profile(monkeypatch, kind="openai", api_key="sk-profile-key", base_url="https://api.openai.com", model="gpt-4o")
    p = get_provider(1)
    asyncio.run(p.chat("", "u", max_tokens=4))
    assert h.seen["api_key"] == "sk-profile-key"
    assert h.seen["base_url"] == "https://api.openai.com"


def test_get_provider_unknown_plugin_kind_raises(monkeypatch):
    """档案 kind 指向未注册的插件协议 -> RuntimeError（明确错误，不静默回退 anthropic）。"""
    from app.llm import get_provider

    _patch_active_profile(monkeypatch, kind="deepseek")  # 未注册
    with pytest.raises(RuntimeError, match="无对应插件 Provider"):
        get_provider(1)


# --- 状态可观测 ---


def test_status_records_providers_after_register():
    api, status = _facade_with_status()
    api.providers.register("openai", _good_handler())
    api.providers.register("ollama", _good_handler())
    assert status.providers == ["openai", "ollama"]

"""后端插件 API v1.5.0 LLM 能力扩展点测试（API.md「§LLM 能力」）。

覆盖：
- is_configured：无供应商 False / 有供应商 True（与 complete 解析口径一致）
- complete：成功返回 content/model/stop_reason/usage；prompt/system/max_tokens 透传；
  max_tokens 收敛 [1, 8192]；入参校验（user_id/prompt/system）
- 错误映射：无配置 no_llm_provider(503) / 日预算 budget_exceeded(402) /
  瞬态 server_busy(503) / 其他 bad_gateway(502)
- 计量：api_usage 落行（tokens/model/cost，与宿主端点同口径）
- 门面接线：BackendPluginAPI.llm 存在 + version 1.5.0
"""

from __future__ import annotations

import asyncio

import pytest

from app.db.base import SessionLocal
from app.llm.base import LLMProvider, LLMResponse, LLMUsage, TransientLLMError
from app.plugins.api import BackendPluginAPI
from app.plugins.runtime import PluginStatus


# --- 通用辅助 ---


class _StubProvider(LLMProvider):
    """罐头 provider：记录入参，返回固定响应（模型名取真实族名以命中计价表）。"""

    def __init__(self, content: str = "STUB-REPLY", exc: Exception | None = None):
        self._content = content
        self._exc = exc
        self.seen_system: str | None = None
        self.seen_user: str | None = None
        self.seen_max_tokens: int | None = None

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        self.seen_system = system
        self.seen_user = user
        self.seen_max_tokens = max_tokens
        if self._exc is not None:
            raise self._exc
        return LLMResponse(
            content=self._content,
            usage=LLMUsage(input_tokens=11, output_tokens=7, model="claude-sonnet-5"),
            stop_reason="end_turn",
        )


def _facade(plugin_id: str = "llmtest") -> BackendPluginAPI:
    """直接构造门面（RoutesNamespace 仅需一个 FastAPI 实例，不挂载任何路由）。"""
    from fastapi import FastAPI

    status = PluginStatus(id=plugin_id, name=plugin_id, version="", file="", state="loaded")
    return BackendPluginAPI(app=FastAPI(), plugin_id=plugin_id, data_dir="/tmp", status=status)


def _patch_provider(monkeypatch, provider):
    monkeypatch.setattr("app.llm.get_provider", lambda user_id=None: provider)


def _patch_no_provider(monkeypatch):
    def _raise(user_id=None):
        raise RuntimeError("ANTHROPIC_API_KEY 未配置且无生效供应商/Key 档案")

    monkeypatch.setattr("app.llm.get_provider", _raise)


def _no_budget_block(monkeypatch):
    """默认日预算守卫放行（多数用例不关心预算）。"""
    monkeypatch.setattr("app.api.deps.check_daily_budget", lambda db, user_id: None)


# --- is_configured ---


def test_is_configured_false_without_provider(monkeypatch):
    _patch_no_provider(monkeypatch)
    assert _facade().llm.is_configured(1) is False


def test_is_configured_true_with_provider(monkeypatch):
    _patch_provider(monkeypatch, _StubProvider())
    assert _facade().llm.is_configured(1) is True


# --- complete 成功路径 ---


def test_complete_returns_contract_dict(monkeypatch):
    stub = _StubProvider("总结结果")
    _patch_provider(monkeypatch, stub)
    _no_budget_block(monkeypatch)
    resp = asyncio.run(_facade().llm.complete(1, "帮我总结这段材料", system="你是助教", max_tokens=512))
    assert resp["content"] == "总结结果"
    assert resp["model"] == "claude-sonnet-5"
    assert resp["stop_reason"] == "end_turn"
    assert resp["usage"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    # 入参透传给 provider（system/prompt/max_tokens）
    assert stub.seen_system == "你是助教"
    assert stub.seen_user == "帮我总结这段材料"
    assert stub.seen_max_tokens == 512


def test_complete_records_usage(monkeypatch):
    """用量落 api_usage（日预算/用量页口径完整，与宿主端点同规）。"""
    _patch_provider(monkeypatch, _StubProvider())
    _no_budget_block(monkeypatch)
    # api_usage.user_id 有外键——先落一个真实用户（同 test_plugins 模式）
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="u1", username="u1", password_hash="", is_admin=False))
        db.commit()
        uid = db.query(User).first().id
    finally:
        db.close()
    asyncio.run(_facade().llm.complete(uid, "hello"))
    db = SessionLocal()
    try:
        from app.db.models import ApiUsage

        rows = db.query(ApiUsage).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.user_id == uid
        assert row.model == "claude-sonnet-5"
        assert row.tokens_in == 11 and row.tokens_out == 7
        assert row.cost > 0  # 计价经 build_metadata(estimate_cost)，sonnet 族命中价目表
    finally:
        db.close()


def test_complete_clamps_max_tokens(monkeypatch):
    stub = _StubProvider()
    _patch_provider(monkeypatch, stub)
    _no_budget_block(monkeypatch)
    api = _facade().llm
    asyncio.run(api.complete(1, "x", max_tokens=999_999))
    assert stub.seen_max_tokens == 8192  # 上限收敛
    asyncio.run(api.complete(1, "x", max_tokens=0))
    assert stub.seen_max_tokens == 1  # 下限收敛


def test_complete_validates_args(monkeypatch):
    _patch_provider(monkeypatch, _StubProvider())
    _no_budget_block(monkeypatch)
    api = _facade().llm
    with pytest.raises(ValueError, match="user_id"):
        asyncio.run(api.complete(0, "x"))
    with pytest.raises(ValueError, match="prompt"):
        asyncio.run(api.complete(1, "   "))
    with pytest.raises(TypeError, match="system"):
        asyncio.run(api.complete(1, "x", system=None))


# --- 错误映射 ---


def test_complete_no_provider_is_503(monkeypatch):
    from app.api.errors import ApiError

    _patch_no_provider(monkeypatch)
    _no_budget_block(monkeypatch)
    with pytest.raises(ApiError) as ei:
        asyncio.run(_facade().llm.complete(1, "x"))
    assert ei.value.code == "no_llm_provider"
    assert ei.value.status == 503


def test_complete_budget_exceeded_402(monkeypatch):
    from app.api.errors import ApiError

    _patch_provider(monkeypatch, _StubProvider())

    def _block(db, user_id):
        raise ApiError("budget_exceeded", "日预算已用尽")

    monkeypatch.setattr("app.api.deps.check_daily_budget", _block)
    with pytest.raises(ApiError) as ei:
        asyncio.run(_facade().llm.complete(1, "x"))
    assert ei.value.code == "budget_exceeded"
    assert ei.value.status == 402


def test_complete_transient_is_server_busy(monkeypatch):
    from app.api.errors import ApiError

    _patch_provider(monkeypatch, _StubProvider(exc=TransientLLMError("rate limit")))
    _no_budget_block(monkeypatch)
    with pytest.raises(ApiError) as ei:
        asyncio.run(_facade().llm.complete(1, "x"))
    assert ei.value.code == "server_busy"
    assert ei.value.status == 503


def test_complete_other_error_is_bad_gateway(monkeypatch):
    from app.api.errors import ApiError

    _patch_provider(monkeypatch, _StubProvider(exc=ValueError("bad prompt shape")))
    _no_budget_block(monkeypatch)
    with pytest.raises(ApiError) as ei:
        asyncio.run(_facade().llm.complete(1, "x"))
    assert ei.value.code == "bad_gateway"
    assert ei.value.status == 502


# --- 门面接线 ---


def test_facade_exposes_llm_and_version():
    api = _facade()
    assert api.llm is not None
    assert api.version == "1.7.0"
    assert callable(api.llm.is_configured)
    assert asyncio.iscoroutinefunction(api.llm.complete)

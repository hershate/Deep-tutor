"""后端插件 API v1.7.0 api.tools.call 工具调用测试（API.md「§工具注册与调用」）。

覆盖：
- Python 工具调用（插件注册）：ok + data + 入参透传
- 核心工具调用（parse_material kind=text）：插件可调用宿主核心工具（"新工作流"组合性）
- LLM 工具调用（inline_prompt + output_model）：ok + data（解析自 provider JSON）+ api_usage 计量
- 错误：未知工具 ValueError / Python 工具异常 -> 信封 status=error（不抛）/ 入参校验
- LLM 工具：无 provider -> ApiError(no_llm_provider) / 预算超限 -> ApiError(budget_exceeded)
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from app.db.base import SessionLocal
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.plugins.api import BackendPluginAPI
from app.plugins.runtime import PluginStatus


# --- 通用辅助 ---


def _facade(plugin_id: str = "tooltest") -> BackendPluginAPI:
    from fastapi import FastAPI

    status = PluginStatus(id=plugin_id, name=plugin_id, version="", file="", state="loaded")
    return BackendPluginAPI(app=FastAPI(), plugin_id=plugin_id, data_dir="/tmp", status=status)


def _no_budget_block(monkeypatch):
    monkeypatch.setattr("app.api.deps.check_daily_budget", lambda db, user_id: None)


def _make_user() -> int:
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="u", username="utool", password_hash="", is_admin=False))
        db.commit()
        return db.query(User).first().id
    finally:
        db.close()


class _JsonProvider(LLMProvider):
    """返回固定 JSON 文本（供 LLM 工具 _parse_and_validate 解析）。"""

    def __init__(self, content: str = '{"summary": "AI 摘要"}'):
        self._content = content

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        return LLMResponse(content=self._content, usage=LLMUsage(input_tokens=20, output_tokens=5, model="claude-sonnet-5"))


# --- Python 工具调用 ---


def test_call_python_tool_returns_data(monkeypatch):
    _no_budget_block(monkeypatch)
    uid = _make_user()
    api = _facade()

    async def echo(inp, ctx):
        return {"echo": inp.args.get("msg"), "sid": ctx.session_id}

    api.tools.register_python("tooltest.echo", echo)
    res = asyncio.run(api.tools.call("tooltest.echo", {"msg": "hi"}, user_id=uid))
    assert res["status"] == "ok"
    assert res["data"]["echo"] == "hi"
    assert res["data"]["sid"].startswith("plugin_call:tooltest:")  # 可追溯 session_id


def test_call_core_tool_parse_material(monkeypatch):
    """插件可调用宿主核心工具（parse_material kind=text）——'新工作流'组合性。"""
    _no_budget_block(monkeypatch)
    uid = _make_user()
    res = asyncio.run(
        _facade().tools.call("parse_material", {"kind": "text", "source": "一段学习材料"}, user_id=uid)
    )
    assert res["status"] == "ok"
    assert res["data"]["text"] == "一段学习材料"
    assert res["data"]["meta"]["source_type"] == "text"


# --- LLM 工具调用 ---


class _SummaryOut(BaseModel):
    summary: str


def test_call_llm_tool_parses_and_meters(monkeypatch):
    """LLM 工具：provider 返回 JSON -> 解析校验 -> data；用量落 api_usage（计量）。"""
    monkeypatch.setattr("app.llm.get_provider", lambda user_id=None: _JsonProvider())
    _no_budget_block(monkeypatch)
    uid = _make_user()
    api = _facade()
    from app.agent.registry import Tool

    api.tools.register(Tool(name="tooltest.summarize", llm=True, inline_prompt="总结", output_model=_SummaryOut))
    res = asyncio.run(api.tools.call("tooltest.summarize", {"text": "材料"}, user_id=uid))
    assert res["status"] == "ok"
    assert res["data"]["summary"] == "AI 摘要"

    # 计量：api_usage 落行（user_id/model/tokens）
    from app.db.models import ApiUsage

    db = SessionLocal()
    try:
        rows = db.query(ApiUsage).all()
        assert len(rows) == 1
        assert rows[0].user_id == uid
        assert rows[0].tokens_in == 20 and rows[0].tokens_out == 5
    finally:
        db.close()


def test_call_llm_tool_no_provider_is_503(monkeypatch):
    from app.api.errors import ApiError

    def _raise(user_id=None):
        raise RuntimeError("未配置")

    monkeypatch.setattr("app.llm.get_provider", _raise)
    _no_budget_block(monkeypatch)
    api = _facade()
    from app.agent.registry import Tool

    api.tools.register(Tool(name="tooltest.summarize", llm=True, inline_prompt="x", output_model=_SummaryOut))
    with pytest.raises(ApiError) as ei:
        asyncio.run(api.tools.call("tooltest.summarize", {"text": "x"}, user_id=1))
    assert ei.value.code == "no_llm_provider" and ei.value.status == 503


def test_call_llm_tool_budget_exceeded(monkeypatch):
    from app.api.errors import ApiError

    monkeypatch.setattr("app.llm.get_provider", lambda user_id=None: _JsonProvider())

    def _block(db, user_id):
        raise ApiError("budget_exceeded", "日预算已用尽")

    monkeypatch.setattr("app.api.deps.check_daily_budget", _block)
    api = _facade()
    from app.agent.registry import Tool

    api.tools.register(Tool(name="tooltest.summarize", llm=True, inline_prompt="x", output_model=_SummaryOut))
    with pytest.raises(ApiError) as ei:
        asyncio.run(api.tools.call("tooltest.summarize", {"text": "x"}, user_id=1))
    assert ei.value.code == "budget_exceeded" and ei.value.status == 402


# --- 错误信封与入参校验 ---


def test_call_unknown_tool_raises(monkeypatch):
    _no_budget_block(monkeypatch)
    with pytest.raises(ValueError, match="未注册的工具"):
        asyncio.run(_facade().tools.call("nope", {}, user_id=1))


def test_call_python_tool_error_is_envelope(monkeypatch):
    """Python 工具抛异常 -> 信封 status=error（不向上抛，交插件处理）。"""
    _no_budget_block(monkeypatch)
    uid = _make_user()
    api = _facade()

    async def boom(inp, ctx):
        raise RuntimeError("炸了")

    api.tools.register_python("tooltest.boom", boom)
    res = asyncio.run(api.tools.call("tooltest.boom", {}, user_id=uid))
    assert res["status"] == "error"
    assert res["data"] == {}
    assert any("炸了" in e["message"] for e in res["errors"])


def test_call_validates_args(monkeypatch):
    _no_budget_block(monkeypatch)
    api = _facade()
    with pytest.raises(ValueError, match="name"):
        asyncio.run(api.tools.call("", {}, user_id=1))
    with pytest.raises(TypeError):
        asyncio.run(api.tools.call("parse_material", "not-dict", user_id=1))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="user_id"):
        asyncio.run(api.tools.call("parse_material", {}, user_id=0))


# --- 门面接线 ---


def test_facade_exposes_tools_call():
    api = _facade()
    assert callable(api.tools.call)
    assert asyncio.iscoroutinefunction(api.tools.call)

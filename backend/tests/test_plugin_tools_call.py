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

from app.api.errors import ApiError
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
        return LLMResponse(
            content=self._content, usage=LLMUsage(input_tokens=20, output_tokens=5, model="claude-sonnet-5")
        )


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
    res = asyncio.run(_facade().tools.call("parse_material", {"kind": "text", "source": "一段学习材料"}, user_id=uid))
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


def test_call_unknown_tool_is_404_envelope(monkeypatch):
    """iter 385 R28：未知工具异常双继承 ApiError(not_found, 404)——此前裸 ValueError
    逃出插件路由成 500（热管理窗口期在途调用的正常时序被恶化为服务器错误）。"""
    from app.api.errors import UnknownToolError

    exc = None
    try:
        asyncio.run(_facade().tools.call("nope", {}, user_id=1))
    except ValueError as e:
        exc = e
    assert isinstance(exc, UnknownToolError)
    assert isinstance(exc, ApiError)
    assert exc.code == "not_found" and exc.status == 404
    assert "未注册的工具" in str(exc)


def test_plugin_route_unknown_tool_http_404(tmp_path):
    """端点级：插件路由未捕获未知工具异常 -> 宿主统一错误信封 404（非 500）。"""
    (tmp_path / "toolmiss.py").write_text(
        "from fastapi import APIRouter\n\n"
        'PLUGIN_META = {"id": "toolmiss", "name": "TM", "version": "0.1.0"}\n\n'
        "def register(api):\n"
        "    router = APIRouter()\n\n"
        "    @router.get('/boom')\n"
        "    async def boom():\n"
        "        return await api.tools.call('ghost_tool', {}, user_id=1)\n\n"
        "    api.routes.mount(router, auth=False)\n",
        encoding="utf-8",
    )
    from app.main import create_app
    from app.plugins.loader import load_plugins

    app = create_app()
    load_plugins(app, tmp_path)
    from fastapi.testclient import TestClient

    r = TestClient(app).get("/api/plugins/toolmiss/boom")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "not_found"
    assert "ghost_tool" in r.json()["error"]["message"]


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


# --- api.tools.list 发现（iter 317）---


def test_tools_list_returns_core_and_plugin_tools():
    api = _facade()
    api.tools.register_python("tooltest.discoverable", lambda inp, ctx: {"ok": True})
    tools = api.tools.list()
    names = [t["name"] for t in tools]
    # 核心工具（可组合的工作流积木）
    assert "parse_material" in names
    assert "understand_intent" in names
    assert "generate_quiz" in names
    # 插件工具亦列（插件可知自己注册了什么）
    assert "tooltest.discoverable" in names
    # 元数据结构 + 核心工具有人类描述
    pm = next(t for t in tools if t["name"] == "parse_material")
    assert set(pm) == {"name", "llm", "requires", "description"}
    assert pm["llm"] is False  # parse_material 是 Python 工具
    assert pm["description"]  # TOOL_DESCRIPTIONS 提供


def test_tools_list_llm_flag_for_llm_tool():
    api = _facade()
    from app.agent.registry import Tool

    api.tools.register(Tool(name="tooltest.llmtool", llm=True, inline_prompt="x"))
    tools = api.tools.list()
    t = next(x for x in tools if x["name"] == "tooltest.llmtool")
    assert t["llm"] is True  # LLM 工具标志（插件据此知哪些耗预算）
    assert t["description"] == ""  # 未设描述 -> 空


# --- Tool.description（iter 324）---


def test_register_python_description_shows_in_list():
    """register_python(description=) -> api.tools.list 展示插件工具自带描述。"""
    api = _facade()
    api.tools.register_python("tooltest.docced", lambda inp, ctx: {}, description="一个有描述的工具")
    t = next(x for x in api.tools.list() if x["name"] == "tooltest.docced")
    assert t["description"] == "一个有描述的工具"


def test_register_tool_description_field_shows_in_list():
    """register(Tool(..., description=...)) -> list 展示（LLM 工具亦可带描述）。"""
    from app.agent.registry import Tool

    api = _facade()
    api.tools.register(Tool(name="tooltest.field_doc", llm=True, inline_prompt="x", description="字段描述"))
    t = next(x for x in api.tools.list() if x["name"] == "tooltest.field_doc")
    assert t["description"] == "字段描述"


def test_core_tool_description_falls_back_to_dict():
    """核心工具 Tool.description 缺省空 -> tool_metadata 回退 TOOL_DESCRIPTIONS（parse_material）。"""
    pm = next(x for x in _facade().tools.list() if x["name"] == "parse_material")
    assert pm["description"]  # 来自 TOOL_DESCRIPTIONS（核心工具未设字段）

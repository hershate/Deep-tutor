"""mcp_bridge 真实插件装载测试（API 稳定性验证）。

加载仓库 plugins/mcp_bridge.py 真实源码，验证 mcp+tools+routes 协同：
- mcp：connect 连接外部 MCP server + list_tools / call_tool
- tools：mcp_bridge.list_tools 宿主工具（经 api.tools 调用）
- routes：POST /tools / POST /invoke / GET /info

外部 MCP server 经 monkeypatch app.plugins.mcp.McpHttpClient 模拟（api.mcp.connect 函数内
import 该类，patch 后生效），避免真实 HTTP server 依赖。
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

_PLUGINS_SRC = Path(__file__).resolve().parents[2] / "plugins"


def _load_real(name: str, tmp_path: Path):
    src = _PLUGINS_SRC / f"{name}.py"
    assert src.is_file(), f"真实插件源码缺失: {src}"
    shutil.copy2(src, tmp_path / f"{name}.py")
    from app.main import create_app
    from app.plugins.loader import load_plugins

    app = create_app()
    load_plugins(app, tmp_path)
    return app


def _status_map():
    from app.plugins.loader import get_plugin_statuses

    return {s.id: s for s in get_plugin_statuses()}


def _ensure_admin():
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        if not db.query(models.User).filter_by(is_admin=True).first():
            db.add(models.User(name="admin", username="admin", password_hash="x", is_admin=True))
            db.commit()
    finally:
        db.close()


# --- mock McpHttpClient（api.mcp.connect 函数内 import 此类）---


class _MockMcpClient:
    created: list = []
    closed: list = []

    def __init__(self, url, *, headers=None, timeout=30.0, **k):
        self.url = url
        self.server_info = {"name": "mock", "version": "1"}

    async def initialize(self):
        pass

    async def list_tools(self):
        return [{"name": "echo", "description": "回显文本", "inputSchema": {}}]

    async def call_tool(self, name, arguments=None):
        return {
            "content": [{"type": "text", "text": f"echo: {(arguments or {}).get('text', '')}"}],
            "isError": False,
        }

    async def close(self):
        self.closed.append(self.url)


def _mock(monkeypatch):
    _MockMcpClient.created = []
    _MockMcpClient.closed = []
    monkeypatch.setattr("app.plugins.mcp.McpHttpClient", _MockMcpClient)


# --- 装载 ---


def test_loads(tmp_path):
    _load_real("mcp_bridge", tmp_path)
    s = _status_map()["mcp_bridge"]
    assert s.state == "loaded"
    assert "mcp_bridge.list_tools" in s.tools
    assert "/api/plugins/mcp_bridge" in s.routes


# --- routes /tools /invoke ---


def test_list_tools_route(tmp_path, auth_headers, monkeypatch):
    _mock(monkeypatch)
    app = _load_real("mcp_bridge", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    r = client.post(
        "/api/plugins/mcp_bridge/tools", json={"url": "http://mock/mcp"}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["tools"][0]["name"] == "echo"
    assert d["server_info"]["name"] == "mock"
    assert len(_MockMcpClient.closed) == 1  # 用完 close 防连接泄漏


def test_invoke_route(tmp_path, auth_headers, monkeypatch):
    _mock(monkeypatch)
    app = _load_real("mcp_bridge", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    r = client.post(
        "/api/plugins/mcp_bridge/invoke",
        json={"url": "http://mock/mcp", "tool": "echo", "arguments": {"text": "你好"}},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["content"][0]["text"] == "echo: 你好"


def test_info_route(tmp_path, auth_headers):
    app = _load_real("mcp_bridge", tmp_path)
    _ensure_admin()
    r = TestClient(app).get("/api/plugins/mcp_bridge/info", headers=auth_headers)
    assert r.status_code == 200


# --- tools：mcp_bridge.list_tools 宿主工具 ---


def test_list_tools_via_tool_registry(tmp_path, monkeypatch):
    _mock(monkeypatch)
    _load_real("mcp_bridge", tmp_path)
    from app.agent.context import ExecContext
    from app.tools import default_registry
    from app.tools.base import ToolInput

    tool = default_registry().get("mcp_bridge.list_tools")
    inp = ToolInput(tool="mcp_bridge.list_tools", session_id="s", args={"url": "http://mock/mcp"})
    out = asyncio.run(tool.func(inp, ExecContext(session_id="s", trace_id="t")))
    assert out["count"] == 1
    assert out["tools"] == ["echo"]


def test_list_tools_tool_missing_url(tmp_path):
    _load_real("mcp_bridge", tmp_path)
    from app.agent.context import ExecContext
    from app.tools import default_registry
    from app.tools.base import ToolInput

    tool = default_registry().get("mcp_bridge.list_tools")
    out = asyncio.run(tool.func(ToolInput(tool="mcp_bridge.list_tools", session_id="s", args={}), ExecContext(session_id="s", trace_id="t")))
    assert out == {"error": "missing url"}


# --- 容错：连接失败优雅返回错误（不抛崩路由）---


def test_connect_error_handled(tmp_path, auth_headers, monkeypatch):
    class _BadClient:
        def __init__(self, *a, **k):
            pass

        async def initialize(self):
            raise RuntimeError("server down")

        async def close(self):
            pass

    monkeypatch.setattr("app.plugins.mcp.McpHttpClient", _BadClient)
    app = _load_real("mcp_bridge", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    r = client.post(
        "/api/plugins/mcp_bridge/tools", json={"url": "http://bad/mcp"}, headers=auth_headers
    )
    assert r.status_code == 200  # catch 返回 error，不 500
    assert "error" in r.json()

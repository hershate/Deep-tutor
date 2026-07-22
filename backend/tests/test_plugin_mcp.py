"""后端插件 API v1.9.0 MCP 客户端测试（API.md「§MCP 客户端」）。

用 httpx.MockTransport 起一个 spec-faithful 的 MCP server 桩（initialize / notifications/initialized /
tools/list / tools/call，JSON 与 SSE 双响应模式），验证 McpHttpClient 协议正确性 + api.mcp.connect 接线。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.plugins.api import BackendPluginAPI, McpNamespace
from app.plugins.mcp import McpHttpClient
from app.plugins.runtime import PluginStatus


def _facade() -> BackendPluginAPI:
    from fastapi import FastAPI

    status = PluginStatus(id="mcptest", name="mcptest", version="", file="", state="loaded")
    return BackendPluginAPI(app=FastAPI(), plugin_id="mcptest", data_dir="/tmp", status=status)


def _mock_transport(*, sse: bool = False, capture=None, status_for_tools_call=200, error_methods=None):
    """spec-faithful MCP server 桩。capture: 收到的请求头/方法列表（验证 session 回传等）。
    error_methods: 对这些方法返回 JSON-RPC error（验证高层方法抛 RuntimeError）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body.get("method")
        msg_id = body.get("id")
        if capture is not None:
            capture.append({"method": method, "Mcp-Session-Id": request.headers.get("mcp-session-id")})
        headers: dict[str, str] = {}
        if method == "initialize":
            headers["mcp-session-id"] = "sess-123"
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": "2025-06-18", "serverInfo": {"name": "mock", "version": "1"}, "capabilities": {}}}
        elif method == "notifications/initialized":
            return httpx.Response(202)  # 通知无响应体
        elif error_methods and method in error_methods:
            resp = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": "server error"}}
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": [
                {"name": "echo", "description": "回显文本", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}}
        elif method == "tools/call":
            if status_for_tools_call != 200:
                return httpx.Response(status_for_tools_call, text="upstream down")
            args = body.get("params", {}).get("arguments", {})
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {
                "content": [{"type": "text", "text": "echo: " + args.get("text", "")}], "isError": False}}
        else:
            resp = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "method not found"}}

        if sse:
            return httpx.Response(200, text=f"data: {json.dumps(resp)}\n\n",
                                  headers={**headers, "content-type": "text/event-stream"})
        return httpx.Response(200, json=resp, headers=headers)

    return httpx.MockTransport(handler)


async def _client(*, sse=False, capture=None, status_for_tools_call=200, error_methods=None) -> McpHttpClient:
    c = McpHttpClient(
        "http://mock/mcp",
        client=httpx.AsyncClient(transport=_mock_transport(
            sse=sse, capture=capture, status_for_tools_call=status_for_tools_call, error_methods=error_methods)),
    )
    await c.initialize()
    return c


# --- McpHttpClient 协议（JSON 模式）---


def test_initialize_captures_session_and_server_info():
    c = asyncio.run(_client())
    assert c.server_info["name"] == "mock"
    assert c._session_id == "sess-123"  # noqa: SLF001  测试校验内部 session 捕获
    asyncio.run(c.close())


def test_list_tools():
    c = asyncio.run(_client())
    tools = asyncio.run(c.list_tools())
    assert tools == [{"name": "echo", "description": "回显文本",
                      "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]
    asyncio.run(c.close())


def test_call_tool():
    c = asyncio.run(_client())
    res = asyncio.run(c.call_tool("echo", {"text": "你好"}))
    assert res["isError"] is False
    assert res["content"][0]["text"] == "echo: 你好"
    asyncio.run(c.close())


# --- SSE 响应模式 ---


def test_sse_response_mode():
    """MCP server 以 text/event-stream 响应（data: 行携带 JSON-RPC）——客户端须能解析。"""
    c = asyncio.run(_client(sse=True))
    tools = asyncio.run(c.list_tools())
    assert tools[0]["name"] == "echo"
    res = asyncio.run(c.call_tool("echo", {"text": "x"}))
    assert res["content"][0]["text"] == "echo: x"
    asyncio.run(c.close())


# --- session id 回传 + 错误 ---


def test_session_id_sent_on_subsequent_requests():
    capture: list = []
    c = asyncio.run(_client(capture=capture))
    asyncio.run(c.list_tools())  # initialize 已在 _client 内；此为后续请求
    methods = [m["method"] for m in capture]
    assert "initialize" in methods and "tools/list" in methods
    # initialize 之后的所有请求携带 Mcp-Session-Id
    post_init = [m for m in capture if m["method"] != "initialize"]
    assert all(m["Mcp-Session-Id"] == "sess-123" for m in post_init), "后续请求未回传 session id"
    asyncio.run(c.close())


def test_jsonrpc_error_raises():
    """server 对 tools/list 返回 JSON-RPC error -> list_tools 抛 RuntimeError（插件处理）。"""
    c = asyncio.run(_client(error_methods={"tools/list"}))
    with pytest.raises(RuntimeError, match="失败"):
        asyncio.run(c.list_tools())
    asyncio.run(c.close())


def test_http_status_error_raises():
    c = asyncio.run(_client(status_for_tools_call=503))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(c.call_tool("echo", {"text": "x"}))
    asyncio.run(c.close())


# --- api.mcp.connect 接线 ---


def test_connect_url_validation():
    api = _facade()
    with pytest.raises(ValueError, match="非空字符串"):
        asyncio.run(api.mcp.connect(""))
    with pytest.raises(ValueError, match="http"):
        asyncio.run(api.mcp.connect("ftp://server"))  # type: ignore[arg-type]


def test_connect_initializes_and_returns_client(monkeypatch):
    """connect 创建客户端 + 调 initialize；返回就绪客户端。用桩替换 McpHttpClient 验证接线。"""

    class _Stub:
        def __init__(self, url, *, headers=None, timeout=30.0):
            self.url = url
            self.headers = headers
            self.timeout = timeout
            self.initialized = False

        async def initialize(self):
            self.initialized = True
            return {"name": "stub"}

    monkeypatch.setattr("app.plugins.mcp.McpHttpClient", _Stub)
    api = _facade()
    client = asyncio.run(api.mcp.connect("https://mcp.example.com/sse", headers={"Authorization": "Bearer x"}))
    assert isinstance(client, _Stub)
    assert client.initialized is True
    assert client.url == "https://mcp.example.com/sse"
    assert client.headers == {"Authorization": "Bearer x"}


def test_facade_exposes_mcp():
    api = _facade()
    assert isinstance(api.mcp, McpNamespace)
    assert asyncio.iscoroutinefunction(api.mcp.connect)

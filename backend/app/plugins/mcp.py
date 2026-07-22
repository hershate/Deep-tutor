"""MCP（Model Context Protocol）Streamable HTTP 客户端（后端插件 API v1.9.0 扩展点）。

让后端经插件 API 连接**外部 MCP server**（Streamable HTTP 传输），列出并调用其工具——
"扩展后端类 Claude Code 可以调用的工具和 MCP"（用户点名需求）。插件据此把任意外部
MCP server 的工具接入（如 filesystem / github / 自建工具 server），在自有路由里调用，
或包成宿主工具经 api.tools.call/list 复用。

实现要点（按 MCP Streamable HTTP 传输规范）：
- 单端点 POST JSON-RPC；请求带 Accept: application/json, text/event-stream；
  响应可能是 application/json（单 JSON-RPC）或 text/event-stream（SSE，data: 行携带 JSON-RPC）。
- initialize 握手捕获 Mcp-Session-Id 头，后续请求带上；握后发 notifications/initialized 通知。
- tools/list -> [{name, description, inputSchema}]；tools/call {name, arguments} -> {content, isError}。

安全/稳定：
- 客户端仅插件显式调用（不在任何热路径）——server 慢/挂时调用失败抛清晰错误，不影响宿主稳定。
- 出站到 admin/插件配置的可信 URL（同供应商 base_url 信任模型；MCP server 可在 localhost）。
- httpx 显式超时；JSON-RPC error -> RuntimeError（插件处理）；非 200 -> httpx.HTTPStatusError。

范围（v1）：Streamable HTTP 传输。stdio 传输（子进程管理）+ 真实 MCP server 互操作验证为后续轮。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

log = logging.getLogger("deep_tutor.plugins.mcp")

# MCP 协议版本（Streamable HTTP 传输）
_PROTOCOL_VERSION = "2025-06-18"
_DEFAULT_TIMEOUT = 30.0


def _extract_message(body_text: str, expected_id: int | None) -> dict[str, Any]:
    """从 text/event-stream 响应体提取匹配 expected_id 的 JSON-RPC 消息。

    SSE：`data: {json}\\n\\n`；逐行取 data: 内容，json.loads，找 id 匹配者。
    """
    for raw in body_text.splitlines():
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload.startswith("[") and payload.endswith("]"):
            # 多行 data 拼接场景此处简化跳过（JSON-RPC 单消息通常单行）
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and (expected_id is None or obj.get("id") == expected_id):
            return obj
    raise RuntimeError(f"MCP SSE 响应无匹配 id={expected_id!r} 的消息")


class McpHttpClient:
    """已连接（已 initialize）的 MCP Streamable HTTP 客户端。

    生命周期：经 api.mcp.connect() 创建（自动 initialize + 发 initialized 通知）；
    用完调 close()（或随插件进程结束）。list_tools()/call_tool() 各发一次 JSON-RPC。
    """

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._session_id: str | None = None
        self._next_id = 0
        # 允许注入 client（测试用）；否则新建（带超时）
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._server_info: dict[str, Any] = {}

    @property
    def server_info(self) -> dict[str, Any]:
        return dict(self._server_info)

    def _build(self, method: str, params: dict[str, Any] | None = None, *, notification: bool = False) -> dict[str, Any]:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notification:
            self._next_id += 1
            msg["id"] = self._next_id
        if params is not None:
            msg["params"] = params
        return msg

    async def _post(self, msg: dict[str, Any], *, expect_response: bool = True) -> dict[str, Any] | None:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        resp = await self._client.post(self._url, json=msg, headers=headers)
        # initialize 响应可能带 session id（即便本次非 2xx 也先不捕获）
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid
        if not expect_response:
            # notifications：成功即可（204/200/202），无 body
            return None
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "").lower()
        if "text/event-stream" in ct:
            return _extract_message(resp.text, msg.get("id"))
        data = resp.json()
        if isinstance(data, list):
            # 批量（罕见）：取匹配 id
            for item in data:
                if isinstance(item, dict) and item.get("id") == msg.get("id"):
                    return item
            raise RuntimeError("MCP 批量响应无匹配 id 的消息")
        return data

    async def initialize(self) -> dict[str, Any]:
        """MCP initialize 握手（捕获 session id + server info），随后发 initialized 通知。"""
        msg = self._build(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "deep-tutor", "version": "1.0"},
            },
        )
        resp = await self._post(msg)
        if resp is None or "error" in resp:
            raise RuntimeError(f"MCP initialize 失败: {resp}")
        result = resp.get("result", {}) if isinstance(resp, dict) else {}
        self._server_info = (result.get("serverInfo") if isinstance(result, dict) else None) or {}
        # 发 initialized 通知（规范要求；无响应）
        try:
            await self._post(self._build("notifications/initialized", notification=True), expect_response=False)
        except Exception:  # noqa: BLE001  某些 server 对通知返回非标准状态；不阻断
            log.debug("MCP notifications/initialized 发送异常（忽略）")
        return self._server_info

    async def list_tools(self) -> list[dict[str, Any]]:
        """tools/list -> [{name, description, inputSchema}, ...]。"""
        resp = await self._post(self._build("tools/list"))
        if resp is None or "error" in resp:
            raise RuntimeError(f"MCP tools/list 失败: {resp}")
        result = resp.get("result", {}) if isinstance(resp, dict) else {}
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return tools if isinstance(tools, list) else []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """tools/call {name, arguments} -> {content: [{type, text/...}], isError, ...}。"""
        resp = await self._post(self._build("tools/call", {"name": name, "arguments": arguments or {}}))
        if resp is None:
            raise RuntimeError("MCP tools/call 无响应")
        if "error" in resp:
            raise RuntimeError(f"MCP tools/call({name}) 错误: {resp.get('error')}")
        return resp.get("result", {}) if isinstance(resp, dict) else {}

    async def close(self) -> None:
        await self._client.aclose()

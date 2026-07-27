"""MCP 工具桥（后端插件，复合 mcp+tools+routes）。

连接外部 MCP server（Model Context Protocol，Streamable HTTP），列出并调用其工具——把任意
外部工具 server（filesystem / github / 自建）接入 Deep-tutor，经 /tools /invoke 调用，或包成
宿主工具 mcp_bridge.list_tools 供其他工作流发现/组合。

实际用途：在 Deep-tutor 内调用外部 MCP server 工具（如读本地文件系统的 filesystem server、
查 GitHub issue 的 github server），无需自己实现这些能力——「扩展后端类 Claude Code 可调用的工具」。

复合 API（验证扩展点协同）：
- api.mcp：connect 连接外部 MCP server + list_tools / call_tool
- api.tools：register mcp_bridge.list_tools 工具（供 api.tools.call / list 组合新工作流）
- api.routes：POST /tools（列工具）、POST /invoke（调工具）、GET /info

稳定性：每请求新建连接 + 用完 close（防连接泄漏）；连接/调用失败 catch 返回 {error}（不抛崩路由）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

PLUGIN_META = {
    "id": "mcp_bridge",
    "name": "MCP 工具桥",
    "version": "0.1.0",
    "description": "接入外部 MCP server 工具（mcp+tools+routes）",
    "min_api": "1.9",  # mcp(1.9) + tools(1.0) + routes(1.0)
}


class ToolsIn(BaseModel):
    url: str


class InvokeIn(BaseModel):
    url: str
    tool: str
    arguments: dict = {}


def register(api):
    async def _connect_and_list(url: str):
        client = await api.mcp.connect(url)
        try:
            tools = await client.list_tools()
            simplified = [
                {"name": t.get("name"), "description": t.get("description", "")} for t in tools
            ]
            return simplified, client.server_info
        finally:
            await client.close()

    async def list_tools_tool(inp, ctx):
        """宿主工具：列出 MCP server 工具（供 api.tools.call 组合进自定义工作流）。"""
        url = inp.args.get("url")
        if not url:
            return {"error": "missing url"}
        try:
            tools, _ = await _connect_and_list(url)
            return {"tools": [t["name"] for t in tools], "count": len(tools)}
        except Exception as exc:  # noqa: BLE001  连接失败返回错误（不抛）
            return {"error": str(exc)}

    api.tools.register_python("mcp_bridge.list_tools", list_tools_tool, description="列出 MCP server 工具")

    router = APIRouter()

    @router.post("/tools")
    async def tools(body: ToolsIn, _admin=Depends(api.deps.require_admin)):
        # iter 365：require_admin 收紧——任意登录用户原可令服务器向其指定 URL 发 JSON-RPC
        # POST（SSRF：内网探测 / 云元数据 169.254.169.254 读 IAM 凭证 / 回显内部响应）。
        # 与模块 docstring「admin/插件配置可信」一致；_is_safe_url 会禁 localhost 而误伤合法
        # 本地 MCP server（filesystem 等），故用鉴权收紧而非 URL 黑名单。
        try:
            tools_list, server = await _connect_and_list(body.url)
            return {"tools": tools_list, "server_info": server}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    @router.post("/invoke")
    async def invoke(body: InvokeIn, _admin=Depends(api.deps.require_admin)):  # iter 365：同 /tools 收紧 require_admin
        try:
            client = await api.mcp.connect(body.url)
            try:
                result = await client.call_tool(body.tool, body.arguments)
                return {"tool": body.tool, "result": result}
            finally:
                await client.close()
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    @router.get("/info")
    def info(user=Depends(api.deps.get_current_user)):
        return {"plugin": api.id, "description": "连接外部 MCP server，列出/调用其工具"}

    api.routes.mount(router)
    api.log.info("mcp_bridge 已加载：MCP server 工具桥（/tools /invoke + list_tools 工具）")

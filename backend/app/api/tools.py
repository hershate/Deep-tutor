"""工具发现端点（后端插件 API v1.8.0 / 服务器发现扩展点）。

GET /api/tools  列出本实例全部已注册工具的元数据（核心 14 工具 + 插件工具）——
name/llm/requires/description。供外部客户端 / 替代前端 / 插件动态发现可调用工具
（配合 api.tools.call 组合"新工作流"，或渲染工具调用 UI）。

与 api.tools.list()（插件进程内）对称的 REST 面（外部客户端用）；语义同
GET /materials/formats、GET /export/formats、GET /providers/kinds 的发现端点范式。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import require_auth
from app.tools import default_registry, tool_metadata

router = APIRouter(prefix="/tools", tags=["tools"], dependencies=[Depends(require_auth)])


@router.get("")
def list_tools() -> dict:
    """发现端点：全部已注册工具元数据（核心 + 插件）。认证同宿主端点。"""
    return {"tools": tool_metadata(default_registry())}

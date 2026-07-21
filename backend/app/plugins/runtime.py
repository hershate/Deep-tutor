"""插件运行时共享状态：加载状态台账 + 插件工具表。

模块级状态由 loader 写入、状态端点/tools 注册表读取；测试经 reset_runtime() 隔离
（conftest isolate 夹具每个测试前调用 reset_plugins()）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.registry import Tool


@dataclass
class PluginStatus:
    """单个插件的加载状态（GET /api/plugins 响应行）。"""

    id: str
    name: str
    version: str
    file: str
    state: str  # "loaded" | "failed"
    error: str = ""
    routes: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


_statuses: list[PluginStatus] = []
_plugin_tools: list[Tool] = []


def record(status: PluginStatus) -> None:
    _statuses.append(status)


def statuses() -> list[PluginStatus]:
    return list(_statuses)


def add_tool(tool: Tool) -> None:
    _plugin_tools.append(tool)


def remove_tool(tool: Tool) -> None:
    """移除已注册工具（iter 247：register 中途失败的回滚用）。"""
    try:
        _plugin_tools.remove(tool)
    except ValueError:
        pass


def plugin_tools() -> list[Tool]:
    return list(_plugin_tools)


def reset_runtime() -> None:
    _statuses.clear()
    _plugin_tools.clear()

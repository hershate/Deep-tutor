"""后端插件 API 门面——插件 register(api) 收到的对象（契约见 API.md「后端插件 API」）。

设计原则：插件只依赖本门面，不 import 宿主内部模块路径——宿主内部重构/模块搬家
不破坏已发布插件。门面语义化版本（api.version）只增不改；新增能力以新命名空间/
新方法追加，既有方法签名与行为保持向后兼容。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.plugins import bus, runtime

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

    from app.agent.registry import Tool
    from app.plugins.runtime import PluginStatus

# 后端插件 API 语义化版本（与前端 window.DeepTutor 版本相互独立，见 API.md §版本线）
API_VERSION = "1.1.0"


class ToolsNamespace:
    """Agent 工具注册：插件工具汇入每次请求新建的工具注册表（default_registry）。

    注意：内置生成 PIPELINE 步骤固定，不会自动调用新工具——新工具主要供插件路由/
    其他插件经注册表显式调用；与核心工具同名会覆盖核心实现（注册表告警），
    属高级用法，见 API.md 开发规范。
    """

    def __init__(self, status: PluginStatus) -> None:
        self._status = status

    def register(self, tool: Tool) -> None:
        """注册完整 Tool（LLM 工具或 Python 工具，契约同宿主工具，见 app/agent/registry.py）。"""
        from app.agent.registry import Tool

        if not isinstance(tool, Tool):
            raise TypeError("tool 必须是 app.agent.registry.Tool 实例")
        runtime.add_tool(tool)
        self._status.tools.append(tool.name)

    def register_python(self, name: str, func: Any) -> None:
        """便捷注册 Python 工具：async (inp, ctx) -> dict（data 字典）。

        inp/ctx 契约与宿主工具一致（app/tools/base.py 的 ToolInput；ctx 含 db/registry 等）。
        """
        from app.agent.registry import Tool

        self.register(Tool(name=name, llm=False, func=func))


class RoutesNamespace:
    """REST 路由挂载：插件路由统一隔离在 /api/plugins/<插件id>/ 命名空间下。

    auth=True（默认）经宿主同一认证机制（JWT / AUTH_TOKEN / 开放本机模式）保护，
    未认证 401 与宿主端点语义一致；auth=False 用于公开 webhook 等场景（需自行验签，
    见 API.md 开发规范 §安全）。
    """

    def __init__(self, app: FastAPI, plugin_id: str, status: PluginStatus) -> None:
        self._app = app
        self._pid = plugin_id
        self._status = status

    def mount(self, router: APIRouter, *, auth: bool = True, prefix: str = "") -> str:
        """挂载 APIRouter，返回实际前缀 /api/plugins/<pid>[prefix]（幂等追加，可多次挂载）。

        路由自动带 OpenAPI 标签 `plugin:<id>`（/docs 中按插件分组，便于发现与调试）。
        """
        from fastapi import Depends

        from app.api.deps import require_auth

        if prefix and not prefix.startswith("/"):
            raise ValueError("prefix 需以 / 开头")
        full = f"/api/plugins/{self._pid}{prefix}"
        dependencies = [Depends(require_auth)] if auth else []
        self._app.include_router(
            router, prefix=full, dependencies=dependencies, tags=[f"plugin:{self._pid}"]
        )
        self._status.routes.append(full)
        return full

    @property
    def base_path(self) -> str:
        """本插件路由命名空间前缀（拼接绝对 URL 用）。"""
        return f"/api/plugins/{self._pid}"


class EventsNamespace:
    """事件订阅/发布（总线语义与隔离保证见 app/plugins/bus.py 模块头）。"""

    def __init__(self, status: PluginStatus) -> None:
        self._status = status

    def on(self, event: str, fn: Any) -> Any:
        """订阅事件，返回退订函数。监听器可为同步函数或协程函数。"""
        self._status.events.append(event)
        return bus.on(event, fn)

    def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """发布自定义事件（建议以插件 id 作命名空间，如 "myplugin.xxx"）。"""
        bus.emit(event, payload)


class SettingsView:
    """只读安全配置视图：名称含 key/secret/password/token 的字段一律不暴露。

    插件与宿主同进程运行、本身即受信代码（可直接 import app.core.settings），
    本视图只是防「顺手误读机密并外发」的便利层，不是安全边界。
    """

    _DENY = ("key", "secret", "password", "token")

    def get(self, name: str, default: Any = None) -> Any:
        from app.core.settings import settings

        if any(d in name.lower() for d in self._DENY):
            return default
        return getattr(settings, name, default)

    @property
    def data_dir(self) -> str:
        from app.core.settings import settings

        return settings.data_dir

    @property
    def plugins_dir(self) -> str:
        from app.core.settings import settings

        return settings.plugins_dir


class DepsNamespace:
    """宿主 FastAPI 依赖的 stable 引用（路由 DI 用）。

    插件经 Depends(api.deps.get_current_user) 等方式获得与宿主一致的认证/数据
    归属语义，而不 import 宿主内部路径（内部重构不破坏插件）。
    """

    @property
    def get_db(self) -> Any:
        from app.api.deps import get_db

        return get_db

    @property
    def get_current_user(self) -> Any:
        from app.api.deps import get_current_user

        return get_current_user

    @property
    def require_auth(self) -> Any:
        from app.api.deps import require_auth

        return require_auth

    @property
    def require_admin(self) -> Any:
        from app.api.deps import require_admin

        return require_admin

    @property
    def get_owned_session(self) -> Any:
        from app.api.deps import get_owned_session

        return get_owned_session

    @property
    def get_owned_plan(self) -> Any:
        from app.api.deps import get_owned_plan

        return get_owned_plan


class BackendPluginAPI:
    """register(api) 收到的门面对象（属性一览见 API.md §api 门面）。"""

    def __init__(self, app: FastAPI, plugin_id: str, data_dir: Path, status: PluginStatus) -> None:
        self.version = API_VERSION
        self.id = plugin_id
        self.log = logging.getLogger(f"deep_tutor.plugin.{plugin_id}")
        self.data_dir = data_dir  # 插件私有数据目录（已自动创建），持久化插件自身状态用
        self.tools = ToolsNamespace(status)
        self.routes = RoutesNamespace(app, plugin_id, status)
        self.events = EventsNamespace(status)
        self.settings = SettingsView()
        self.deps = DepsNamespace()

"""R29（iter 386）：include_router lifespan 永久合并泄漏回归。

FastAPI include_router 末段无条件 _merge_lifespan_context——把插件 APIRouter（连同
路由、endpoint、endpoint 闭包捕获的 api 门面图）永久挂进 app.router.lifespan_context
合并链；卸载只摘路由条目、无反合并 API。真实进程实测：热重载一个"路由闭包捕获 api"
的插件，每次泄漏 1 个内核句柄 + 数百对象（gc 不回收——长寿引用）。

修复（RoutesNamespace.mount）：默认 _DefaultLifespan 换为无状态实现（router 事件
handler 本就被 include 复制进 app 事件表，行为不变且消除合并链里的双跑）。
"""

from __future__ import annotations

import gc
import weakref
from pathlib import Path

import fastapi
from fastapi import APIRouter


def _facade(app, pid="p1") -> tuple:
    from app.plugins.api import BackendPluginAPI
    from app.plugins.runtime import PluginStatus

    status = PluginStatus(id=pid, name=pid, version="", file="", state="loaded")
    api = BackendPluginAPI(app=app, plugin_id=pid, data_dir=Path("/tmp"), status=status)
    return api, status


def test_mount_rollback_releases_api_graph(tmp_path):
    """mount + rollback 后，捕获 api 的旧门面图必须可回收（修复前被 lifespan 合并链钉死）。

    router 须在辅助函数内构造——测试局部变量会活到断言之后，把插件路由图钉住造成假红。"""
    from app.plugins import runtime

    app = fastapi.FastAPI()
    api, status = _facade(app)
    ref = weakref.ref(api)

    def _mount_capturing(facade):
        router = APIRouter()

        @router.get("/ping")
        def ping():  # 闭包捕获门面——泄漏触发器（真实插件路由常态）
            facade.events.emit("x", {})
            return {"ok": True}

        facade.routes.mount(router, auth=False)

    _mount_capturing(api)
    api._rollback()
    runtime.remove_status("p1")
    del api, status
    gc.collect()
    assert ref() is None, "卸载后旧 api 门面图仍被引用（lifespan 合并链泄漏）"


def test_custom_lifespan_router_not_swapped(tmp_path):
    """自带自定义 lifespan 的路由器不替换（罕见高级用法保留既有合并语义）。"""

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def custom_ls(app):
        yield {"custom": True}

    app = fastapi.FastAPI()
    api, status = _facade(app)
    router = APIRouter(lifespan=custom_ls)

    @router.get("/x")
    def x():
        return {}

    api.routes.mount(router, auth=False)
    assert router.lifespan_context is not None
    # 自定义 lifespan 保留（未被无状态替换——替换仅针对默认 _DefaultLifespan）
    from app.plugins.api import _stateless_lifespan

    assert router.lifespan_context is not _stateless_lifespan


def test_default_router_swapped_stateless(tmp_path):
    """默认 APIRouter 的 lifespan 被换为无状态实现（合并链不再持有路由器图）。"""
    from app.plugins.api import _stateless_lifespan

    app = fastapi.FastAPI()
    api, status = _facade(app)
    router = APIRouter()

    @router.get("/y")
    def y():
        return {}

    api.routes.mount(router, auth=False)
    assert router.lifespan_context is _stateless_lifespan

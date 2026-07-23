"""后端插件 API v1.17.0 api.middleware 测试（API.md「§HTTP 中间件」）。

覆盖：中间件拦截请求（加头）、错误隔离（fn 抛异常→透传不阻断）、回滚移除、入参校验、状态可观测。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.plugins.api import BackendPluginAPI
from app.plugins.runtime import PluginStatus


def _facade(app=None) -> tuple[BackendPluginAPI, FastAPI]:
    app = app or FastAPI()
    status = PluginStatus(id="mwtest", name="mwtest", version="", file="", state="loaded")
    api = BackendPluginAPI(app=app, plugin_id="mwtest", data_dir="/tmp", status=status)
    return api, app


def test_middleware_intercepts_request():
    """插件中间件拦截请求并加自定义头。"""
    api, app = _facade()

    @app.get("/ping")
    def _ping():
        return {"ok": True}

    async def add_header(request, call_next):
        resp = await call_next(request)
        resp.headers["X-Plugin"] = "mwtest"
        return resp

    api.middleware.register(add_header)

    with TestClient(app) as c:
        r = c.get("/ping")
        assert r.status_code == 200
        assert r.headers["X-Plugin"] == "mwtest"


def test_middleware_error_isolation_passthrough():
    """fn 抛异常 -> 透传原请求（不阻断端点，记日志）。"""
    api, app = _facade()

    @app.get("/ping")
    def _ping():
        return {"ok": True}

    async def boom(request, call_next):
        raise RuntimeError("middleware crashed")

    api.middleware.register(boom)

    with TestClient(app) as c:
        r = c.get("/ping")
        assert r.status_code == 200  # 透传——端点仍可用
        assert r.json()["ok"] is True


def test_middleware_multiple_stack():
    """多个中间件叠加（LIFO：后注册先执行）。"""
    api, app = _facade()

    @app.get("/ping")
    def _ping():
        return {"ok": True}

    order: list[str] = []

    async def first(request, call_next):
        order.append("first-in")
        resp = await call_next(request)
        order.append("first-out")
        return resp

    async def second(request, call_next):
        order.append("second-in")
        resp = await call_next(request)
        order.append("second-out")
        return resp

    api.middleware.register(first)
    api.middleware.register(second)

    with TestClient(app) as c:
        c.get("/ping")
    # second 后注册→外层→先 in 后 out；first 内层
    assert order == ["second-in", "first-in", "first-out", "second-out"]


def test_middleware_rollback_removes():
    """register 失败回滚 -> 中间件移除（请求不再被拦截）。"""
    api, app = _facade()

    @app.get("/ping")
    def _ping():
        return {"ok": True}

    async def add_header(request, call_next):
        resp = await call_next(request)
        resp.headers["X-Plugin"] = "mwtest"
        return resp

    api.middleware.register(add_header)
    api._rollback()  # 模拟 loader register 失败回滚

    with TestClient(app) as c:
        r = c.get("/ping")
        assert "X-Plugin" not in r.headers  # 回滚后无中间件


def test_register_validation():
    api, _ = _facade()
    with pytest.raises(TypeError):
        api.middleware.register("not-callable")  # type: ignore[arg-type]


def test_status_records_middleware():
    app = FastAPI()
    st = PluginStatus(id="mwtest2", name="mwtest2", version="", file="", state="loaded")
    api = BackendPluginAPI(app=app, plugin_id="mwtest2", data_dir="/tmp", status=st)

    async def fn(request, call_next):
        return await call_next(request)

    api.middleware.register(fn)
    assert "mwtest2" in st.middleware

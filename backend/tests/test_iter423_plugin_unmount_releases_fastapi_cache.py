"""iter 423：插件卸载后 endpoint 必须从 FastAPI 的 callable 分类缓存里松手。

FastAPI 0.162 起 `fastapi.dependencies.models` 用 `@lru_cache` 缓存「这个 callable
是不是协程 / 生成器」，**缓存键是持有 endpoint 强引用的 `_CallIdentity`**。于是路由
摘掉之后 endpoint 仍被缓存钉住 → 闭包捕获插件门面的整张 api 图永远回收不了
（iter 386 修掉的卸载泄漏在 0.162 上复发；实测 0.162 + Python 3.12/3.14 均复现）。

修法：卸载路径上清掉这几个纯函数缓存（`app/plugins/api.py::_release_route_call_caches`）。
本文件把「机制」钉住：缓存里不该再有卸载掉的 endpoint。
"""

from __future__ import annotations

import gc
import weakref
from pathlib import Path

import fastapi
from fastapi import APIRouter

from app.plugins.api import BackendPluginAPI, _release_route_call_caches
from app.plugins.runtime import PluginStatus

_FASTAPI_CALL_CACHES = (
    "_is_gen_callable_cached",
    "_is_async_gen_callable_cached",
    "_is_coroutine_callable_cached",
)


def _some_callable():
    """给分类缓存当键的普通 callable（非协程、非生成器）。"""
    return None


def _fastapi_cache_functions() -> list:
    from fastapi.dependencies import models as dep_models

    found = []
    for name in _FASTAPI_CALL_CACHES:
        fn = getattr(dep_models, name, None)
        if fn is not None and hasattr(fn, "cache_clear"):
            found.append(fn)
    return found


def test_fastapi_has_the_pinning_caches():
    """前提：FastAPI 确实有这些以 endpoint 为键的缓存（否则本守卫无的放矢）。"""
    assert _fastapi_cache_functions(), "FastAPI 结构变了：找不到 callable 分类缓存，需重判本守卫"


def test_unmount_releases_the_endpoint_from_the_caches():
    """卸载后，FastAPI 的分类缓存里不该还钉着这个 endpoint。"""
    app = fastapi.FastAPI()
    status = PluginStatus(id="p1", name="p1", version="", file="", state="loaded")
    api = BackendPluginAPI(app=app, plugin_id="p1", data_dir=Path("/tmp"), status=status)

    router = APIRouter()

    @router.get("/ping")
    def ping():
        api.events.emit("x", {})
        return {"ok": True}

    endpoint_ref = weakref.ref(ping)
    api.routes.mount(router, auth=False)
    api._rollback()
    # `api` 故意**不删**：`ping` 的闭包引用它，删掉会把闭包 cell 清空（此时若 endpoint
    # 被调用就是 NameError，而 ruff 的 F821 也会报 undefined）。它本身不引用 endpoint，
    # 留着不影响本断言——断言看的是 endpoint 有没有被缓存钉住。
    del status, router, ping
    gc.collect()

    assert endpoint_ref() is None, "endpoint 仍被 FastAPI 的 callable 分类缓存钉住"


def test_release_helper_clears_every_cache():
    """helper 要把三个缓存都清掉（漏一个就还会钉住 endpoint）。"""
    from fastapi.dependencies.models import _CallIdentity

    caches = _fastapi_cache_functions()
    assert caches
    for fn in caches:
        fn(_CallIdentity(_some_callable))  # 填一条
    assert all(fn.cache_info().currsize > 0 for fn in caches)

    _release_route_call_caches()
    for fn in caches:
        assert fn.cache_info().currsize == 0


def test_release_helper_is_tolerant_of_missing_attributes():
    """上游改名/删除时不能炸——helper 必须尽力而为。"""
    _release_route_call_caches()  # 不抛异常即通过

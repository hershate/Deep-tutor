"""后端插件事件总线（契约见仓库根 API.md「后端插件 API」§事件）。

核心事件由宿主在关键节点广播（startup/shutdown/plan_created/progress_updated/
quiz_submitted，清单与 payload 见 API.md）；插件亦可 emit 自定义事件做插件间协作
（建议以插件 id 作命名空间，如 "myplugin.ready"）。

稳定性语义（宿主承诺）：
- 每个监听器独立 try/except——单个监听器异常不影响其余监听器与宿主流程；
- emit() 同步安全：同步端点（线程池、无事件循环）亦可调用。同步监听器就地执行；
  协程监听器有运行循环时调度为后台任务，无循环时在临时新循环中执行（会阻塞当前线程
  直至完成——监听器应保持轻量，重活请自行后台化）；
- emit_async() 供异步上下文（lifespan / async 端点）按订阅顺序 await 全部监听器。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("deep_tutor.plugins.bus")

Handler = Callable[[dict[str, Any]], Any]

_listeners: dict[str, list[Handler]] = {}


def on(event: str, fn: Handler) -> Callable[[], None]:
    """订阅事件，返回退订函数。

    订阅未知事件名不报错（前向兼容：插件订阅新版本事件，在旧宿主上静默无效，
    与前端 DeepTutor.events 同一语义）。
    """
    if not isinstance(event, str) or not event:
        raise ValueError("event 必填")
    if not callable(fn):
        raise TypeError("监听器必须可调用")
    bucket = _listeners.setdefault(event, [])
    bucket.append(fn)

    def off() -> None:
        try:
            bucket.remove(fn)
        except ValueError:
            pass

    return off


def event_names() -> list[str]:
    """当前有订阅者的事件名（可观测性端点用）。"""
    return sorted(_listeners)


async def _guard_await(coro: Any, event: str) -> None:
    try:
        await coro
    except Exception:  # noqa: BLE001  隔离：协程监听器异常不外溢
        log.exception("插件事件协程监听器异常 (%s)", event)


def _dispatch(fn: Handler, event: str, payload: dict[str, Any]) -> None:
    try:
        result = fn(payload)
        if inspect.isawaitable(result):
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_guard_await(result, event))
            except RuntimeError:
                # 同步端点线程池：无运行循环 -> 临时新循环执行（阻塞当前线程）
                asyncio.run(_guard_await(result, event))
    except Exception:  # noqa: BLE001  隔离：监听器异常不外溢
        log.exception("插件事件监听器异常 (%s)", event)


def emit(event: str, payload: dict[str, Any] | None = None) -> None:
    """同步安全广播（任何上下文可调）。监听器异常各自隔离，永不向调用方抛出。"""
    for fn in list(_listeners.get(event, [])):
        _dispatch(fn, event, payload or {})


async def emit_async(event: str, payload: dict[str, Any] | None = None) -> None:
    """异步广播（lifespan/async 上下文）：按订阅顺序逐个 await（各隔离）。"""
    for fn in list(_listeners.get(event, [])):
        try:
            result = fn(payload or {})
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001  隔离：监听器异常不外溢
            log.exception("插件事件监听器异常 (%s)", event)


def reset() -> None:
    """清空全部订阅（测试隔离用）。"""
    _listeners.clear()

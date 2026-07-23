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
# v1.5.0：全局 sink——接收每一次 emit（含 event 名），供 SSE 事件流端点（app/api/events.py）
# 等需要监听全部事件而非逐个 on() 的消费者订阅。与 _listeners 并列，同一隔离语义。
Sink = Callable[[str, dict[str, Any]], Any]

_listeners: dict[str, list[Handler]] = {}
_sinks: list[Sink] = []


def subscribe_all(fn: Sink) -> Callable[[], None]:
    """订阅全部事件（每次 emit 都收到 (event, payload)）。

    与 on(event, fn) 互补：on 订阅单个事件名；subscribe_all 监听所有事件——
    供需要观察事件流全貌的消费者（SSE 事件流端点转发给前端/外部客户端）。
    监听器异常隔离（同一保证）；返回退订函数。fn 应为轻量同步函数（如入队），
    不要做重活或 await（emit 可能来自同步线程池上下文）。
    """
    if not callable(fn):
        raise TypeError("sink 必须可调用")
    _sinks.append(fn)

    def off() -> None:
        try:
            _sinks.remove(fn)
        except ValueError:
            pass

    return off


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


def _notify_sinks(event: str, payload: dict[str, Any]) -> None:
    """通知全部全局 sink（v1.5.0）。异常隔离——单个 sink 故障不影响其余与宿主。"""
    for fn in list(_sinks):
        try:
            fn(event, payload)
        except Exception:  # noqa: BLE001  隔离：sink 异常不外溢
            log.exception("插件事件 sink 异常 (%s)", event)


def emit(event: str, payload: dict[str, Any] | None = None) -> None:
    """同步安全广播（任何上下文可调）。监听器异常各自隔离，永不向调用方抛出。"""
    payload = payload or {}
    for fn in list(_listeners.get(event, [])):
        _dispatch(fn, event, payload)
    _notify_sinks(event, payload)


async def emit_async(event: str, payload: dict[str, Any] | None = None) -> None:
    """异步广播（lifespan/async 上下文）：按订阅顺序逐个 await（各隔离）。"""
    payload = payload or {}
    for fn in list(_listeners.get(event, [])):
        try:
            result = fn(payload)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001  隔离：监听器异常不外溢
            log.exception("插件事件监听器异常 (%s)", event)
    _notify_sinks(event, payload)


def reset() -> None:
    """清空全部订阅与 sink（测试隔离用）。"""
    _listeners.clear()
    _sinks.clear()

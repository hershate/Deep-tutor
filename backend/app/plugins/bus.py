"""后端插件事件总线（契约见仓库根 API.md「后端插件 API」§事件）。

核心事件由宿主在关键节点广播（startup/shutdown/plan_created/progress_updated/
quiz_submitted，清单与 payload 见 API.md）；插件亦可 emit 自定义事件做插件间协作
（建议以插件 id 作命名空间，如 "myplugin.ready"）。

稳定性语义（宿主承诺）：
- 每个监听器独立 try/except——单个监听器异常不影响其余监听器与宿主流程；
- emit() 同步安全：同步端点（线程池、无事件循环）亦可调用。同步监听器在线程池
  线程就地执行；事件循环上下文（async 端点内 emit）下同步监听器经 to_thread 后台
  任务执行（iter 368：插件误写阻塞调用不再卡死循环；毫秒级不再严格保序）。
  协程监听器有运行循环时调度为后台任务，无循环时在有界 daemon 线程中执行；
- emit_async() 供异步上下文（lifespan / async 端点）按订阅顺序 await 全部监听器
  （同步监听器逐个 to_thread，保序）。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger("deep_tutor.plugins.bus")

Handler = Callable[[dict[str, Any]], Any]
# v1.5.0：全局 sink——接收每一次 emit（含 event 名），供 SSE 事件流端点（app/api/events.py）
# 等需要监听全部事件而非逐个 on() 的消费者订阅。与 _listeners 并列，同一隔离语义。
Sink = Callable[[str, dict[str, Any]], Any]

_listeners: dict[str, list[Handler]] = {}
_sinks: list[Sink] = []
# iter 365 C5：协程监听器的后台 task 须持强引用——loop 仅持弱引用，GC 可 mid-execution
# 回收致监听器副作用（如 webhook 投递）静默丢失。done 回调释放引用。
_bg_tasks: set[asyncio.Task] = set()
# iter 366：同步上下文 emit 协程监听器时的后台 daemon 线程上限（信号量）。此前每次都
# 新起一条 Thread（无界）——插件在同步路由高频 emit + webhook 等协程监听器下，OS 线程
# 随事件流量无界膨胀，可拖垮进程（OOM / 线程数上限）。超额 acquire 非阻塞失败 -> 丢弃
# 事件 + 告警（best-effort 背压，优于无界线程；不阻塞调用线程以免饿死 anyio 线程池）。
_THREAD_SEM: threading.BoundedSemaphore | None = None


def _thread_sem() -> threading.BoundedSemaphore:
    """惰性创建 daemon 线程信号量（按 settings.max_plugin_event_threads）。"""
    global _THREAD_SEM
    if _THREAD_SEM is None:
        from app.core.settings import settings

        _THREAD_SEM = threading.BoundedSemaphore(max(1, int(settings.max_plugin_event_threads)))
    return _THREAD_SEM


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


async def _guard_to_thread(fn: Handler, event: str, payload: dict[str, Any]) -> None:
    """同步监听器移出事件循环执行（iter 368）；返回 awaitable（lambda 包协程等罕见
    形态）则在循环上 await（与原就地调用语义等价）。异常隔离。"""
    try:
        result = await asyncio.to_thread(fn, payload)
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001  隔离：监听器异常不外溢
        log.exception("插件事件监听器异常 (%s)", event)


def _run_awaitable_in_thread(coro: Any, event: str) -> None:
    """后台线程跑协程监听器（独立事件循环）。异常隔离（_guard_await 已记日志）。"""
    try:
        asyncio.run(_guard_await(coro, event))
    except Exception:  # noqa: BLE001
        log.exception("插件事件后台线程执行失败 (%s)", event)


def _run_bounded_thread(coro: Any, event: str, sem: threading.BoundedSemaphore) -> None:
    """有界 daemon 线程跑协程监听器；finally 释放信号量（iter 366）。"""
    try:
        _run_awaitable_in_thread(coro, event)
    finally:
        try:
            sem.release()
        except ValueError:  # 防 acquire/release 失衡兜底（不应发生）
            pass


def _dispatch(fn: Handler, event: str, payload: dict[str, Any]) -> None:
    """单监听器分发（iter 368 重构）：

    - 协程监听器：有运行循环 -> 后台任务；无循环（线程池同步端点）-> 有界 daemon 线程。
    - 同步监听器：无循环 -> 就地执行（线程池线程，不碰循环，顺序不变）；
      有运行循环（async 端点内 emit()）-> to_thread 后台任务——插件是用户可装载代码，
      误写阻塞调用（requests/大文件 IO）此前直接卡死事件循环整段服务。
      代价：循环上下文里同步监听器不再严格保序（毫秒级窗口，现网监听器为计数/日志，
      无顺序依赖）。
    """
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if asyncio.iscoroutinefunction(fn):
            if loop is not None:
                task = loop.create_task(_guard_await(fn(payload), event))
                _bg_tasks.add(task)  # 持强引用防 GC 中途回收（iter 365 C5）
                task.add_done_callback(_bg_tasks.discard)
            else:
                # 同步端点线程池：无运行循环 -> 有界 daemon 线程跑，不阻塞当前线程（iter 364/366）。
                # 原 asyncio.run 阻塞当前线程——webhook sink 含 10s httpx 超时，并发即可抽干
                # anyio 线程池（默认 40），饿死后续同步端点（含登录）。iter 366：原每次新起
                # Thread 无界——改经信号量有界，超额非阻塞丢弃事件 + 告警（best-effort 背压）。
                sem = _thread_sem()
                if not sem.acquire(blocking=False):
                    log.warning(
                        "插件协程事件监听器线程达上限，丢弃事件 %s（可调 MAX_PLUGIN_EVENT_THREADS）",
                        event,
                    )
                    return
                threading.Thread(
                    target=_run_bounded_thread, args=(fn(payload), event, sem), daemon=True
                ).start()
        else:
            if loop is not None:
                task = loop.create_task(_guard_to_thread(fn, event, payload))
                _bg_tasks.add(task)
                task.add_done_callback(_bg_tasks.discard)
            else:
                fn(payload)  # 线程池线程就地执行（原语义）
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
    """异步广播（lifespan/async 上下文）：按订阅顺序逐个 await（各隔离）。

    iter 368：同步监听器经 to_thread 执行（保序——逐个 await 完再下一个），
    不再在事件循环上就地运行（阻塞调用会卡死整段服务）。"""
    payload = payload or {}
    for fn in list(_listeners.get(event, [])):
        try:
            if asyncio.iscoroutinefunction(fn):
                await fn(payload)
            else:
                await _guard_to_thread(fn, event, payload)
        except Exception:  # noqa: BLE001  隔离：监听器异常不外溢
            log.exception("插件事件监听器异常 (%s)", event)
    _notify_sinks(event, payload)


def reset() -> None:
    """清空全部订阅与 sink（测试隔离用）。"""
    _listeners.clear()
    _sinks.clear()
    _bg_tasks.clear()
    global _THREAD_SEM
    _THREAD_SEM = None  # 重建（测试隔离 / 设置变更）

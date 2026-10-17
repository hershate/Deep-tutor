"""插件后台任务调度（后端插件 API v1.10.0 扩展点）。

让插件注册**周期/延迟后台任务**——every(周期) / once(延迟)。插件此前只能注册路由（请求触发）
与事件监听（事件触发），无法做周期工作（定时同步/每日摘要/周期轮询 MCP），register() 须快速
幂当、不能起长任务。本扩展点填补该缺口（"尽可能多扩展后端"）。

设计要点：
- 任务跑在宿主事件循环（asyncio.create_task）；fn 可为 async（await）或 sync（直接调，须非阻塞，
  重活请 fn 内自行 asyncio.to_thread）。
- 错误隔离：fn 抛异常被 _safe_run 捕获并记日志——不杀循环、不影响宿主与其他任务（信任模型同插件代码，
  但单次 fn 失败不连锁）。
- 生命周期：模块级 _tasks 保留任务引用（防 GC）；reset() 取消全部（reset_plugins 调，测试隔离/插件
  卸载）。每任务返 TaskHandle.cancel() 可单独取消。任务不持久化——进程重启即失（周期任务在插件
  register/startup 时重新注册，可接受）。
- 调用须在运行中的事件循环内（register 期间或 async 路由内）——否则 get_running_loop 抛 RuntimeError。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

log = logging.getLogger("deep_tutor.plugins.scheduler")

# 模块级任务台账：保留引用防 GC + reset 统一取消
_tasks: list[asyncio.Task[Any]] = []


def _discard_task(task: asyncio.Task) -> None:
    """iter 365 C4：task 完成后从 _tasks 移除——once() 完成的 task 此前永留 _tasks（缓慢
    内存泄漏，长运行下 RSS 单调上升）。every() 死循环不 done 不触发；cancel() 已先移除容错。"""
    try:
        _tasks.remove(task)
    except ValueError:
        pass


class TaskHandle:
    """单个调度任务句柄：cancel() 取消（幂等）。"""

    def __init__(self, task: asyncio.Task[Any]) -> None:
        self._task = task
        self._cancelled = False

    def cancel(self) -> None:
        """取消任务（幂等；从 _tasks 移除）。"""
        if self._cancelled:
            return
        self._cancelled = True
        self._task.cancel()
        try:
            _tasks.remove(self._task)
        except ValueError:
            pass


# iter 368：单次调度执行上限——插件 sync fn 误写阻塞调用（requests/大文件 IO）或
# async fn 悬挂时，把事件循环卡死整段服务；超时取消并记日志（下轮照常）
_JOB_TIMEOUT_S = 300.0


async def _safe_run(fn: Callable[..., Any]) -> None:
    """执行 fn（async await / sync 经 to_thread 移出循环）；异常/超时隔离记日志不外抛。

    iter 368：① sync fn 移 to_thread——契约虽写明「sync 须非阻塞」，但插件是用户可
    装载的代码，误写阻塞调用（requests、time.sleep、大文件 IO）此前直接卡事件循环
    整段服务无响应；② 300s 执行上限兜底悬挂任务（async fn 内死等 IO 同样卡节奏）。
    """
    try:
        if asyncio.iscoroutinefunction(fn):
            await asyncio.wait_for(fn(), timeout=_JOB_TIMEOUT_S)
        else:
            await asyncio.wait_for(asyncio.to_thread(fn), timeout=_JOB_TIMEOUT_S)
    except TimeoutError:
        log.warning("插件调度任务执行超时（>%ss，已放弃等待本轮）: %r", _JOB_TIMEOUT_S, fn)
    except Exception:  # noqa: BLE001  插件 fn 异常隔离：记日志，不杀循环/不影响他任务
        log.exception("插件调度任务执行异常（已隔离）")


async def _every_loop(interval_s: float, fn: Callable[..., Any], run_immediately: bool) -> None:
    """周期循环：run_immediately 先跑一次，之后每 interval_s 跑一次；CancelledError 自然传播（取消即停）。"""
    if run_immediately:
        await _safe_run(fn)
    while True:
        await asyncio.sleep(interval_s)
        await _safe_run(fn)


def every(interval_s: float, fn: Callable[..., Any], *, run_immediately: bool = False) -> TaskHandle:
    """注册周期任务：每 interval_s 秒跑一次 fn。须在运行中的事件循环内调用。

    返回 TaskHandle（cancel() 取消）。fn async 或 sync（sync 须非阻塞）。
    fn 异常被隔离（记日志，不杀循环）。interval_s 须 > 0。
    """
    interval_s = float(interval_s)
    if interval_s <= 0:
        raise ValueError("interval_s 须 > 0")
    if not callable(fn):
        raise TypeError("fn 必须可调用（async 或 sync）")
    loop = asyncio.get_running_loop()
    task = loop.create_task(_every_loop(interval_s, fn, run_immediately))
    _tasks.append(task)
    task.add_done_callback(_discard_task)
    return TaskHandle(task)


def once(delay_s: float, fn: Callable[..., Any]) -> TaskHandle:
    """注册一次性延迟任务：delay_s 秒后跑一次 fn。须在运行中的事件循环内调用。

    返回 TaskHandle（cancel() 取消，取消则 fn 不跑）。delay_s 须 >= 0。
    """
    delay_s = float(delay_s)
    if delay_s < 0:
        raise ValueError("delay_s 须 >= 0")
    if not callable(fn):
        raise TypeError("fn 必须可调用（async 或 sync）")

    async def _run() -> None:
        await asyncio.sleep(delay_s)
        await _safe_run(fn)

    loop = asyncio.get_running_loop()
    task = loop.create_task(_run())
    _tasks.append(task)
    task.add_done_callback(_discard_task)
    return TaskHandle(task)


def reset() -> None:
    """取消全部调度任务（reset_plugins 调：测试隔离 / 插件卸载）。"""
    for t in _tasks:
        t.cancel()
    _tasks.clear()

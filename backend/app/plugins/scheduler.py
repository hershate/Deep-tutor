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
import threading
from typing import Any, Callable

log = logging.getLogger("deep_tutor.plugins.scheduler")

# 模块级任务台账：保留引用防 GC + reset 统一取消
_tasks: list[asyncio.Task[Any]] = []

# R27（iter 384）宿主主循环绑定：热管理端点重跑 register 时若端点/插件路由运行在
# 线程池线程（无运行循环），every/once 回退到该循环创建任务（load_plugins 于 lifespan
# 内绑定）。此前无循环上下文直接抛 RuntimeError——启动（lifespan 内有循环）与热路径
# 行为不一致：使用 api.scheduler 的插件永远无法热重载/热启用。
_bound_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """绑定/解绑宿主主事件循环（load_plugins 在 lifespan 内绑定；reset_plugins 解绑）。"""
    global _bound_loop
    _bound_loop = loop


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _cancel_task(task: asyncio.Task[Any]) -> None:
    """线程安全取消（iter 384 R27）：task.cancel() 仅可在其所属循环线程调用——
    插件 sync 路由（线程池）自行 cancel 句柄、conftest reset_plugins（测试线程）都发生在
    循环外线程。跨线程直接 cancel：轻则延迟生效（Future.cancel 内部 call_soon 不唤醒
    选择器，取消挂起到循环下次自然醒），重则 debug 模式 _check_thread 抛 RuntimeError
    被回滚的异常隔离吞掉 -> 任务泄漏、已卸载插件的旧代码永续后台跑。"""
    loop = task.get_loop()
    if _running_loop() is loop:
        task.cancel()
    else:
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:
            return  # 循环已关：任务随循环消亡，无需（也无法）取消
    try:
        _tasks.remove(task)
    except ValueError:
        pass


def _discard_task(task: asyncio.Task) -> None:
    """iter 365 C4：task 完成后从 _tasks 移除——once() 完成的 task 此前永留 _tasks（缓慢
    内存泄漏，长运行下 RSS 单调上升）。every() 死循环不 done 不触发；cancel() 已先移除容错。"""
    try:
        _tasks.remove(task)
    except ValueError:
        pass


class TaskHandle:
    """单个调度任务句柄：cancel() 取消（幂等、任意线程可调）。"""

    def __init__(self, task: asyncio.Task[Any]) -> None:
        self._task = task
        self._cancelled = False

    def cancel(self) -> None:
        """取消任务（幂等；从 _tasks 移除）。"""
        if self._cancelled:
            return
        self._cancelled = True
        _cancel_task(self._task)


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


async def _once_run(delay_s: float, fn: Callable[..., Any]) -> None:
    await asyncio.sleep(delay_s)
    await _safe_run(fn)


def _schedule_every(
    loop: asyncio.AbstractEventLoop, interval_s: float, fn: Callable[..., Any], run_immediately: bool
) -> TaskHandle:
    """在指定循环上创建周期任务（iter 384 R27：every 的循环内直接路径）。"""
    task = loop.create_task(_every_loop(interval_s, fn, run_immediately))
    _tasks.append(task)
    task.add_done_callback(_discard_task)
    return TaskHandle(task)


def _schedule_once(loop: asyncio.AbstractEventLoop, delay_s: float, fn: Callable[..., Any]) -> TaskHandle:
    """在指定循环上创建一次性延迟任务（iter 384 R27：once 的循环内直接路径）。"""
    task = loop.create_task(_once_run(delay_s, fn))
    _tasks.append(task)
    task.add_done_callback(_discard_task)
    return TaskHandle(task)


def every(interval_s: float, fn: Callable[..., Any], *, run_immediately: bool = False) -> TaskHandle:
    """注册周期任务：每 interval_s 秒跑一次 fn。返回 TaskHandle（cancel 取消）。fn async 或 sync
    （sync 须非阻塞）。fn 异常被隔离（记日志，不杀循环）。interval_s 须 > 0。

    iter 384 R27：任意线程可调——循环内直接创建；线程池/无循环上下文回退到
    bind_loop 绑定的宿主主循环（call_soon_threadsafe + Event 握手取回句柄）。
    """
    interval_s = float(interval_s)
    if interval_s <= 0:
        raise ValueError("interval_s 须 > 0")
    if not callable(fn):
        raise TypeError("fn 必须可调用（async 或 sync）")

    def _make() -> TaskHandle:
        return _schedule_every(asyncio.get_running_loop(), interval_s, fn, run_immediately)

    if _running_loop() is not None:
        return _make()
    return _create_on_bound_loop(_make)


def once(delay_s: float, fn: Callable[..., Any]) -> TaskHandle:
    """注册一次性延迟任务：delay_s 秒后跑一次。返回 TaskHandle（cancel 取消，取消则 fn 不跑）。

    delay_s 须 >= 0。线程语义同 every（iter 384 R27）。"""
    delay_s = float(delay_s)
    if delay_s < 0:
        raise ValueError("delay_s 须 >= 0")
    if not callable(fn):
        raise TypeError("fn 必须可调用（async 或 sync）")

    def _make() -> TaskHandle:
        return _schedule_once(asyncio.get_running_loop(), delay_s, fn)

    if _running_loop() is not None:
        return _make()
    return _create_on_bound_loop(_make)


def _create_on_bound_loop(make: Callable[[], TaskHandle]) -> TaskHandle:
    """线程池上下文的任务创建：调度到绑定循环执行，Event 握手取回句柄（iter 384 R27）。"""
    loop = _bound_loop
    if loop is None or loop.is_closed():
        raise RuntimeError(
            "api.scheduler 须在事件循环内调用，且宿主主循环未绑定"
            "（load_plugins 尚未在 lifespan 内执行）——无法注册调度任务"
        )
    holder: dict[str, Any] = {}
    done = threading.Event()

    def _create() -> None:
        try:
            holder["handle"] = make()
        except Exception as exc:  # noqa: BLE001  创建异常原样带回调用方（register 应见真实错误）
            holder["error"] = exc
        finally:
            done.set()

    loop.call_soon_threadsafe(_create)
    if not done.wait(timeout=10.0):
        raise RuntimeError("宿主事件循环 10s 内未完成调度任务创建（循环过载或已停止）")
    if "error" in holder:
        raise holder["error"]
    return holder["handle"]


def reset() -> None:
    """取消全部调度任务（reset_plugins 调：测试隔离 / 插件卸载）。线程安全（iter 384 R27）。"""
    for t in _tasks:
        _cancel_task(t)
    _tasks.clear()

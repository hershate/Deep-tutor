"""全局并发治理（多用户 / 高负载稳定性，iter 366）。

背景
----
单进程 asyncio 模型下，LLM 重活任务（方案生成 / AI 对话 / 自学材料 / 后台测验）
与插件 Webhook 投递若各自只受任务级配额约束，跨类相加仍可能无上限并发——100 用户
同时触发时：

- 触发模型供应商速率限制（429），瞬态重试退避风暴级联放大；
- 每路任务/Webhook 各持一条 DB/出站连接 + 缓冲，内存/文件描述符随并发线性膨胀。

本模块提供**按事件循环缓存的有界信号量**作为背压闸：超额时排队等待（优雅降级），
而非无上限并发。任务级配额（生成 / 对话各自的 ``max_concurrent_*``）仍是公平性子限；
本信号量是全局兜底。

放置层级选择
------------
信号量在**任务级**（包住整个 orchestrator run / agent loop / materials 流 / 后台
测验 / webhook 单次投递），而非 provider 级：provider 的 ``stream``/``stream_agentic``
是 async generator，在 ``async with`` 内 ``yield`` 时若消费方提前退出未 ``aclose``，槽位
会泄漏到 GC。任务级包裹的是跑完即返回的普通协程（或被 Starlette 可靠 close 的响应体
生成器），``__aexit__`` 必然执行，槽位不泄漏。

事件循环绑定
------------
信号量按事件循环缓存（同 ``anthropic_provider._shared_client`` 模式）：测试逐用例
``asyncio.run`` 起新循环时自动重建，避免 "Future <...> attached to a different loop"。
``reset_llm_semaphore()`` 供测试隔离调用（同 ``reset_client_cache``）。
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.core.settings import settings


class _NullSlot:
    """上限 <= 0（不限）时的无操作占位上下文管理器。"""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> bool:
        return False


_NULL_SLOT = _NullSlot()

# 按名缓存的 (信号量, 绑定事件循环)。跨循环（测试 asyncio.run / daemon 线程新循环）
# 自动重建，防 "Future attached to a different loop"。
_semaphores: dict[str, tuple[asyncio.Semaphore, asyncio.AbstractEventLoop | None]] = {}
_locks: dict[str, tuple[asyncio.Lock, asyncio.AbstractEventLoop | None]] = {}


def _loop_sem(key: str, size: int) -> asyncio.Semaphore | None:
    """取 key 对应、绑定当前事件循环的信号量；size<=0 -> None（不限）。跨循环自动重建。"""
    if not size or size <= 0:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None  # 无运行循环（同步上下文）：返回既有或新建（无循环绑定）
    cur = _semaphores.get(key)
    if cur is None or cur[1] is not loop:
        sem = asyncio.Semaphore(int(size))
        _semaphores[key] = (sem, loop)
        return sem
    return cur[0]


def llm_task_slot() -> Any:
    """LLM 重活任务槽（生成/对话/材料/后台测验）：限 >0 返回信号量（acquire 排队）；否则占位。"""
    sem = _loop_sem("llm", settings.max_concurrent_llm_calls)
    return sem if sem is not None else _NULL_SLOT


def webhook_slot() -> Any:
    """Webhook 投递槽：限 >0 返回信号量（acquire 排队）；否则占位。防 100 用户 × webhook
    同时触发打满出站连接/文件描述符。"""
    sem = _loop_sem("webhook", settings.max_concurrent_webhook_deliveries)
    return sem if sem is not None else _NULL_SLOT


def search_fetch_slot() -> Any:
    """搜索引擎抓取槽（iter 369）：bing_web 免 Key 抓取的进程级上界。此前并发上界只有
    主检索 pipeline 内的局部信号量（每次调用各一个），材料流 3 worker × 多用户叠加时对
    cn.bing.com 的并发抓取无界——易触发反爬（验证码页）。跨 pipeline/材料流共享。"""
    sem = _loop_sem("search_fetch", settings.max_concurrent_search_fetches)
    return sem if sem is not None else _NULL_SLOT


def reset_llm_semaphore() -> None:
    """测试隔离：清空全部缓存信号量与锁（防跨事件循环残留 waiters）。"""
    _semaphores.clear()
    _locks.clear()


# ---------------------------------------------------------------------------
# 临界段锁（iter 397）
# ---------------------------------------------------------------------------


def loop_lock(key: str) -> asyncio.Lock:
    """按事件循环缓存的互斥锁（测试逐用例 asyncio.run 起新循环时自动重建）。

    模块级 ``asyncio.Lock`` 只在**争用路径**上绑定事件循环（Python 3.10+
    的 ``_LoopBoundMixin._get_loop``）——首次争用后锁即绑定该循环；换循环再
    争用会抛「is bound to a different event loop」。故按循环缓存（同
    ``_loop_sem`` 的信号量口径），避免跨循环复用同一把锁。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None  # 无运行循环（同步上下文）：用无绑定的新锁
    cur = _locks.get(key)
    if cur is None or cur[1] is not loop:
        lk = asyncio.Lock()
        _locks[key] = (lk, loop)
        return lk
    return cur[0]


async def acquire_bounded(lock: asyncio.Lock, timeout: float) -> bool:
    """带**等待上限**地获取锁：拿到返回 True，超时返回 False（调用方快失败）。

    实现要点（与 ``_TimedSlot`` 同款）：acquire 走独立 task + shield——
    ``wait_for`` 超时只取消等待包装，acquire 本身不被取消（``Lock.acquire``
    在「即将获得」瞬间被取消会丢锁）。竞态：恰好在超时瞬间拿到锁 -> 立即
    release，防锁泄漏。

    用途：临界段内含**可长时间等待的外部资源**（如 SQLite 写锁 busy_timeout）
    的锁，若无上限，一个慢持有者会把所有等待者排成 N 倍延迟的串行队列
    （尾延迟无界）。加上限后转为「有界等待 -> 失败可重试」。
    """
    task = asyncio.ensure_future(lock.acquire())
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return True
    except (asyncio.TimeoutError, TimeoutError):
        if task.done() and not task.cancelled() and task.exception() is None:
            lock.release()  # 竞态：恰好在超时瞬间拿到许可 -> 归还，防锁泄漏
        else:
            task.cancel()
            try:
                await task
            except BaseException:  # noqa: BLE001  取消路径异常吞掉（清理收尾）
                pass
        return False

# ---------------------------------------------------------------------------
# 插件 LLM 调用（iter 393）
# ---------------------------------------------------------------------------

# 插件面此前完全绕过全局 LLM 背压：api.llm.complete / api.llm.stream /
# api.tools.call 直连供应商，并发无上界（多用户 + 插件并发 -> 429 退避风暴、
# 连接与内存线性膨胀）。宿主各 LLM 路径（生成/对话/材料/测验/slides 导出）均已
# 经 llm_task_slot 收口，插件面是最后的缺口。
#
# 但**不能直接复用宿主信号量**：宿主 LLM 任务持槽期间可能调插件工具，插件工具
# 内部再调 api.llm.complete -> 同一信号量嵌套等待 -> 持槽任务互等（死锁）。
# 故用**独立信号量**（不与宿主争用，插件不能饿死宿主，宿主也不被插件拖死），
# 并对等待设上限：超时抛 PluginLLMBusy（503 可重试），把极端嵌套降级为
# 有界失败而非挂死。

_PLUGIN_LLM_WAIT_SECONDS = 30.0


class PluginLLMBusy(RuntimeError):
    """等待插件 LLM 槽超时（资源争用，可重试语义）。"""


class _TimedSlot:
    """带等待上限的槽：超时放弃并抛 busy 异常（不无限排队）。"""

    def __init__(
        self,
        sem: asyncio.Semaphore,
        timeout: float,
        exc_factory: type[RuntimeError] = PluginLLMBusy,
        label: str = "插件 LLM 调用槽",
    ) -> None:
        self._sem = sem
        self._timeout = timeout
        self._exc_factory = exc_factory
        self._label = label

    async def __aenter__(self) -> None:
        # shield + 独立取消：wait_for 超时只取消等待包装，acquire 本身不被取消
        # （Semaphore.acquire 在「即将获得」瞬间被取消会丢槽）。
        task = asyncio.ensure_future(self._sem.acquire())
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self._timeout)
        except (asyncio.TimeoutError, TimeoutError):
            if task.done() and not task.cancelled() and task.exception() is None:
                # 竞态：恰好在超时瞬间拿到许可 -> 立即归还，防槽泄漏
                self._sem.release()
            else:
                task.cancel()
                try:
                    await task
                except BaseException:  # noqa: BLE001  取消路径异常吞掉（清理收尾）
                    pass
            raise self._exc_factory(f"等待{self._label}超过 {self._timeout:g}s") from None
        except BaseException:
            task.cancel()
            raise

    async def __aexit__(self, *exc: Any) -> bool:
        self._sem.release()
        return False


def plugin_llm_task_slot(timeout: float = _PLUGIN_LLM_WAIT_SECONDS) -> Any:
    """插件 LLM 调用槽（独立预算 + 有界等待）。

    仅用于**跑完即返回的普通协程**（api.llm.complete / api.tools.call）。
    provider 级 async generator（api.llm.stream）按本模块既定约定**不得**包槽
    ——消费方提前退出未 aclose 会漏槽到 GC；插件面为运维方可信代码，该路径
    维持现状并在 plugins/api.py 内注明。
    """
    sem = _loop_sem("plugin_llm", settings.max_concurrent_plugin_llm_calls)
    if sem is None:
        return _NULL_SLOT
    return _TimedSlot(sem, timeout)


# ---------------------------------------------------------------------------
# 口令校验（iter 397）
# ---------------------------------------------------------------------------

_PWD_WAIT_SECONDS = 5.0
_pwd_pool: ThreadPoolExecutor | None = None
_pwd_pool_lock = threading.Lock()


class PasswordVerifyBusy(RuntimeError):
    """等待口令校验槽超时（资源争用，可重试语义）。"""


def password_pool() -> ThreadPoolExecutor:
    """口令校验专用小线程池。

    与 anyio 默认工作线程池**隔离**：每次登录尝试一次 pbkdf2-hmac-sha256
    600k 轮（实测 ~0.19s CPU）。此前 login 是同步端点 -> 跑在 anyio 池（默认
    40 槽）里，40 个并发登录即可占满全部槽位，使**所有同步端点**（用户管理/
    会话/导出/分享）排队——未认证的 CPU 放大器。改 async 端点后，口令校验若
    仍走默认池，问题只是从「占满 anyio」变成「占满默认执行器」，故另开小池。
    """
    global _pwd_pool
    with _pwd_pool_lock:
        if _pwd_pool is None:
            workers = max(1, settings.max_concurrent_password_verifications or 1)
            _pwd_pool = ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="pwd-verify"
            )
        return _pwd_pool


def password_verify_slot(timeout: float = _PWD_WAIT_SECONDS) -> Any:
    """口令校验槽：限制进程内并发 pbkdf2 数；等待超时抛 PasswordVerifyBusy。"""
    sem = _loop_sem("pwd_verify", settings.max_concurrent_password_verifications)
    if sem is None:
        return _NULL_SLOT
    return _TimedSlot(sem, timeout, PasswordVerifyBusy, "口令校验槽")

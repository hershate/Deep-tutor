"""全局并发治理（多用户 / 高负载稳定性，iter 366）。

背景
----
单进程 asyncio 模型下，LLM 重活任务（方案生成 / AI 对话 / 自学材料 / 后台测验）
若各自只受任务级配额约束，跨类相加仍可能无上限并发——100 用户同时触发时：

- 触发模型供应商速率限制（429），瞬态重试退避风暴级联放大；
- 每路任务各持一条 DB 连接 + LLM 缓冲，内存随并发线性膨胀。

本模块提供**单一全局信号量** ``max_concurrent_llm_calls`` 作为所有 LLM 重活任务
的背压闸：超额时新任务在信号量上排队等待（优雅降级），而非无上限并发。任务级
配额（生成 / 对话各自的 ``max_concurrent_*``）仍是公平性子限；本信号量是全局兜底。

放置层级选择
------------
信号量在**任务级**（包住整个 orchestrator run / agent loop / materials 流 / 后台
测验），而非 provider 级：provider 的 ``stream``/``stream_agentic`` 是 async generator，
在 ``async with`` 内 ``yield`` 时若消费方提前退出未 ``aclose``，槽位会泄漏到 GC。
任务级包裹的是跑完即返回的普通协程（或被 Starlette 可靠 close 的响应体生成器），
``__aexit__`` 必然执行，槽位不泄漏。

事件循环绑定
------------
信号量按事件循环缓存（同 ``anthropic_provider._shared_client`` 模式）：测试逐用例
``asyncio.run`` 起新循环时自动重建，避免 "Future <...> attached to a different loop"。
``reset_llm_semaphore()`` 供测试隔离调用（同 ``reset_client_cache``）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.settings import settings

_sem: asyncio.Semaphore | None = None
_sem_loop: asyncio.AbstractEventLoop | None = None


class _NullSlot:
    """max_concurrent_llm_calls <= 0（不限）时的无操作占位上下文管理器。"""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> bool:
        return False


_NULL_SLOT = _NullSlot()


def _build_semaphore() -> asyncio.Semaphore | None:
    size = settings.max_concurrent_llm_calls
    if not size or size <= 0:
        return None  # 0 / 负 = 不限
    return asyncio.Semaphore(int(size))


def get_llm_semaphore() -> asyncio.Semaphore | None:
    """取当前事件循环绑定的全局 LLM 信号量（跨循环自动重建）。无运行循环或设为不限 -> None。"""
    global _sem, _sem_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None  # 无运行循环（同步上下文调用）：返回既有或 None
    if _sem is None or _sem_loop is not loop:
        _sem = _build_semaphore()
        _sem_loop = loop
    return _sem


def llm_task_slot() -> Any:
    """LLM 重活任务槽上下文管理器。限 >0 返回信号量（acquire 排队）；否则返回无操作占位。"""
    sem = get_llm_semaphore()
    return sem if sem is not None else _NULL_SLOT


def reset_llm_semaphore() -> None:
    """测试隔离：每用例重建信号量，防跨事件循环残留 waiters。"""
    global _sem, _sem_loop
    _sem = None
    _sem_loop = None

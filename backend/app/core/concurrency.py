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
    """测试隔离：清空全部缓存信号量（防跨事件循环残留 waiters）。"""
    _semaphores.clear()

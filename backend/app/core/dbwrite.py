"""DB 写提交移出事件循环（iter 393）。

问题
----
async 端点内直接调用 `db.commit()` / `db.flush()` 时，SQLite 写锁争用的等待
发生在**事件循环线程**：写锁被占时 busy_timeout（settings.db_busy_timeout_ms，
默认 10000ms）到期才抛 "database is locked"——实测等待 11.0s。这 11s 内事件
循环无法调度任何协程：SSE 心跳停摆、其他请求全部挂起，表现为"全服务无响应"。
典型触发场景：备份导入（单事务持写锁数秒~数十秒）期间的并发写请求。

争用点在 **flush/commit 触发的 DML**（INSERT/UPDATE 获取 WAL 写锁），而非
COMMIT 语句本身——故 flush 与 commit 都要移出，只移 commit 无效。

方案
----
写提交放入**专用写线程池**（与 asyncio 默认线程池隔离：长锁等待不占用
to_thread 的文件 IO 槽位，避免大文件落盘被写锁等待饿死），事件循环只 await
结果。争用超时仍由 api.errors.db_busy_exception_handler 降级为 503 +
Retry-After，语义不变，只是等待不再冻结事件循环。

线程安全
--------
SQLAlchemy Session 非线程安全，但本模块的调用方在 await 点**串行**使用同一
Session（await 期间事件循环线程不触碰该 Session），且 SQLite 连接
`check_same_thread=False`，跨线程顺序使用安全——与 api.memory._finalize_chat
的既有做法同口径。写锁由事务持有，跨线程不改变其归属。
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

# 写线程池容量：SQLite 写本就串行，8 个线程足以覆盖"等锁 + 真写"两段；再大
# 只会在写锁风暴下多占连接池槽位（每请求持一条），收益递减。
_WRITE_WORKERS = 8

_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()


def _write_pool() -> ThreadPoolExecutor:
    """惰性创建进程级写线程池（首次写请求时创建）。"""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=_WRITE_WORKERS, thread_name_prefix="db-write"
            )
        return _pool


async def _run_db(fn: Callable[..., Any], *args: Any) -> Any:
    """在写线程池执行同步 DB 写。

    显式 copy_context：loop.run_in_executor 不像 asyncio.to_thread 那样自动
    传播 contextvars（请求级 trace/租户上下文依赖它）。
    """
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    call = functools.partial(ctx.run, fn, *args)
    return await loop.run_in_executor(_write_pool(), call)


async def commit_async(db: Session) -> None:
    """事务提交移出事件循环（写锁等待只占用写线程池线程）。"""
    await _run_db(db.commit)


async def flush_async(db: Session) -> None:
    """flush 移出事件循环——flush 触发的 DML 才是真正获取 SQLite 写锁的点。"""
    await _run_db(db.flush)


def shutdown_pool(wait: bool = False) -> None:
    """应用关停时回收写线程池（lifespan 收尾调用）。"""
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=wait, cancel_futures=True)

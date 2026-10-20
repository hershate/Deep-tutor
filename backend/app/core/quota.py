"""配额守卫的并发互斥（iter 394）。

配额判定本质是「先查后写」（check-then-act）：计数与插入之间若无互斥，并发请求
可同时通过检查 -> 实际写入数远超配额（并发 N 个请求即突破 N 倍上限）。例：
凭证档案每类上限 20，但 25 个并发创建请求可一次写入 25 行。

单进程部署（scripts/start.* 明确不加 --workers：SSE 流 / 限流 / 并发配额 / 插件
事件总线均为进程内状态），故进程内互斥即足够；跨进程部署需改 DB 级约束
（当前未支持多进程，见 start.sh 注释）。

锁采用**分段（striped）**而非按 key 无限增长：固定 64 条锁，按 key 哈希取一条。
既避免「每用户每类一条锁」的无界内存增长（长期运行的用户数无上限），又让不同
用户/不同类的请求几乎不会互相阻塞。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

_STRIPE_COUNT = 64
_locks: list[threading.Lock] = [threading.Lock() for _ in range(_STRIPE_COUNT)]


def _lock_for(key: str) -> threading.Lock:
    return _locks[hash(key) % _STRIPE_COUNT]


@contextmanager
def quota_slot(key: str) -> Iterator[None]:
    """配额判定的互斥区：key 形如 ``credential:<user_id>:<kind>``。

    用法：把「计数检查 + 插入 + 提交」整段放进互斥区，否则检查与写入之间仍有窗口。

    同步端点直接用本上下文管理器；异步端点用 :func:`guarded_insert_async`
    （检查与写入在同一写线程内完成，同样持有本锁——同步/异步路径共用同一键
    命名空间，故同一资源的两种路径互斥）。
    """
    lock = _lock_for(key)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


async def guarded_insert_async(
    db: Session, obj: object, check: Callable[[Session], None], key: str
) -> None:
    """配额守卫下的「检查 + 入队 + flush」，一次完成（iter 394）。

    check：接受 db 的可调用对象，超限时自行抛 ApiError（429 quota_exceeded）。
    key：配额键——与同步路径同一命名空间。

    实现要点：检查（SELECT 计数）与写入（flush 触发的 INSERT）必须在**同一线程、
    不跨 await**地完成。若把检查放在事件循环里、写入放在写线程，中间仍有窗口：
    并发请求可同时通过「未达上限」检查，实际写入数按并发数突破配额。
    """
    from app.core.dbwrite import run_db_write

    def _work() -> None:
        with quota_slot(key):
            check(db)
            db.add(obj)  # type: ignore[arg-type]
            db.flush()

    await run_db_write(_work)


async def guarded_commit_async(
    db: Session,
    obj: object,
    check: Callable[[Session], None],
    key: str,
    after_flush: Callable[[Session, object], None] | None = None,
) -> None:
    """配额守卫下的「检查 + 入队 + flush + （落盘）+ 提交」，一次完成（iter 394）。

    与 :func:`guarded_insert_async` 的差别：**必须含提交**。原因：SQLite 走
    pysqlite 传统事务模式——flush 触发的 INSERT 在 commit 之前对其他连接**不可见**，
    并发请求的计数看不到它。只锁住「检查」或只锁到 flush，配额仍可被并发突破
    （实测语义：计数必须建立在已提交的可见状态上）。

    after_flush：flush 之后、commit 之前的同步回调（如解析文本落盘 + 回填路径），
    在写线程内执行——既不占事件循环（iter 393 口径），又让「检查 -> 提交」整段
    处于同一把锁内，保持原子的同时避免孤儿行/孤儿文件。
    """
    from app.core.dbwrite import run_db_write

    def _work() -> None:
        with quota_slot(key):
            check(db)
            db.add(obj)  # type: ignore[arg-type]
            db.flush()
            if after_flush is not None:
                after_flush(db, obj)
            db.commit()

    await run_db_write(_work)

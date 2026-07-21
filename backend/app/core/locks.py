"""asyncio 锁台账辅助（有界生命周期，iter 232）。"""

from __future__ import annotations

import asyncio


def drop_lock(registry: dict[int, asyncio.Lock], key: int, lock: asyncio.Lock) -> None:
    """从台账移除已用完的锁（防模块级锁字典无界增长——缓慢内存泄漏）。

    调用契约：须在锁释放后调用，且释放与本调用之间**无 await**（单事件循环下原子）；
    使用方约定「lock.locked() -> 409 立即拒绝、从不等待」，故不存在「等待者仍引用
    旧锁、新协程另建新锁导致并发失控」的竞态——等待者是唯一危险形态，而本约定
    下等待者不存在。
    """
    if registry.get(key) is lock:
        del registry[key]

"""进程内滑动窗口速率限制（plan/09 §十八 速率限制）。

内存实现（单进程 uvicorn）：key -> 最近命中时间戳队列，窗口内超限 -> 拒绝。
多进程部署时应换成 Redis 等共享存储（接口已按可替换设计）。
"""

from __future__ import annotations

import time
from collections import deque


class SlidingWindowLimiter:
    """滑动窗口限流器：check(key, limit, window_s) 命中计数并返回是否放行。"""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, limit: int, window_s: float = 60.0) -> bool:
        now = time.monotonic()
        q = self._hits.setdefault(key, deque())
        cutoff = now - window_s
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    def reset(self) -> None:
        """清空全部计数（测试隔离用）。"""
        self._hits.clear()


# 全局实例（同 verify_url 缓存模式）
limiter = SlidingWindowLimiter()


def reset_rate_limiter() -> None:
    limiter.reset()

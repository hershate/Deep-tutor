"""进程内滑动窗口速率限制（plan/09 §十八 速率限制）。

内存实现（单进程 uvicorn）：key -> 最近命中时间戳队列，窗口内超限 -> 拒绝。
多进程部署时应换成 Redis 等共享存储（接口已按可替换设计）。
"""

from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowLimiter:
    """滑动窗口限流器：check(key, limit, window_s) 命中计数并返回是否放行。"""

    # err.md C6：key 数量兜底上限（key 洪泛攻击下全清，防内存无界增长）
    _MAX_KEYS = 100_000

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        # iter 246：check 是"读-改-写"非原子——FastAPI 同步端点跑在 anyio 线程池，
        # 并发同 key 请求可双双通过（限流形同虚设）；锁粒度为全实例，单进程内存
        # 操作极快，无争用开销之忧
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_s: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if q is not None:
                cutoff = now - window_s
                while q and q[0] <= cutoff:
                    q.popleft()
                if not q:
                    del self._hits[key]  # err.md C6：窗口外空闲 key 惰性淘汰
                    q = None
                elif len(q) >= limit:
                    return False
            if q is None:
                if len(self._hits) >= self._MAX_KEYS:
                    self._hits.clear()  # 兜底：异常量级时全清（限流短暂失效优于 OOM）
                q = deque()
                self._hits[key] = q
            q.append(now)
            return True

    def reset(self) -> None:
        """清空全部计数（测试隔离用）。"""
        with self._lock:
            self._hits.clear()


# 全局实例（同 verify_url 缓存模式）
limiter = SlidingWindowLimiter()


def reset_rate_limiter() -> None:
    limiter.reset()

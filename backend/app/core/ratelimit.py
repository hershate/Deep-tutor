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

    # err.md C6：key 数量兜底上限（key 洪泛攻击下防内存无界增长）
    _MAX_KEYS = 100_000
    # iter 397（E13）：键长上限（防单键超长占内存）——键多由调用方归一
    _MAX_KEY_LEN = 160
    # iter 397（E13）：触顶时淘汰的键数（一次淘汰最老的一批，避免每来一个新键
    # 就退化成全清）
    _EVICT_BATCH = 4096

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        # iter 246：check 是"读-改-写"非原子——FastAPI 同步端点跑在 anyio 线程池，
        # 并发同 key 请求可双双通过（限流形同虚设）；锁粒度为全实例，单进程内存
        # 操作极快，无争用开销之忧
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_s: float = 60.0) -> bool:
        now = time.monotonic()
        # iter 397（E13）：键长封顶（防单键超长占内存）
        if len(key) > self._MAX_KEY_LEN:
            key = key[: self._MAX_KEY_LEN]
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
                    # iter 397（E13）：原为 `self._hits.clear()`——**全清**会把进程内
                    # 全部限流桶（登录/导出/分享/setup/认证失败）一次性清空，限流在
                    # 攻击者选定的时刻整体失效（如登录路径用攻击者可控的用户名
                    # 每次新增 2 个键，约 5 万次即可触发）。现改为**淘汰最老的一批**
                    # （dict 保序 -> 最老键在头部）：只挤掉最久未用的键，存量桶
                    # （含正常用户的）继续有效。
                    for _ in range(self._EVICT_BATCH):
                        if not self._hits:
                            break
                        self._hits.pop(next(iter(self._hits)), None)
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

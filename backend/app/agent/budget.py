"""预算守卫（plan/01 §三 成本计量 / plan/10 §十四 预算超限）。

累计 cost 超过会话预算 -> 触发中断 + 保留部分结果。
limit<=0 视为不限（开发/测试用）。
"""

from __future__ import annotations


class BudgetTracker:
    def __init__(self, limit: float) -> None:
        self.limit = limit
        self.spent: float = 0.0

    def add(self, cost: float) -> None:
        self.spent += cost

    def exceeded(self) -> bool:
        """limit<=0 视为不限。err.md B15：达上限即超限（>=，此前 > 恰满不限）。"""
        if self.limit <= 0:
            return False
        return self.spent >= self.limit

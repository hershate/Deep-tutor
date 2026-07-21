"""预算守卫（plan/01 §三 成本计量 / plan/10 §十四 预算超限）。

累计 cost 超过会话预算 -> 触发中断 + 保留部分结果。
limit<=0 视为不限（开发/测试用）。
"""

from __future__ import annotations

import logging
import math

log = logging.getLogger("deep_tutor.budget")


class BudgetTracker:
    def __init__(self, limit: float) -> None:
        # iter 245：limit=NaN 时 nan<=0 为 False -> exceeded 恒 False（守卫静默失效）。
        # 非法 limit 按「不限」处理并告警（settings 来源为 float 配置，正常不可达）
        if not math.isfinite(limit):
            log.error("预算上限非法 limit=%r，按不限处理", limit)
            limit = 0.0
        self.limit = limit
        self.spent: float = 0.0

    def add(self, cost: float) -> None:
        # iter 245：非法成本防御——NaN 会让 spent=NaN、exceeded() 恒 False（预算守卫
        # 静默失效，比报错更糟）；负数抵减已花费变相绕过预算。内部链路当前不可达
        # （pydantic 拒绝 NaN tokens、价目非负），此为 public 入口契约防线：
        # 拒绝入账并 error 级告警（不静默），不污染累计值
        if not math.isfinite(cost) or cost < 0:
            log.error("预算归集收到非法 cost=%r，已拒绝入账（疑似 provider 构造 bug）", cost)
            return
        self.spent += cost

    def exceeded(self) -> bool:
        """limit<=0 视为不限。err.md B15：达上限即超限（>=，此前 > 恰满不限）。"""
        if self.limit <= 0:
            return False
        return self.spent >= self.limit

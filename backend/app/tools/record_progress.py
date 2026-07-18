"""record_progress 逻辑（plan/02 §9，P2）：掌握度加权 + 间隔重复。

纯 Python 确定性函数，DB upsert 由 API 层（app/api/progress.py）承载（覆盖写，幂等）。
简化 MVP 决策（记入 stage.md）：
- 掌握度 = 答题正确率加权：有 quiz 得分 -> 0.7*score + 0.3*old（近期权重高）；
  无 quiz 标记 done -> max(old, 0.8)（主观标记不给满分）；learning 不动 old。
- next_review 间隔重复简化（SM-2-lite）：mastery <0.5 -> +1d；<0.7 -> +3d；
  <0.85 -> +7d；否则 +14d。
延后：quiz_attempts 逐题落库后的全历史加权、完整 SM-2（EF/重复次数）。
"""

from __future__ import annotations

from datetime import date, timedelta


def compute_mastery(old: float, status: str, quiz_score: float | None) -> float:
    """掌握度（0-1）。quiz_score 为本次答题正确率（0-1，None=无测验）。"""
    old = max(0.0, min(1.0, old))
    if quiz_score is not None:
        score = max(0.0, min(1.0, quiz_score))
        return round(0.7 * score + 0.3 * old, 3)
    if status == "done":
        return max(old, 0.8)  # 主观标记掌握，不给满分
    return old


def next_review_date(mastery: float, today: date | None = None) -> date:
    """下次复习日（间隔重复简化：掌握度越低复习越近）。"""
    today = today or date.today()
    m = max(0.0, min(1.0, mastery))
    if m < 0.5:
        delta = timedelta(days=1)
    elif m < 0.7:
        delta = timedelta(days=3)
    elif m < 0.85:
        delta = timedelta(days=7)
    else:
        delta = timedelta(days=14)
    return today + delta

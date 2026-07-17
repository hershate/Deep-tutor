"""record_progress 纯函数测试（plan/02 §9，P2 iter 39）：掌握度加权 + 间隔重复简化。"""

from __future__ import annotations

from datetime import date, timedelta

from app.tools.record_progress import compute_mastery, next_review_date

_TODAY = date(2026, 7, 17)


def test_mastery_quiz_weighted():
    """有 quiz 得分 -> 0.7*score + 0.3*old（近期权重高）。"""
    assert compute_mastery(0.0, "done", 1.0) == 0.7
    assert compute_mastery(0.5, "done", 0.5) == 0.5
    assert compute_mastery(0.7, "done", 1.0) == 0.91
    assert compute_mastery(0.8, "learning", 0.0) == 0.24


def test_mastery_quiz_score_clamped():
    """score 越界 -> 截断 0-1（防御，API 层 pydantic 已约束）。"""
    assert compute_mastery(0.0, "done", 1.5) == 0.7
    assert compute_mastery(0.0, "done", -0.5) == 0.0


def test_mastery_done_without_quiz():
    """无 quiz 标记 done -> max(old, 0.8)（主观标记不给满分）。"""
    assert compute_mastery(0.3, "done", None) == 0.8
    assert compute_mastery(0.9, "done", None) == 0.9


def test_mastery_learning_unchanged():
    """learning / not_started 无 quiz -> 掌握度不变。"""
    assert compute_mastery(0.3, "learning", None) == 0.3
    assert compute_mastery(0.0, "not_started", None) == 0.0


def test_next_review_bands():
    """SM-2-lite：mastery <0.5 +1d；<0.7 +3d；<0.85 +7d；否则 +14d。"""
    assert next_review_date(0.4, _TODAY) == _TODAY + timedelta(days=1)
    assert next_review_date(0.5, _TODAY) == _TODAY + timedelta(days=3)  # 边界 >=0.5
    assert next_review_date(0.7, _TODAY) == _TODAY + timedelta(days=7)  # 边界 >=0.7
    assert next_review_date(0.85, _TODAY) == _TODAY + timedelta(days=14)  # 边界 >=0.85
    assert next_review_date(1.0, _TODAY) == _TODAY + timedelta(days=14)


def test_next_review_mastery_clamped():
    """mastery 越界 -> 截断后分档。"""
    assert next_review_date(-1.0, _TODAY) == _TODAY + timedelta(days=1)
    assert next_review_date(2.0, _TODAY) == _TODAY + timedelta(days=14)

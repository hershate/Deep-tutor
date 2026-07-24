"""问题1 测试（iter 353）：结构化时间输入 + schedule 日期确定性重写。

覆盖：understand_intent._validate_intent 用 args 结构化字段 authoritative 覆盖 LLM 输出；
schedule_plan.check_schedule 从 start_date（或今天）连续重写日期（根治 LLM 无锚点臆造日期
+ 「今日学习」端点 date.today() 匹配）。
"""

from __future__ import annotations

from datetime import date, timedelta

from app.tools.base import ToolInput


def _inp(args: dict | None = None, upstream: dict | None = None) -> ToolInput:
    return ToolInput(tool="understand_intent", session_id="s", args=args or {}, upstream=upstream or {})


def test_validate_intent_overrides_from_args():
    """args 的结构化时间字段 authoritative 覆盖 LLM 从 raw_text 的抽取。"""
    from app.tools.understand_intent import _validate_intent

    data = {"topic": "x", "daily_minutes": 999, "deadline": "2099-01-01", "start_date": None}
    inp = _inp(args={"daily_minutes": 60, "deadline": "2099-12-31", "start_date": "2099-06-01"})
    out = _validate_intent(data, inp)
    assert out["daily_minutes"] == 60  # 覆盖 LLM 抽取的 999
    assert out["deadline"] == "2099-12-31"
    assert out["start_date"] == "2099-06-01"


def test_validate_intent_no_override_when_args_empty():
    """无结构化 args 时保留 LLM 输出（向后兼容，旧流程不传结构化字段）。"""
    from app.tools.understand_intent import _validate_intent

    data = {"topic": "x", "daily_minutes": 30, "deadline": None, "start_date": None}
    out = _validate_intent(data, _inp())
    assert out["daily_minutes"] == 30


def test_validate_intent_invalid_start_date_cleared():
    """非法 start_date 串 -> 清空（不阻断生成）。"""
    from app.tools.understand_intent import _validate_intent

    out = _validate_intent({"topic": "x", "start_date": "not-a-date"}, _inp())
    assert out["start_date"] is None


def test_check_schedule_rewrites_from_start_date():
    """check_schedule 从 upstream.start_date 连续重写日期（覆盖 LLM 编的 2020-01-01）。"""
    from app.tools.schedule_plan import check_schedule

    sd = (date.today() + timedelta(days=5)).isoformat()
    data = {
        "schedule": [
            {"date": "2020-01-01", "stage_index": 0, "tasks": ["a"], "est_minutes": 60},
            {"date": "2020-01-01", "stage_index": 0, "tasks": ["b"], "est_minutes": 60},
            {"date": "2020-01-01", "stage_index": 1, "tasks": ["c"], "est_minutes": 60},
        ]
    }
    inp = ToolInput(
        tool="schedule_plan", session_id="s", args={},
        upstream={"understand_intent": {"data": {"start_date": sd, "daily_minutes": 60}}},
    )
    out = check_schedule(data, inp)
    dates = [d["date"] for d in out["schedule"]]
    base = date.fromisoformat(sd)
    assert dates == [base.isoformat(), (base + timedelta(days=1)).isoformat(), (base + timedelta(days=2)).isoformat()]
    assert out["start_date"] == sd


def test_check_schedule_defaults_to_today():
    """无 start_date 时从今天开始（不再 LLM 编日期，今日学习端点可匹配今天）。"""
    from app.tools.schedule_plan import check_schedule

    data = {"schedule": [{"date": "2020-01-01", "stage_index": 0, "tasks": ["a"], "est_minutes": 30}]}
    inp = ToolInput(
        tool="schedule_plan", session_id="s", args={},
        upstream={"understand_intent": {"data": {"daily_minutes": 30}}},
    )
    out = check_schedule(data, inp)
    assert out["schedule"][0]["date"] == date.today().isoformat()
    assert out["start_date"] == date.today().isoformat()

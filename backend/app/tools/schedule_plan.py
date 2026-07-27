"""schedule_plan 工具（plan/02 §7，LLM 无 Skill，内联 prompt + 确定性校验）。

按时长/截止日排期。LLM 生成草案 + post_process 确定性校验：
总量 <= 排期跨度 × 每日时长；不足 -> insufficient_time 警告 + 建议（不阻塞交付）。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.agent.registry import Tool
from app.tools.base import ToolInput

_MAX_SCHEDULE_DAYS = 365  # 排期天数上限（plan/11 §二/§三）

PROMPT = """你是 Deep-tutor 的「学习排期」工具。任务：依据学习阶段、每日可用时长与截止日期，生成逐日学习计划。

要求：
- schedule：逐日安排，每条含 date(ISO 8601)、stage_index（对应阶段 index）、tasks（当日任务列表）、
  est_minutes（当日预计分钟数）。
- 每日 est_minutes 不超过每日可用时长；阶段难度高的日子可少排。
- 覆盖所有阶段；阶段顺序与 design_stages 一致。
- 若时长不足以覆盖所有阶段，仍生成一个合理的排期（后续会校验并提示）。
- **日期从 args.start_date（或 upstream.understand_intent.data.start_date）开始连续排列；若未提供则从今天开始。不要自行编造起始日**（程序层会按 start_date 确定性重写日期，你只需保证条目数量与每日内容合理）。

仅返回符合 schema 的 JSON 对象（不要 markdown 代码块）：
{"schedule":[{"date":"2026-07-16","stage_index":0,"tasks":["学 JS 基础"],"est_minutes":60}]}
"""


class ScheduleItem(BaseModel):
    date: str
    stage_index: int = 0
    tasks: list[str] = Field(default_factory=list)
    est_minutes: int = 0

    @field_validator("est_minutes")
    @classmethod
    def _clamp_minutes(cls, v: int) -> int:
        """iter 365 C7：est_minutes 夹紧 >=0——负数会压低 total、击穿容量校验（insufficient_time 失效）。"""
        return max(0, int(v or 0))


class ScheduleData(BaseModel):
    schedule: list[ScheduleItem]


def check_schedule(data: dict[str, Any], inp: ToolInput) -> dict[str, Any]:
    """确定性校验：总量 <= 排期跨度 × 每日时长；排期跨度 ≤365 天（plan/11 §二/§三）。"""
    schedule = data.get("schedule") or []
    # MAX_SCHEDULE_DAYS=365（plan/11 §二/§三）：超长排期截断并标记
    if len(schedule) > _MAX_SCHEDULE_DAYS:
        schedule = schedule[:_MAX_SCHEDULE_DAYS]
        data["schedule"] = schedule
        data["truncated"] = True
    # iter 353：日期确定性重写——从 start_date（用户表单指定，upstream.understand_intent.data.start_date）
    # 或今天开始连续排程，替代 LLM 无锚点臆造的日期（此前 schedule 日期与真实日历错位，致「今日学习」
    # 匹配不到；用户诉求「生成计划的时间由我自己指定」）。
    from datetime import date as _schedule_date, timedelta as _timedelta

    start: _schedule_date | None = None
    try:
        sd_raw = (((inp.upstream or {}).get("understand_intent") or {}).get("data") or {}).get("start_date")
        if sd_raw:
            start = datetime.fromisoformat(str(sd_raw).strip()).date()
    except (ValueError, TypeError):
        start = None
    if start is None:
        start = _schedule_date.today()
    for i, s in enumerate(schedule):
        if isinstance(s, dict):
            s["date"] = (start + _timedelta(days=i)).isoformat()
    data["start_date"] = start.isoformat()
    # err.md B17：daily_minutes 非正数/非数值时跳过容量校验——负数会让
    # capacity 变负，任何排期都误报 insufficient_time
    try:
        daily = int(inp.args.get("daily_minutes") or 0)
    except (TypeError, ValueError):
        daily = 0
    if daily <= 0:
        # iter 245：生产 args 从不携带 daily_minutes（args_by_tool 仅给 understand_intent
        # 传需求、给 start_tool 传 fixes）——容量校验曾因此整体失效（daily 恒 0 跳过）。
        # 从 upstream.understand_intent 派生（已校验 1..1440 或 None；对齐 err.md B21
        # search_resources 的 stage_topics 派生模式）
        try:
            daily = int(
                (((inp.upstream or {}).get("understand_intent") or {}).get("data") or {}).get("daily_minutes") or 0
            )
        except (TypeError, ValueError):
            daily = 0
    if daily <= 0:
        daily = 0
    total = sum(int(s.get("est_minutes", 0) or 0) for s in schedule)
    data["total_minutes"] = total
    if daily:
        data["required_days"] = math.ceil(total / daily)

    if daily and schedule:
        dates = []
        for s in schedule:
            try:
                dates.append(datetime.fromisoformat(s.get("date", "")).date())
            except ValueError:
                continue
        # iter 245：单日排期同样做容量校验（1 天排 600 分钟此前无任何警告）
        if len(dates) >= 1:
            span = (max(dates) - min(dates)).days + 1
            capacity = span * daily
            data["span_days"] = span
            data["capacity_minutes"] = capacity
            if total > capacity:
                data["warning"] = "insufficient_time"
                data["suggestion"] = (
                    f"总时长 {total} 分钟超过排期 {span} 天的容量 {capacity} 分钟；建议压缩任务/延期/降级"
                )
    return data


schedule_plan_tool = Tool(
    name="schedule_plan",
    llm=True,
    skill_name=None,  # plan 指定 schedule_plan 为「LLM 无 Skill」内联 prompt
    output_model=ScheduleData,
    inline_prompt=PROMPT,
    post_process=check_schedule,
    default_data={"schedule": []},
)

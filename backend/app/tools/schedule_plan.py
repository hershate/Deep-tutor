"""schedule_plan 工具（plan/02 §7，LLM 无 Skill，内联 prompt + 确定性校验）。

按时长/截止日排期。LLM 生成草案 + post_process 确定性校验：
总量 <= 排期跨度 × 每日时长；不足 -> insufficient_time 警告 + 建议（不阻塞交付）。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.agent.registry import Tool
from app.tools.base import ToolInput

PROMPT = """你是 Deep-tutor 的「学习排期」工具。任务：依据学习阶段、每日可用时长与截止日期，生成逐日学习计划。

要求：
- schedule：逐日安排，每条含 date(ISO 8601)、stage_index（对应阶段 index）、tasks（当日任务列表）、
  est_minutes（当日预计分钟数）。
- 每日 est_minutes 不超过每日可用时长；阶段难度高的日子可少排。
- 覆盖所有阶段；阶段顺序与 design_stages 一致。
- 若时长不足以覆盖所有阶段，仍生成一个合理的排期（后续会校验并提示）。
- 日期连续或按工作日排列均可，但不要编造周末之外的非existent 日期。

仅返回符合 schema 的 JSON 对象（不要 markdown 代码块）：
{"schedule":[{"date":"2026-07-16","stage_index":0,"tasks":["学 JS 基础"],"est_minutes":60}]}
"""


class ScheduleItem(BaseModel):
    date: str
    stage_index: int = 0
    tasks: list[str] = Field(default_factory=list)
    est_minutes: int = 0


class ScheduleData(BaseModel):
    schedule: list[ScheduleItem]


def check_schedule(data: dict[str, Any], inp: ToolInput) -> dict[str, Any]:
    """确定性校验：总量 <= 排期跨度 × 每日时长。"""
    schedule = data.get("schedule") or []
    daily = inp.args.get("daily_minutes")
    total = sum(int(s.get("est_minutes", 0) or 0) for s in schedule)
    data["total_minutes"] = total
    if daily:
        data["required_days"] = math.ceil(total / daily) if daily else 0

    if daily and schedule:
        dates = []
        for s in schedule:
            try:
                dates.append(datetime.fromisoformat(s.get("date", "")).date())
            except ValueError:
                continue
        if len(dates) >= 2:
            span = (max(dates) - min(dates)).days + 1
            capacity = span * int(daily)
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

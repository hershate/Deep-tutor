"""方案组装映射（err.md B13：pipeline 工具输出 -> 方案字段的单一声明式来源）。

此前「工具输出如何聚合成方案」在 orchestrator._assemble 与
export_plan._assemble_from_upstream 各硬编码一份，新增工具需同步改 4 处
（PIPELINE / FEEDBACK_TARGETS / 两份 assemble），漏改即静默丢失输出。
现统一为本模块的 PLAN_SECTIONS：新增方案组件工具只需在 PIPELINE 与此处登记。

parse_material / export_plan 不产方案内容（前者是输入解析、后者只产文件），
不在映射内属预期，assemble 时对未登记工具仅 debug 日志，不告警。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("deep_tutor.plan_assembly")

# 工具名 -> (方案字段名, 从该工具 data 提取字段值的函数)
PLAN_SECTIONS: dict[str, tuple[str, Callable[[dict[str, Any]], Any]]] = {
    "understand_intent": ("intent", lambda d: d),
    "build_knowledge_map": ("knowledge_map", lambda d: d),
    "design_stages": ("stages", lambda d: d.get("stages", [])),
    "search_resources": ("resources", lambda d: d.get("resources", [])),
    "schedule_plan": ("schedule", lambda d: d.get("schedule", [])),
    "validate_plan": ("validation_report", lambda d: d),
}

# 不产方案内容的 pipeline 工具（输入解析 / 文件导出），未登记不视为遗漏
NON_CONTENT_TOOLS = frozenset({"parse_material", "export_plan"})


def assemble_plan(outputs: dict[str, Any]) -> dict[str, Any]:
    """按 PLAN_SECTIONS 聚合工具输出 -> 方案 dict。

    outputs 的 value 为 ToolOutput（有 .data）或 {"data": ...} dict，两者兼容。
    """
    plan: dict[str, Any] = {}
    for tool_name, payload in outputs.items():
        data = payload.get("data") if isinstance(payload, dict) else getattr(payload, "data", None)
        if not data:
            continue
        section = PLAN_SECTIONS.get(tool_name)
        if section is None:
            if tool_name not in NON_CONTENT_TOOLS:
                # err.md B13：输出了内容但未登记进方案映射 -> 告警（此前静默丢失）
                log.warning("工具 %s 有输出但未登记 PLAN_SECTIONS，方案组装跳过", tool_name)
            continue
        field_name, extract = section
        plan[field_name] = extract(data)
    return plan

"""自研工具集（plan/02 工具契约、plan/08 数据交换格式）。

统一 I/O 信封（ToolInput/ToolOutput）见 app/tools/base.py。
P0 工具链于 step 4 逐工具落地；default_registry() 汇集已实现工具。
"""

from __future__ import annotations

from app.agent.registry import ToolRegistry
from app.tools.build_knowledge_map import build_knowledge_map_tool
from app.tools.design_stages import design_stages_tool
from app.tools.export_plan import export_plan_tool
from app.tools.parse_material import parse_material_tool
from app.tools.schedule_plan import schedule_plan_tool
from app.tools.search_resources import search_resources_tool
from app.tools.understand_intent import understand_intent_tool
from app.tools.validate_plan import validate_plan_tool
from app.tools.verify_url import verify_url_tool


def default_registry() -> ToolRegistry:
    """汇集已实现的 P0 工具。随 step 4 推进逐个加入。"""
    reg = ToolRegistry()
    reg.register(parse_material_tool)
    reg.register(understand_intent_tool)
    reg.register(build_knowledge_map_tool)
    reg.register(design_stages_tool)
    reg.register(search_resources_tool)
    reg.register(verify_url_tool)
    reg.register(schedule_plan_tool)
    reg.register(validate_plan_tool)
    reg.register(export_plan_tool)
    return reg


__all__ = [
    "default_registry",
    "parse_material_tool",
    "understand_intent_tool",
    "build_knowledge_map_tool",
    "design_stages_tool",
    "search_resources_tool",
    "verify_url_tool",
    "schedule_plan_tool",
    "validate_plan_tool",
    "export_plan_tool",
]

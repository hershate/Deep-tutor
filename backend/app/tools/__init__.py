"""自研工具集（plan/02 工具契约、plan/08 数据交换格式）。

统一 I/O 信封（ToolInput/ToolOutput）见 app/tools/base.py。
P0 工具链于 step 4 逐工具落地；default_registry() 汇集已实现工具。
"""

from __future__ import annotations

from app.agent.registry import ToolRegistry
from app.tools.build_knowledge_map import build_knowledge_map_tool
from app.tools.build_plan_slides import build_plan_slides_tool
from app.tools.design_stages import design_stages_tool
from app.tools.export_plan import export_plan_tool
from app.tools.generate_quiz import generate_quiz_tool
from app.tools.generate_study_materials import generate_study_materials_tool, generate_unit_content_tool
from app.tools.kb_answer import kb_answer_tool
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
    reg.register(build_plan_slides_tool)  # P1 slides（export format=slides 调用，不在 PIPELINE）
    reg.register(generate_study_materials_tool)  # P2 自学材料（POST /plans/{id}/materials 调用，不在 PIPELINE）
    reg.register(generate_unit_content_tool)  # P2 逐单元材料（materials SSE 流式路径，iter 46）
    reg.register(generate_quiz_tool)  # P2 测验（POST /plans/{id}/quiz 调用，不在 PIPELINE）
    reg.register(kb_answer_tool)  # iter 216 知识库问答（POST /kb/chat 调用，不在 PIPELINE）
    # 后端插件工具（API.md「后端插件 API」§工具）：插件经 api.tools.register 注册，
    # 随每次新建的注册表汇入。与核心工具同名 -> 覆盖并告警（registry.register 既有
    # 行为），插件可借此替换核心工具实现（高级用法，见 API.md 开发规范）。
    from app.plugins import runtime as plugin_runtime

    for plugin_tool in plugin_runtime.plugin_tools():
        reg.register(plugin_tool)

    # iter 235 W4b：集齐全部工具实例后，校验并行组同组工具互不读取对方输出
    # （约定从注释升级为 import 期强制；违例启动即炸而非线上静默读陈旧数据）。
    from app.agent.orchestrator import check_parallel_groups_safe

    check_parallel_groups_safe({name: reg.get(name) for name in reg.names()})
    return reg


# iter 317：核心工具的人类可读一行描述（api.tools.list / GET /api/tools 发现端点用）。
# 插件工具无对应描述（Tool 无 description 字段），发现输出中 description="" —— 插件作者
# 自知其工具用途；未来如需插件自带描述可给 Tool 加可选 description 字段。
TOOL_DESCRIPTIONS: dict[str, str] = {
    "parse_material": "多模态资料 → 结构化文本 + 元数据（file/text/url/image/audio/video）",
    "understand_intent": "抽取学习意图：主题/基础/目标/时长（信息不足时 needs_clarify）",
    "build_knowledge_map": "拆解知识点与依赖关系（nodes + edges）",
    "design_stages": "设计 3-5 渐进学习阶段（目标/知识点/里程碑）",
    "search_resources": "Web 检索学习资源 + 链接验证（✅⚠️❌）",
    "verify_url": "校验 URL 可达性与内容难度适配",
    "schedule_plan": "按时长/截止日排期学习日程",
    "validate_plan": "五维校验（依赖闭包/难度阶梯/认知负荷/里程碑/缺口）+ 修复建议",
    "export_plan": "导出方案为 md/pdf/ics",
    "build_plan_slides": "方案 → PPT 风格 HTML 幻灯片",
    "generate_study_materials": "方案 → 逐单元自学材料索引",
    "generate_unit_content": "生成单个自学单元的 Markdown 内容",
    "generate_quiz": "按知识点生成测验题（选择/填空/简答）",
    "kb_answer": "知识库问答（带引用出处）",
}


def tool_metadata(reg: ToolRegistry) -> list[dict]:
    """发现视图：注册表全部工具的元数据（name/llm/requires/description），供
    api.tools.list() 与 GET /api/tools 发现端点。插件据此动态组合工作流（iter 317）。"""
    out: list[dict] = []
    for name in reg.names():
        t = reg.get(name)
        out.append(
            {
                "name": name,
                "llm": bool(t.llm),
                "requires": list(t.requires),
                "description": TOOL_DESCRIPTIONS.get(name, ""),
            }
        )
    return out


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
    "build_plan_slides_tool",
    "generate_study_materials_tool",
    "generate_unit_content_tool",
    "generate_quiz_tool",
    "kb_answer_tool",
]

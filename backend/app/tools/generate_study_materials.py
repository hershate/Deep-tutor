"""generate_study_materials 工具（plan/02 §13，plan/07 §三，P2）。

学习方案 -> 逐单元自学材料（markdown）。复用 self-study-guide 方法论。
- LLM 生成 StudyMaterialsSpec（每单元 content_md 按 self-study-guide 模板），Python 渲染为 markdown。
- LLM 失败/无 provider -> 降级 default_materials_from_plan（纯 Python 默认）。

简化 MVP（同 build_plan_slides 模式）：一次 LLM 调用生成全部单元规约 -> render_materials_md
单文档 -> 前端 react-markdown。延后：DB 持久化/每单元文件/WebSearch/异步/StudyMaterialReader。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agent.registry import Tool

_DEFAULT_TITLE = "学习方案"


class StudyUnitSpec(BaseModel):
    """单个自学单元。content_md 为 markdown（概述/核心概念/实践练习/检查理解/总结/扩展阅读）。"""

    title: str
    objectives: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    estimated_time: str = ""  # 如 "45 min"
    content_md: str = ""


class StudyMaterialsSpec(BaseModel):
    """自学材料规约：LLM 输出 schema + 渲染输入。"""

    title: str = _DEFAULT_TITLE
    units: list[StudyUnitSpec]


def default_materials_from_plan(plan: dict[str, Any]) -> StudyMaterialsSpec:
    """从 plan 构造默认材料（降级用，纯 Python 确定性）：每阶段一单元，brief 内容。"""
    intent = plan.get("intent") or {}
    title = intent.get("topic") or _DEFAULT_TITLE
    units: list[StudyUnitSpec] = []

    for i, s in enumerate(plan.get("stages") or []):
        topics = s.get("topics") or []
        goal = s.get("goal", "")
        name = s.get("name", f"阶段 {i + 1}")
        weeks = s.get("estimated_weeks")
        objectives = [goal] if goal else []

        lines = ["### 概述", ""]
        if goal:
            lines.append(f"本单元学习「{name}」，目标：{goal}。")
        else:
            lines.append(f"本单元学习「{name}」。")
        lines.append("")
        if topics:
            lines.append("### 核心概念")
            lines.append("")
            for t in topics:
                lines.append(f"- {t.get('name', '')}")
            lines.append("")
        if s.get("milestone"):
            lines.append("### 里程碑")
            lines.append("")
            lines.append(s["milestone"])
            lines.append("")

        units.append(
            StudyUnitSpec(
                title=name,
                objectives=objectives,
                prerequisites=[],
                estimated_time=f"{weeks} 周" if weeks else "30-60 min",
                content_md="\n".join(lines),
            )
        )

    if not units:
        units.append(StudyUnitSpec(title="概述", content_md="(方案无阶段信息，无法生成自学材料)"))
    return StudyMaterialsSpec(title=title, units=units)


def render_materials_md(spec: StudyMaterialsSpec) -> str:
    """StudyMaterialsSpec -> 单 markdown 文档（前端 react-markdown 渲染）。"""
    lines = [f"# {spec.title}", "", f"> 共 {len(spec.units)} 个自学单元", ""]
    for i, u in enumerate(spec.units, 1):
        lines.append(f"## {i}. {u.title}")
        lines.append("")
        meta: list[str] = []
        if u.estimated_time:
            meta.append(f"**预计时长**：{u.estimated_time}")
        if u.objectives:
            meta.append(f"**学习目标**：{'; '.join(u.objectives)}")
        if u.prerequisites:
            meta.append(f"**前置知识**：{'; '.join(u.prerequisites)}")
        if meta:
            lines.append("  · ".join(meta))
            lines.append("")
        if u.content_md:
            lines.append(u.content_md)
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


# --- LLM 工具（iter 35）：plan -> StudyMaterialsSpec，复用 loop 修复链 ---

PROMPT = """你是 Deep-tutor 的「自学材料规约生成」工具。任务：从学习方案（args.plan）生成逐单元自学材料规约（StudyMaterialsSpec JSON），供渲染为单个 markdown 文档。非交互，直接输出规约。

输入：user message 的 args.plan（学习方案 dict，含 intent{topic,level,goal,daily_minutes}、stages[{index,name,goal,difficulty,estimated_weeks,topics[{name,why,must_learn}],milestone}]、resources[{title,url,note}]）。args.depth 可选 brief|full（默认 full；brief 时 content_md 精简为概述+要点）。

仅返回符合如下 schema 的 JSON 对象（不要 markdown 代码块、不要解释）：
{"title":"方案标题","units":[{"title":"单元名","objectives":["学完后能够..."],"prerequisites":["前置知识"],"estimated_time":"45 min","content_md":"markdown 内容"}]}

规则：
- 单元来源 plan.stages：每阶段 1-3 单元，按阶段顺序排列，总 3-12 单元；每单元对应 30-60 分钟自学量。
- 每单元 2-4 个 objectives（以「学完后能够…」表述）；prerequisites 来自更早单元/阶段（第一单元可为 ["无"] 或基础知识）。
- content_md 为 markdown，用 ### 级小标题，依次包含：
  ### 概述（200-300 字：这是什么、为什么重要，日常语言）
  ### 核心概念（每概念：通俗解释（类比/日常场景）+ 要点 + 示例）
  ### 实践练习（基础练习 ≥2 个，由易到难；可附进阶挑战）
  ### 检查理解（2-4 个自测问题）
  ### 总结（150-200 字要点回顾）
  ### 扩展阅读（仅可引用 args.plan.resources 中已有的 url，标注来源；无合适资源时写「无外部资料参考」）
- 禁止编造 URL：扩展阅读只可引用 plan.resources 已给的 url，否则省略该节或标注「无外部资料参考」。
- 用自己的话组织内容，不粘贴资料原文；不编造 plan 中没有的知识点。
- 单元内容少时不强行填模板，保持简洁。

仅返回符合 schema 的 JSON 对象。
"""

# 工具降级默认（LLM 失败时的静态兜底；端点在非 ok 时改用 default_materials_from_plan(plan) 渲染）
_DEFAULT_MATERIALS_DATA = {
    "title": "学习方案",
    "units": [
        {
            "title": "概述",
            "objectives": [],
            "prerequisites": [],
            "estimated_time": "",
            "content_md": "（自学材料规约生成失败，已降级）",
        }
    ],
}


generate_study_materials_tool = Tool(
    name="generate_study_materials",
    llm=True,
    skill_name=None,  # 用聚焦 inline_prompt（SKILL.md 为交互式，不宜作 spec 生成 system prompt）
    output_model=StudyMaterialsSpec,
    inline_prompt=PROMPT,
    default_data=_DEFAULT_MATERIALS_DATA,
)

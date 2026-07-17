"""generate_study_materials 工具（plan/02 §13，plan/07 §三，P2）。

学习方案 -> 逐单元自学材料（markdown）。复用 self-study-guide 方法论。
- LLM 生成 StudyMaterialsSpec（每单元 content_md 按 self-study-guide 模板），Python 渲染为 markdown。
- LLM 失败/无 provider -> 降级 default_materials_from_plan（纯 Python 默认）。

iter 34：仅模型 + 默认 + 渲染（无 LLM）。LLM 工具 + 注册在 iter 35。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

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

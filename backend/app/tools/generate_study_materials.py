"""generate_study_materials 工具（plan/02 §13，plan/07 §三，P2）。

学习方案 -> 逐单元自学材料（markdown）。复用 self-study-guide 方法论。
- LLM 生成 StudyMaterialsSpec（每单元 content_md 按 self-study-guide 模板），Python 渲染为 markdown。
- LLM 失败/无 provider -> 降级 default_materials_from_plan（纯 Python 默认）。

两条生成路径：
- 同步（POST /plans/{id}/materials 默认）：generate_study_materials 一次 LLM 调用生成全部
  单元规约 -> render_materials_md 单文档 -> 前端 react-markdown。
- SSE 流式（Accept: text/event-stream，iter 46，plan/07 §三「每单元一次 LLM」）：按 plan.stages
  拆分，逐单元调 generate_unit_content；单单元失败 -> brief_unit_from_stage 降级简要大纲。

延后：WebSearch 补资料 / quiz 题源联动。
"""

from __future__ import annotations

import dataclasses
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


def _stage_to_brief_unit(s: dict[str, Any], index: int) -> StudyUnitSpec:
    """单阶段 -> 简要大纲单元（纯 Python；默认规约与单单元降级共用）。"""
    topics = s.get("topics") or []
    goal = s.get("goal", "")
    name = s.get("name", f"阶段 {index + 1}")
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

    return StudyUnitSpec(
        title=name,
        objectives=objectives,
        prerequisites=[],
        estimated_time=f"{weeks} 周" if weeks else "30-60 min",
        content_md="\n".join(lines),
    )


def brief_unit_from_stage(stage: dict[str, Any], fallback_title: str = "概述") -> StudyUnitSpec:
    """单单元降级简要大纲（plan/07 §三 errors：LLM 失败 -> 该单元降级为简要大纲，纯 Python）。"""
    if not stage:
        return StudyUnitSpec(
            title=fallback_title,
            content_md="### 概述\n\n（本单元内容生成失败，已降级为占位大纲，请刷新重试）",
        )
    return _stage_to_brief_unit(stage, 0)


def default_materials_from_plan(plan: dict[str, Any]) -> StudyMaterialsSpec:
    """从 plan 构造默认材料（降级用，纯 Python 确定性）：每阶段一单元，brief 内容。"""
    intent = plan.get("intent") or {}
    title = intent.get("topic") or _DEFAULT_TITLE
    units = [_stage_to_brief_unit(s, i) for i, s in enumerate(plan.get("stages") or [])]

    if not units:
        units.append(StudyUnitSpec(title="概述", content_md="(方案无阶段信息，无法生成自学材料)"))
    return StudyMaterialsSpec(title=title, units=units)


# --- iter 348：知识点驱动单元切分（节点 -> 自学单元）---

# 难度 -> 分钟代理（单元聚类目标 ~55 分钟）
_NODE_WEIGHT = {1: 15, 2: 25, 3: 40, 4: 55, 5: 60}
_UNIT_TARGET_MINUTES = 55  # 单元自学量上限（累积超此 -> 起新单元）
_UNIT_MAX_NODES = 3  # 单元最多聚合知识点数
_NODE_FALLBACK_MIN = 3  # 知识点 < 此数 -> 回退阶段制（避免单元过少）


@dataclasses.dataclass
class UnitPlan:
    """单单元生成计划（iter 348：知识点驱动切分）。

    nodes：本单元覆盖的知识点 dict 列表（含 name/difficulty/must_learn）。
    stage：所属阶段 dict（难度递进上下文 + brief 降级用）。stage_index/node_sids 供持久化。
    """

    title: str
    stage: dict[str, Any]
    stage_index: int | None
    nodes: list[dict[str, Any]]
    node_sids: list[str]


def _node_stage_index(node: dict[str, Any], stages: list[dict[str, Any]]) -> int | None:
    """知识点 -> 所属阶段 index（先按 stage.topics 名匹配；无匹配按难度就近归入）。"""
    name = node.get("name")
    for s in stages:
        if name and any(t.get("name") == name for t in (s.get("topics") or [])):
            return s.get("index")
    if not stages:
        return None
    nd = int(node.get("difficulty") or 1)
    best = min(stages, key=lambda s: abs(int(s.get("difficulty") or 1) - nd))
    return best.get("index")


def group_nodes_into_units(
    nodes: list[dict[str, Any]], stages: list[dict[str, Any]]
) -> list[UnitPlan]:
    """知识点 -> 自学单元（iter 348）：按 (阶段, 难度) 排序后聚类。

    - 难度 ≥4 的难点：单独成单元（值得聚焦，~55-60min）。
    - 易点：累积到 ~55 分钟或 3 个知识点成单元。
    单元标题取知识点名（多个用「、」连，超长截断 + 「等」）。
    知识点 < _NODE_FALLBACK_MIN -> 返回空列表（调用方回退阶段制，1 阶段=1 单元）。
    """
    if len(nodes) < _NODE_FALLBACK_MIN:
        return []

    stage_by_index = {s.get("index"): s for s in stages}

    def _key(n: dict[str, Any]):
        si = _node_stage_index(n, stages)
        return (si if si is not None else 1 << 30, int(n.get("difficulty") or 1))

    ordered = sorted(nodes, key=_key)
    plans: list[UnitPlan] = []
    buf: list[dict[str, Any]] = []
    buf_min = 0

    def _title(ns: list[dict[str, Any]]) -> str:
        names = [str(n.get("name") or "").strip() for n in ns if n.get("name")]
        if not names:
            return "综合"
        t = "、".join(names)
        return t if len(t) <= 30 else t[:29] + "等"

    def _flush() -> None:
        nonlocal buf, buf_min
        if not buf:
            return
        first = buf[0]
        si = _node_stage_index(first, stages)
        plans.append(
            UnitPlan(
                title=_title(buf),
                stage=stage_by_index.get(si) or (stages[0] if stages else {}),
                stage_index=si,
                nodes=list(buf),
                node_sids=[str(n.get("id") or "") for n in buf if n.get("id")],
            )
        )
        buf = []
        buf_min = 0

    for n in ordered:
        d = int(n.get("difficulty") or 1)
        w = _NODE_WEIGHT.get(d, 30)
        if buf and (d >= 4 or buf_min + w > _UNIT_TARGET_MINUTES or len(buf) >= _UNIT_MAX_NODES):
            _flush()
        buf.append(n)
        buf_min += w
    _flush()
    return plans


def brief_unit_from_unitplan(plan: UnitPlan, fallback_title: str = "概述") -> StudyUnitSpec:
    """单单元降级简要大纲（iter 348：列出单元覆盖的知识点 + 所属阶段目标）。"""
    stage = plan.stage or {}
    nodes = plan.nodes or []
    names = [n.get("name") for n in nodes if n.get("name")]
    title = plan.title or fallback_title
    goal = stage.get("goal", "")
    weeks = stage.get("estimated_weeks")
    lines = ["### 概述", ""]
    if names:
        lines.append(f"本单元学习：{'、'.join(names)}。")
    else:
        lines.append(f"本单元学习「{title}」。")
    if goal:
        lines.append(f"阶段目标：{goal}。")
    lines.append("")
    if names:
        lines.append("### 核心概念")
        lines.append("")
        for nm in names:
            lines.append(f"- {nm}")
        lines.append("")
    lines.append("（本单元内容生成失败，已降级为占位大纲，请刷新重试）")
    return StudyUnitSpec(
        title=title,
        objectives=[goal] if goal else [],
        prerequisites=[],
        estimated_time=f"{weeks} 周" if weeks else "30-60 min",
        content_md="\n".join(lines),
    )


_MODULE_SIZE = 5  # 单元过多(>10) -> 每 5 单元一模块（plan/02 §13 errors：分模块）


def render_materials_md(spec: StudyMaterialsSpec) -> str:
    """StudyMaterialsSpec -> 单 markdown 文档（前端 react-markdown 渲染）。

    单元 >10 时按 5 个一模块插入模块分组标题（plan/02 §13「单元过多 -> 分模块」）。
    """
    lines = [f"# {spec.title}", "", f"> 共 {len(spec.units)} 个自学单元", ""]
    modular = len(spec.units) > 10
    for i, u in enumerate(spec.units, 1):
        if modular and (i - 1) % _MODULE_SIZE == 0:
            end = min(i + _MODULE_SIZE - 1, len(spec.units))
            lines.append(f"## 模块 {(i - 1) // _MODULE_SIZE + 1}（单元 {i}-{end}）")
            lines.append("")
        lines.append(f"{'###' if modular else '##'} {i}. {u.title}")
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


def build_materials_index(plan: dict[str, Any], spec: StudyMaterialsSpec) -> str:
    """index 文档（plan/02 §13 output index_url / plan/07 §三 步骤 4）：
    路线图（单元表）+ 计划表（plan.schedule）+ 复习节点（单元标题 + 必学知识点）。
    """
    intent = plan.get("intent") or {}
    title = spec.title or intent.get("topic") or _DEFAULT_TITLE
    lines = [f"# {title} · 自学路线图", ""]

    lines.append("## 路线图")
    lines.append("")
    lines.append("| # | 单元 | 预计时长 | 前置 | 目标 |")
    lines.append("|---|---|---|---|---|")
    for i, u in enumerate(spec.units, 1):
        prereq = "、".join(u.prerequisites) or "无"
        objectives = "；".join(u.objectives) or "—"
        lines.append(f"| {i} | {u.title} | {u.estimated_time or '—'} | {prereq} | {objectives} |")
    lines.append("")

    schedule = plan.get("schedule") or []
    if schedule:
        lines.append("## 计划表")
        lines.append("")
        lines.append("| 日期 | 阶段 | 任务 | 预计分钟 |")
        lines.append("|---|---|---|---|")
        for d in schedule:
            tasks = "、".join(d.get("tasks", []))
            lines.append(f"| {d.get('date', '')} | {d.get('stage_index', '')} | {tasks} | {d.get('est_minutes', '')} |")
        lines.append("")

    lines.append("## 复习节点")
    lines.append("")
    for i, u in enumerate(spec.units, 1):
        lines.append(f"- [ ] 单元 {i}：{u.title}")
    must_nodes = [
        n.get("name")
        for n in (plan.get("knowledge_map") or {}).get("nodes", [])
        if n.get("must_learn") and n.get("name")
    ]
    if must_nodes:
        lines.append("")
        lines.append("必学知识点：" + "、".join(must_nodes))
    lines.append("")
    return "\n".join(lines)


# --- LLM 工具（iter 35）：plan -> StudyMaterialsSpec，复用 loop 修复链 ---

PROMPT = """你是 Deep-tutor 的「自学材料规约生成」工具。任务：从学习方案（args.plan）生成逐单元自学材料规约（StudyMaterialsSpec JSON），供渲染为单个 markdown 文档。非交互，直接输出规约。

输入：user message 的 args.plan（学习方案 dict，含 intent{topic,level,goal,daily_minutes}、knowledge_map.nodes[{id,name,difficulty,must_learn}]、stages[{index,name,goal,difficulty,estimated_weeks,topics[{name,why,must_learn}],milestone}]、resources[{title,url,note}]）。args.depth 可选 brief|full（默认 full；brief 时 content_md 精简为概述+要点）。

仅返回符合如下 schema 的 JSON 对象（不要 markdown 代码块、不要解释）：
{"title":"方案标题","units":[{"title":"单元名","objectives":["学完后能够..."],"prerequisites":["前置知识"],"estimated_time":"45 min","content_md":"markdown 内容"}]}

规则：
- 单元来源：优先 plan.knowledge_map.nodes（若非空）——按知识点聚合切分，每单元 1-3 个相关知识点、约 30-60 分钟，单元数随知识点数量（通常 4-12；difficulty≥4 的难点单独成单元）；knowledge_map 为空或节点极少（<3）时回退 plan.stages（每阶段一单元）。按阶段/难度顺序排列。
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


# --- 逐单元生成（iter 46，plan/07 §三「每单元一次 LLM」；SSE 流式端点用）---


class UnitContentSpec(BaseModel):
    """单单元 LLM 输出（generate_unit_content）。title 由 stage 确定性给出，不入 LLM 输出。"""

    objectives: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    estimated_time: str = ""  # 如 "45 min"
    content_md: str = ""


UNIT_CONTENT_PROMPT = """你是 Deep-tutor 的「自学单元内容生成」工具。任务：为学习方案（args.plan）中的一个阶段（args.stage，第 args.order/args.total 个单元）生成单个自学单元的内容（UnitContentSpec JSON）。非交互，直接输出规约。

输入：user message 的 args.plan（学习方案 dict，含 intent{topic,level,goal,daily_minutes}、stages[{index,name,goal,difficulty,estimated_weeks,topics[{name,why,must_learn}],milestone}]、resources[{title,url,note}]）、args.unit_nodes（本单元要覆盖的知识点列表 [{name,difficulty,must_learn}]，优先以此为准）、args.stage（本单元所属阶段，作难度递进上下文）、args.order/args.total（单元序号/总数）、args.depth（brief|full，默认 full；brief 时 content_md 精简为概述+要点）。

仅返回符合如下 schema 的 JSON 对象（不要 markdown 代码块、不要解释）：
{"objectives":["学完后能够..."],"prerequisites":["前置知识"],"estimated_time":"45 min","content_md":"markdown 内容"}

规则：
- 本单元对应 30-60 分钟自学量；2-4 个 objectives（以「学完后能够…」表述）；prerequisites 来自更早单元/阶段（第一单元可为 ["无"] 或基础知识）。
- content_md 为 markdown，用 ### 级小标题，依次包含：
  ### 概述（200-300 字：这是什么、为什么重要，日常语言）
  ### 核心概念（每概念：通俗解释（类比/日常场景）+ 要点 + 示例；覆盖 args.unit_nodes 的每个知识点；args.unit_nodes 缺失时改覆盖 args.stage.topics）
  ### 实践练习（基础练习 ≥2 个，由易到难；可附进阶挑战）
  ### 检查理解（2-4 个自测问题）
  ### 总结（150-200 字要点回顾）
  ### 扩展阅读（仅可引用 args.plan.resources 中已有的 url，标注来源；无合适资源时写「无外部资料参考」）
- 禁止编造 URL：扩展阅读只可引用 plan.resources 已给的 url，否则省略该节或标注「无外部资料参考」。
- 用自己的话组织内容，不粘贴资料原文；不编造 plan 中没有的知识点。
- 单元内容少时不强行填模板，保持简洁。

仅返回符合 schema 的 JSON 对象。
"""

# 工具降级默认（端点在非 ok 时改用 brief_unit_from_stage(stage) 生成简要大纲）
_DEFAULT_UNIT_DATA = {"objectives": [], "prerequisites": [], "estimated_time": "", "content_md": ""}


generate_unit_content_tool = Tool(
    name="generate_unit_content",
    llm=True,
    skill_name=None,  # 同 generate_study_materials：聚焦 inline_prompt
    output_model=UnitContentSpec,
    inline_prompt=UNIT_CONTENT_PROMPT,
    default_data=_DEFAULT_UNIT_DATA,
)

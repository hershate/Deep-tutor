"""export_plan 工具（plan/02 §11，纯 Python）。

导出方案为 Markdown（MVP：md；pdf 在 P1，ics 在 P5）。
- 从 args.plan 取方案；缺则从 upstream（各工具输出）组装。
- 不支持格式 -> 降级为 md（plan/02 §11）。
- 写入 data/sessions/<sid>/exports/<plan_id>.md，返回 file_url。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent.context import ExecContext
from app.agent.registry import Tool
from app.tools.base import ToolError, ToolInput, ToolOutput


async def export_plan(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    args = inp.args
    fmt = args.get("format", "md")
    plan = args.get("plan") or _assemble_from_upstream(inp.upstream)
    if not plan:
        return _err("empty_plan", "无可导出的方案内容")

    # MVP 仅 md；其他格式降级为 md（pdf P1，ics P5）
    degraded_format = fmt not in {"md"}
    if degraded_format:
        fmt = "md"

    md = render_markdown(plan)
    sid = inp.session_id or "default"
    out_dir = Path(ctx.data_dir or "data") / "sessions" / sid / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_id = str(args.get("plan_id", "plan"))
    file_path = out_dir / f"{plan_id}.md"
    file_path.write_text(md, encoding="utf-8")

    data = {"file_url": str(file_path), "format": "md", "chars": len(md), "content": md, "filename": f"{plan_id}.md"}
    if degraded_format:
        return ToolOutput(
            tool="export_plan",
            status="repaired",
            data=data,
            errors=[ToolError(code="unsupported_format", message=f"格式 {args.get('format')} 暂不支持，已降级为 md")],
        )
    return ToolOutput(tool="export_plan", status="ok", data=data)


def _assemble_from_upstream(upstream: dict[str, Any]) -> dict[str, Any]:
    """从上游工具输出组装方案 dict（err.md B13：统一走 plan_assembly.PLAN_SECTIONS）。"""
    from app.agent.plan_assembly import assemble_plan

    return assemble_plan(upstream)


def render_markdown(plan: dict[str, Any]) -> str:
    """方案 dict -> 可读 Markdown。"""
    lines: list[str] = []
    intent = plan.get("intent") or {}
    title = intent.get("topic") or plan.get("title") or "学习方案"
    lines.append(f"# {title}\n")

    if intent:
        lines.append("## 学习意图\n")
        lines.append(f"- 主题：{intent.get('topic', '')}")
        if intent.get("level") is not None:
            lines.append(f"- 当前基础：{intent['level']}")
        if intent.get("goal") is not None:
            lines.append(f"- 学习目标：{intent['goal']}")
        if intent.get("daily_minutes") is not None:
            lines.append(f"- 每日时长：{intent['daily_minutes']} 分钟")
        if intent.get("deadline"):
            lines.append(f"- 截止日期：{intent['deadline']}")
        lines.append("")

    kmap = plan.get("knowledge_map")
    if kmap and kmap.get("nodes"):
        lines.append("## 知识图谱\n")
        for n in kmap.get("nodes", []):
            must = "★" if n.get("must_learn") else " "
            lines.append(f"- [{must}] {n.get('name', '')}（难度 {n.get('difficulty', '?')}）")
        lines.append("")

    stages = plan.get("stages")
    if stages:
        lines.append("## 学习阶段\n")
        for s in stages:
            lines.append(f"### 阶段 {s.get('index', '?')}：{s.get('name', '')}")
            lines.append(f"- 目标：{s.get('goal', '')}")
            lines.append(f"- 难度：{s.get('difficulty', '?')} / 预计 {s.get('estimated_weeks', '?')} 周")
            topics = s.get("topics") or []
            if topics:
                lines.append("- 知识点：")
                for t in topics:
                    must = "★" if t.get("must_learn") else " "
                    lines.append(f"  - [{must}] {t.get('name', '')}（{t.get('why', '')}）")
            if s.get("milestone"):
                lines.append(f"- 里程碑：{s['milestone']}")
            lines.append("")

    resources = plan.get("resources")
    if resources:
        lines.append("## 配套资源\n")
        for r in resources:
            label = {"ok": "✅", "uncertain": "⚠️", "invalid": "❌"}.get(r.get("verified"), "")
            lines.append(f"- {label} [{r.get('type', '')}] {r.get('title', '')}：{r.get('url', '')}")
            if r.get("note"):
                lines.append(f"  - {r['note']}")
        lines.append("")

    schedule = plan.get("schedule")
    if schedule:
        lines.append("## 时间安排\n")
        lines.append("| 日期 | 阶段 | 任务 | 预计分钟 |")
        lines.append("|---|---|---|---|")
        for d in schedule:
            tasks = "、".join(d.get("tasks", []))
            lines.append(f"| {d.get('date', '')} | {d.get('stage_index', '')} | {tasks} | {d.get('est_minutes', '')} |")
        lines.append("")

    quizzes = plan.get("quizzes")
    if quizzes:
        lines.append("## 测验题\n")
        for i, q in enumerate(quizzes, 1):
            if not isinstance(q, dict):
                continue
            lines.append(f"{i}. {q.get('question', '')}")
            for opt in q.get("options") or []:
                lines.append(f"   - {opt}")
            lines.append(f"   - 答案：{q.get('answer', '')}")
            if q.get("explanation"):
                lines.append(f"   - 解析：{q['explanation']}")
        lines.append("")

    report = plan.get("validation_report")
    if report:
        lines.append("## 校验报告\n")
        lines.append(f"- 总体：{'通过' if report.get('passed') else '未通过'}")
        if report.get("degraded"):
            # err.md B11：降级交付的方案明确标注未经自动校验
            lines.append("- ⚠️ 自动校验未执行（校验工具降级），方案未经核验，请人工复核")
        rep = report.get("report") or {}
        for key, val in rep.items():
            status = val.get("status") if isinstance(val, dict) else val
            details = val.get("details", "") if isinstance(val, dict) else ""
            lines.append(f"  - {key}: {status} {details}")
        fixes = report.get("fixes") or []
        if fixes:
            lines.append("- 修复建议：")
            for f in fixes:
                lines.append(f"  - {f}")
        lines.append("")

    return "\n".join(lines)


def _err(code: str, message: str) -> ToolOutput:
    return ToolOutput(tool="export_plan", status="error", errors=[ToolError(code=code, message=message)])


export_plan_tool = Tool(name="export_plan", llm=False, func=export_plan)

"""外层确定性编排器（plan/01 §三 两层执行模型 / plan/10 §一/§二/§六/§十一）。

固定顺序 pipeline：parse -> understand -> map -> stage -> resource -> schedule
-> validate（-> 反馈循环≤2）-> export。
- 每工具：字段覆盖检查 -> run_tool -> 成本/预算 -> SSE 事件
- understand_intent.needs_clarify -> paused_clarify（持久化部分，step 6 续接）
- validate_plan.passed=false -> 据 report 重跑 design_stages/build_knowledge_map（反馈循环）
- 预算超限 / 工具 error / 取消 -> 中断保留部分
- 状态机持久化（orchestrator_state）与 SSE/HTTP 接入留 step 6；此处提供可单测的核心
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from app.agent.budget import BudgetExceeded
from app.agent.context import ExecContext
from app.agent.loop import run_tool
from app.agent.registry import Tool, ToolRegistry
from app.tools.base import ToolError, ToolInput, ToolOutput

log = logging.getLogger("deep_tutor.orchestrator")

# 固定流水线顺序（plan/10 §一）
PIPELINE: list[str] = [
    "parse_material",
    "understand_intent",
    "build_knowledge_map",
    "design_stages",
    "search_resources",
    "schedule_plan",
    "validate_plan",
    "export_plan",
]

# 反馈循环：validate_plan.report 失败项 -> 重跑目标（plan/10 §六）
FEEDBACK_TARGETS: dict[str, str] = {
    "dependency_closure": "build_knowledge_map",
    "difficulty_step": "design_stages",
    "cognitive_load": "design_stages",
    "milestone_feasibility": "design_stages",
    "gap_analysis": "design_stages",
}

MAX_FEEDBACK_ROUNDS = 2  # 对齐 settings.max_regenerations

EventCb = Callable[[str, dict[str, Any]], None]


@dataclass
class OrchestratorResult:
    status: str  # done | paused_clarify | error | interrupted
    outputs: dict[str, ToolOutput] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def check_field_coverage(tool: Tool, outputs: dict[str, ToolOutput]) -> list[str]:
    """字段覆盖检查（plan/08 §五 L4）：tool.requires 的点号路径须存在于上游输出。

    路径形如 "understand_intent.topic" -> outputs["understand_intent"].data["topic"]。
    """
    missing: list[str] = []
    for req in tool.requires:
        up_tool, _, field_name = req.partition(".")
        up_out = outputs.get(up_tool)
        if up_out is None or up_out.status == "error":
            missing.append(req)
            continue
        if field_name and field_name not in (up_out.data or {}):
            missing.append(req)
    return missing


def _is_fail(val: Any) -> bool:
    if val is False:
        return True
    if isinstance(val, str):
        return val.lower() in {"fail", "failed", "error"}
    if isinstance(val, dict):
        return val.get("passed") is False or val.get("status") in {"fail", "failed", "error"}
    return False


class Orchestrator:
    def __init__(
        self,
        registry: ToolRegistry,
        ctx: ExecContext,
        event_cb: EventCb | None = None,
        max_tool_calls: int = 20,
    ) -> None:
        self.registry = registry
        self.ctx = ctx
        self.event_cb = event_cb
        self.max_tool_calls = max_tool_calls

    # ---- 公开入口 ----

    async def run(self, args_by_tool: dict[str, dict[str, Any]]) -> OrchestratorResult:
        outputs: dict[str, ToolOutput] = {}
        errors: list[str] = []
        call_count = 0

        for name in PIPELINE:
            if not self.registry.has(name):
                continue  # 未注册的工具跳过（便于分步骤落地/测试）
            if call_count >= self.max_tool_calls:
                errors.append(f"工具调用轮次达上限 {self.max_tool_calls}")
                return OrchestratorResult("interrupted", outputs, self._assemble(outputs), errors)

            out = await self._exec(name, args_by_tool, outputs)
            outputs[name] = out
            call_count += 1

            # 预算
            if self.ctx.budget:
                self.ctx.budget.add(out.metadata.cost)
                if self.ctx.budget.exceeded():
                    self._emit("budget_exceeded", {"spent": self.ctx.budget.spent, "limit": self.ctx.budget.limit})
                    errors.append("budget_exceeded")
                    return OrchestratorResult("interrupted", outputs, self._assemble(outputs), errors)

            # 错误：中断保留部分（降级矩阵细化留 step 6）
            if out.status == "error":
                for e in out.errors:
                    errors.append(f"{name}: {e.code} - {e.message}")
                self._emit("error", {"code": out.errors[-1].code if out.errors else "error", "stage": name})
                return OrchestratorResult("error", outputs, self._assemble(outputs), errors)

            # 澄清：understand_intent 需澄清 -> 暂停
            if name == "understand_intent" and out.data.get("needs_clarify"):
                self._emit("clarify_needed", {"questions": out.data.get("clarify_questions", [])})
                return OrchestratorResult("paused_clarify", outputs, self._assemble(outputs), errors)

            # 反馈循环：validate_plan 未过 -> 重跑上游
            if name == "validate_plan":
                rounds = 0
                while not out.data.get("passed", True) and rounds < MAX_FEEDBACK_ROUNDS:
                    rounds += 1
                    target = self._feedback_target(out.data)
                    if not target or target not in PIPELINE:
                        break
                    self._emit("warn", {"code": "validation_failed", "message": f"反馈循环第 {rounds} 轮，重跑 {target}"})
                    reran = await self._rerun_from(target, args_by_tool, outputs, fixes=out.data.get("fixes", []))
                    call_count += reran
                    out = outputs.get("validate_plan", out)
                    if out.status == "error":
                        break
                if not out.data.get("passed", True):
                    self._emit("warn", {"code": "validation_unresolved", "message": "校验仍不通过，交付当前方案 + 报告"})

        return OrchestratorResult("done", outputs, self._assemble(outputs), errors)

    # ---- 内部 ----

    async def _exec(
        self,
        name: str,
        args_by_tool: dict[str, dict[str, Any]],
        outputs: dict[str, ToolOutput],
        fixes: list[str] | None = None,
    ) -> ToolOutput:
        tool = self.registry.get(name)
        # 字段覆盖检查
        missing = check_field_coverage(tool, outputs)
        if missing:
            self._emit("tool_start", {"tool": name})
            out = ToolOutput(
                tool=name,
                status="error",
                errors=[ToolError(code="missing_upstream_field", message=f"缺少上游字段: {missing}")],
            )
            self._emit("tool_end", {"tool": name, "status": "error", "metadata": {}})
            return out

        self._emit("tool_start", {"tool": name})
        inp = self._build_input(name, args_by_tool, outputs, fixes)
        out = await run_tool(name, inp, self.ctx)
        self._emit(
            "tool_end",
            {
                "tool": name,
                "status": out.status,
                "metadata": out.metadata.model_dump(),
            },
        )
        return out

    async def _rerun_from(
        self,
        target: str,
        args_by_tool: dict[str, dict[str, Any]],
        outputs: dict[str, ToolOutput],
        fixes: list[str],
    ) -> int:
        """从 target 重跑到 validate_plan（含），返回本次重跑的工具数。"""
        start = PIPELINE.index(target)
        end = PIPELINE.index("validate_plan")
        count = 0
        for name in PIPELINE[start : end + 1]:
            if not self.registry.has(name):
                continue
            out = await self._exec(name, args_by_tool, outputs, fixes=fixes if name == target else None)
            outputs[name] = out
            count += 1
            if self.ctx.budget:
                self.ctx.budget.add(out.metadata.cost)
            if out.status == "error":
                break
        return count

    def _build_input(
        self,
        name: str,
        args_by_tool: dict[str, dict[str, Any]],
        outputs: dict[str, ToolOutput],
        fixes: list[str] | None,
    ) -> ToolInput:
        args = dict(args_by_tool.get(name, {}))
        if fixes:
            args["fixes"] = fixes
        upstream = {t: {"data": o.data} for t, o in outputs.items()}
        return ToolInput(tool=name, session_id=self.ctx.session_id, args=args, upstream=upstream)

    def _feedback_target(self, validate_data: dict[str, Any]) -> str | None:
        report = validate_data.get("report") or {}
        for key, target in FEEDBACK_TARGETS.items():
            if _is_fail(report.get(key)):
                return target
        return None

    def _assemble(self, outputs: dict[str, ToolOutput]) -> dict[str, Any]:
        export = outputs.get("export_plan")
        if export and export.data:
            return dict(export.data)
        return {name: o.data for name, o in outputs.items()}

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        if self.event_cb:
            try:
                self.event_cb(event, data)
            except Exception:  # noqa: BLE001  事件回调不应影响编排
                log.exception("事件回调异常: %s", event)

"""外层确定性编排器（plan/01 §三 两层执行模型 / plan/10 §一/§二/§六/§十一）。

执行分组 pipeline：parse -> understand -> map -> stage -> [resource ∥ schedule]
-> validate（-> 反馈循环≤2）-> export。
- search_resources 与 schedule_plan 均仅依赖 design_stages 输出、彼此无上下游
  关系 -> 同组并发（iter 229 R2 并行段，消除串行 pipeline 最长 wall-clock 段）；
  组内顺序即 PIPELINE 顺序，错误报告/预算归集/输出写回保持确定性。
- 每工具：字段覆盖检查 -> run_tool -> 成本/预算 -> SSE 事件
- understand_intent.needs_clarify -> paused_clarify（持久化部分，step 6 续接）
- validate_plan.passed=false -> 据 report 重跑 design_stages/build_knowledge_map（反馈循环）
- 预算超限 / 工具 error / 取消 -> 中断保留部分
- 状态机持久化（orchestrator_state）与 SSE/HTTP 接入留 step 6；此处提供可单测的核心
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from app.agent.context import ExecContext
from app.agent.loop import run_tool
from app.agent.plan_assembly import PLAN_SECTIONS, assemble_plan
from app.agent.registry import Tool, ToolRegistry
from app.tools.base import ToolError, ToolInput, ToolOutput

log = logging.getLogger("deep_tutor.orchestrator")

# 流水线规范顺序（plan/10 §一）：步骤序号/局部重生成区间/反馈目标的唯一定义；
# 实际执行按 _PARALLEL_GROUPS 分组（同序等集，静态断言保证）。
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

# 执行分组（iter 229 R2 并行段）：search_resources 与 schedule_plan 均仅依赖
# design_stages 输出（B21 主题派生 / 阶段+用户画像排程），彼此无上下游关系 ->
# 同组并发执行；组内顺序即 PIPELINE 顺序（错误报告/预算归集/写回确定性）。
# 新增工具时默认独立成组（串行）；仅当与相邻工具**确认无依赖**才可同组。
_PARALLEL_GROUPS: list[list[str]] = [
    ["parse_material"],
    ["understand_intent"],
    ["build_knowledge_map"],
    ["design_stages"],
    ["search_resources", "schedule_plan"],  # R2 并行段
    ["validate_plan"],
    ["export_plan"],
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

# err.md B13：静态一致性自检（新增工具漏登记时尽早暴露，而非静默丢输出/反馈无效）
assert set(FEEDBACK_TARGETS.values()) <= set(PIPELINE), "FEEDBACK_TARGETS 指向不在 PIPELINE 的工具"
assert set(PLAN_SECTIONS) <= set(PIPELINE), "PLAN_SECTIONS 登记了不在 PIPELINE 的工具"
assert [t for g in _PARALLEL_GROUPS for t in g] == PIPELINE, "执行分组须与 PIPELINE 同序等集"


def check_parallel_groups_safe(tools: dict[str, Tool]) -> None:
    """校验 _PARALLEL_GROUPS 的并行安全性（iter 235 W4b）：同组工具互不读取对方输出。

    并行组成员共享同一 outputs 快照、并发执行——若某成员的上游读取（Tool.upstream_reads，
    声明式事实源）指向同组另一成员，它会读到「快照里尚未写入/陈旧」的数据，且无任何
    运行时拦截。此前该约束仅靠 _PARALLEL_GROUPS 上方注释约定，新人误加依赖型工具进
    并行组会静默出错。本函数把约定升级为 import 期强制（由 default_registry() 在集齐
    工具实例后调用），违例 -> ValueError 启动即炸，而非线上静默错数据。

    tools：工具名 -> Tool 实例（须至少覆盖所有多成员组的组员；缺省 upstream_reads 视为
    不读上游，与 registry.Tool 默认一致）。
    """
    for group in _PARALLEL_GROUPS:
        if len(group) < 2:
            continue
        members = set(group)
        for name in group:
            tool = tools.get(name)
            reads = set(tool.upstream_reads) if tool is not None else set()
            conflict = reads & (members - {name})
            if conflict:
                raise ValueError(
                    f"并行组成员 {name} 读取同组工具输出 {sorted(conflict)}——"
                    f"并发执行下会读到缺失/陈旧数据；请将其移出并行组或消除该上游依赖"
                )


EventCb = Callable[[str, dict[str, Any]], None]


def _window_groups(start_tool: str | None, end_tool: str | None = None) -> list[list[str]]:
    """PIPELINE [start_tool..end_tool] 闭区间按执行分组切分（区间内过滤、保序）。

    start_tool 落在并行组中间时（如局部重生成 scope=schedule_plan），首组仅含
    该工具及同组后续成员——同组上游成员（search_resources）由 initial_upstream
    保留、不重跑（plan/10 §十三 重跑范围最小化）。
    """
    lo = PIPELINE.index(start_tool) if start_tool else 0
    hi = PIPELINE.index(end_tool) if end_tool is not None else len(PIPELINE) - 1
    window = PIPELINE[lo : hi + 1]
    return [[n for n in g if n in window] for g in _PARALLEL_GROUPS if any(n in window for n in g)]


@dataclass
class OrchestratorResult:
    status: str  # done | paused_clarify | error | interrupted
    outputs: dict[str, ToolOutput] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    # iter 243：本次运行**实际执行**的每次工具调用（执行序，含反馈循环被覆盖的
    # 早轮输出）——outputs 每工具只留最后一轮（_rerun_from 覆盖），若按 outputs
    # 落账则反馈重跑轮次的 token 不进 tool_calls/api_usage（日预算只信 api_usage
    # -> 成本黑洞）。持久化以此为准；outputs 仅供方案组装/部分态。
    executed: list[ToolOutput] = field(default_factory=list)


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
        # iter 243：执行台账（_exec 唯一追加点；同列表对象经 stream.executed 暴露，
        # 任务被取消/异常时 _finalize 仍能落账已执行部分）
        self.executed: list[ToolOutput] = []

    # ---- 公开入口 ----

    async def run(
        self,
        args_by_tool: dict[str, dict[str, Any]],
        start_tool: str | None = None,
        initial_upstream: dict[str, ToolOutput] | None = None,
    ) -> OrchestratorResult:
        """执行 pipeline。

        - start_tool：从该工具起执行（默认从头）；支持局部重生成/澄清续接（plan/10 §十三）。
        - initial_upstream：预填充的上游输出（如已解析的 materials），跳过其上游工具。
        """
        outputs: dict[str, ToolOutput] = dict(initial_upstream or {})
        errors: list[str] = []
        call_count = 0
        self.executed.clear()  # 复用实例重跑时重置台账（生产一实例一跑，测试可复用）

        # err.md B18：不在 PIPELINE 的 start_tool 原会静默回退从头执行，与语义不符
        if start_tool is not None and start_tool not in PIPELINE:
            msg = f"start_tool 不在 PIPELINE: {start_tool}"
            log.error(msg)
            return self._result("error", outputs, {}, [msg])
        for group in _window_groups(start_tool):
            names = [n for n in group if self.registry.has(n)]
            if not names:
                continue  # 未注册的工具跳过（便于分步骤落地/测试）
            if call_count >= self.max_tool_calls:
                errors.append(f"工具调用轮次达上限 {self.max_tool_calls}")
                return self._result("interrupted", outputs, self._assemble(outputs), errors)

            outs = await self._exec_group(names, args_by_tool, outputs)
            for n, o in zip(names, outs, strict=True):
                outputs[n] = o
            call_count += len(names)  # 并行组允许超出上限 ≤组内人数-1（两调用同时发起，软守卫语义不变）

            # 预算（组内成本全部归集后判定；并行组固有两调用已同时发起——超限判定
            # 较旧串行晚一个组内工具，超支有界且仍中断，iter 229 有意的并行代价）
            if self.ctx.budget:
                for o in outs:
                    self.ctx.budget.add(o.metadata.cost)
                if self.ctx.budget.exceeded():
                    self._emit("budget_exceeded", {"spent": self.ctx.budget.spent, "limit": self.ctx.budget.limit})
                    errors.append("budget_exceeded")
                    return self._result("interrupted", outputs, self._assemble(outputs), errors)

            # 错误：组内按 PIPELINE 顺序取首个 error 中断（确定性），保留部分
            for n, o in zip(names, outs, strict=True):
                if o.status == "error":
                    for e in o.errors:
                        errors.append(f"{n}: {e.code} - {e.message}")
                    self._emit("error", {"code": o.errors[-1].code if o.errors else "error", "stage": n})
                    return self._result("error", outputs, self._assemble(outputs), errors)

            # 澄清：understand_intent 需澄清 -> 暂停
            if "understand_intent" in names:
                intent_out = outputs["understand_intent"]
                if intent_out.data.get("needs_clarify"):
                    self._emit("clarify_needed", {"questions": intent_out.data.get("clarify_questions", [])})
                    return self._result("paused_clarify", outputs, self._assemble(outputs), errors)

            # 反馈循环：validate_plan 未过 -> 重跑上游
            if "validate_plan" in names:
                out = outputs["validate_plan"]
                rounds = 0
                while not out.data.get("passed", True) and rounds < MAX_FEEDBACK_ROUNDS:
                    rounds += 1
                    target = self._feedback_target(out.data)
                    if not target or target not in PIPELINE:
                        break
                    self._emit("warn", {"code": "validation_failed", "message": f"反馈循环第 {rounds} 轮，重跑 {target}"})
                    reran, rerun_err = await self._rerun_from(
                        target, args_by_tool, outputs, fixes=out.data.get("fixes", []), attempt=rounds + 1
                    )
                    call_count += reran
                    if rerun_err is not None:
                        # err.md B10：重跑链路工具 error 不得被吞——此前重跑未到达
                        # validate_plan 时取到旧的 ok 输出，pipeline 以 done 交付错误方案
                        for e in rerun_err.errors:
                            errors.append(f"feedback:{target}: {e.code} - {e.message}")
                        self._emit("error", {
                            "code": rerun_err.errors[-1].code if rerun_err.errors else "error",
                            "stage": f"feedback:{target}",
                        })
                        return self._result("error", outputs, self._assemble(outputs), errors)
                    out = outputs.get("validate_plan", out)
                    if out.status == "error":
                        break
                    if self.ctx.budget and self.ctx.budget.exceeded():
                        break  # 预算超限，停止反馈循环
                # 反馈循环中预算超限 -> 中断保留部分（先于 validation_unresolved 判定）
                if self.ctx.budget and self.ctx.budget.exceeded():
                    self._emit("budget_exceeded", {"spent": self.ctx.budget.spent, "limit": self.ctx.budget.limit})
                    errors.append("budget_exceeded")
                    return self._result("interrupted", outputs, self._assemble(outputs), errors)
                if not out.data.get("passed", True):
                    self._emit("warn", {"code": "validation_unresolved", "message": "校验仍不通过，交付当前方案 + 报告"})

            # plan/03 §一 SSE plan_chunk：每个工具组产出后广播当前部分方案，
            # 前端「查看部分」（plan/06 §四.3）生成中即可浏览已产出片段；
            # 事件经 _SessionStream 缓冲，断线重连自动重放最新片段。
            self._emit("plan_chunk", {"partial_plan": self._assemble(outputs)})

        return self._result("done", outputs, self._assemble(outputs), errors)

    # ---- 内部 ----

    def _result(
        self,
        status: str,
        outputs: dict[str, ToolOutput],
        plan: dict[str, Any],
        errors: list[str],
    ) -> OrchestratorResult:
        """组装结果：快照执行台账（list 复制——调用方持有时台账不再增长）。"""
        return OrchestratorResult(status, outputs, plan, errors, executed=list(self.executed))

    async def _exec(
        self,
        name: str,
        args_by_tool: dict[str, dict[str, Any]],
        outputs: dict[str, ToolOutput],
        fixes: list[str] | None = None,
        attempt: int = 1,
    ) -> ToolOutput:
        tool = self.registry.get(name)
        # iter 184 B2：事件携带步骤序号（前端进度条 i/N + 反馈循环重跑轮次标识）。
        # step_index 为 PIPELINE 全局序号（局部重生成如 design_stages 起 = 4/8，语义自洽）；
        # attempt：主流程首发 1，validate 反馈循环重跑 2/3。向后兼容（旧前端忽略新字段）。
        step = {"tool": name, "step_index": PIPELINE.index(name) + 1, "total_steps": len(PIPELINE), "attempt": attempt}
        # 字段覆盖检查
        missing = check_field_coverage(tool, outputs)
        if missing:
            self._emit("tool_start", dict(step))
            out = ToolOutput(
                tool=name,
                status="error",
                errors=[ToolError(code="missing_upstream_field", message=f"缺少上游字段: {missing}")],
            )
            self._emit("tool_end", {**step, "status": "error", "metadata": {}})
            self.executed.append(out)  # 未真正调用 LLM，但事件已发——台账留痕（零成本行）
            return out

        self._emit("tool_start", dict(step))
        inp = self._build_input(name, args_by_tool, outputs, fixes)
        out = await run_tool(name, inp, self.ctx)
        self._emit(
            "tool_end",
            {
                **step,
                "status": out.status,
                "metadata": out.metadata.model_dump(),
            },
        )
        self.executed.append(out)  # iter 243：执行台账（成本落账唯一事实源）
        return out

    async def _exec_group(
        self,
        names: list[str],
        args_by_tool: dict[str, dict[str, Any]],
        outputs: dict[str, ToolOutput],
        fixes_for: str | None = None,
        fixes: list[str] | None = None,
        attempt: int = 1,
    ) -> list[ToolOutput]:
        """执行一个工具组，返回与 names 顺序对齐的输出（iter 229 R2）。

        单工具组：直接 _exec（行为与旧串行逐字一致）。
        多工具组（并行段）：并发执行——组内工具互无依赖（_PARALLEL_GROUPS 静态约束），
        输入均基于同一 outputs 快照、写回由调用方按 PIPELINE 顺序进行；
        tool_start/tool_end 事件交错发出（前端按 (tool,attempt) 键控步骤，乱序安全；
        后端 live.current_tool 显示最后开始者，仅展示语义）。
        fixes 仅发给 fixes_for 指定的工具（反馈循环目标），其余为 None。
        """
        if len(names) == 1:
            n = names[0]
            return [await self._exec(n, args_by_tool, outputs, fixes=fixes if n == fixes_for else None, attempt=attempt)]
        return list(
            await asyncio.gather(
                *(
                    self._exec(n, args_by_tool, outputs, fixes=fixes if n == fixes_for else None, attempt=attempt)
                    for n in names
                )
            )
        )

    async def _rerun_from(
        self,
        target: str,
        args_by_tool: dict[str, dict[str, Any]],
        outputs: dict[str, ToolOutput],
        fixes: list[str],
        attempt: int = 2,
    ) -> tuple[int, ToolOutput | None]:
        """从 target 重跑到 validate_plan（含）；返回 (重跑工具数, 首个 error 输出或 None)。

        iter 229：与主循环同一分组执行——重跑区间覆盖并行段时 search/schedule 同样并发；
        error 按组内 PIPELINE 顺序取首个上抛（确定性），预算按组归集后判定。
        """
        count = 0
        for group in _window_groups(target, "validate_plan"):
            names = [n for n in group if self.registry.has(n)]
            if not names:
                continue
            outs = await self._exec_group(names, args_by_tool, outputs, fixes_for=target, fixes=fixes, attempt=attempt)
            for n, o in zip(names, outs, strict=True):
                outputs[n] = o
            count += len(names)
            for o in outs:
                if o.status == "error":
                    return count, o  # err.md B10：上抛给主循环按 error 中断，不得吞掉
            if self.ctx.budget:
                for o in outs:
                    self.ctx.budget.add(o.metadata.cost)
                if self.ctx.budget.exceeded():
                    break  # 预算超限，停止重跑（主循环将返回 interrupted）
        return count, None

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
        """组装完整方案（err.md B13：统一走 plan_assembly.PLAN_SECTIONS 声明式映射）。"""
        return assemble_plan(outputs)

    def _emit(self, event: str, data: dict[str, Any]) -> None:
        if self.event_cb:
            try:
                self.event_cb(event, data)
            except Exception:  # noqa: BLE001  事件回调不应影响编排
                log.exception("事件回调异常: %s", event)

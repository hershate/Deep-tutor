"""Agent 编排核心（plan/01 §三 两层执行模型、plan/10）。

- registry.Tool / ToolRegistry：工具注册表
- context.ExecContext / Timeouts：执行上下文
- budget.BudgetTracker / BudgetExceeded：预算守卫
- cost：成本归集（usage -> api_usage / metadata）
- loop.run_tool：内层工具调用循环（含工具调用修复）
- orchestrator.Orchestrator：外层确定性编排器（pipeline + 字段覆盖 + 反馈循环 + 事件）
"""

from app.agent.budget import BudgetExceeded, BudgetTracker
from app.agent.context import ExecContext, Timeouts
from app.agent.loop import REPAIR_MAX_RETRIES, run_tool
from app.agent.orchestrator import (
    FEEDBACK_TARGETS,
    MAX_FEEDBACK_ROUNDS,
    PIPELINE,
    Orchestrator,
    OrchestratorResult,
    check_field_coverage,
)
from app.agent.registry import Tool, ToolRegistry

__all__ = [
    "BudgetExceeded",
    "BudgetTracker",
    "ExecContext",
    "Timeouts",
    "REPAIR_MAX_RETRIES",
    "run_tool",
    "FEEDBACK_TARGETS",
    "MAX_FEEDBACK_ROUNDS",
    "PIPELINE",
    "Orchestrator",
    "OrchestratorResult",
    "check_field_coverage",
    "Tool",
    "ToolRegistry",
]

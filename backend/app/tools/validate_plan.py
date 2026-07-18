"""validate_plan 工具（plan/02 §10，LLM，Skill=deep-tutor-validate）。

5 项校验 + 修复建议。方法论移植 study-planner Step 6：
1. dependency_closure 依赖闭包（每阶段前置须在更早阶段出现）
2. difficulty_step 难度阶梯（相邻阶段难度差 ≤ 2）
3. cognitive_load 认知负荷（每阶段 3-6 知识点）
4. milestone_feasibility 里程碑可行性（仅用当前+已学知识可达成）
5. gap_analysis 新手盲点（无未解释术语/跳步）

report 各项 {status: pass|fail|warn, details}；passed=false 时 fixes 给可执行修复建议。
编排器据 report 失败项触发反馈循环（plan/10 §六）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agent.registry import Tool

PROMPT = """你是 Deep-tutor 的「方案校验」工具。任务：对完整学习方案做 5 项校验，产出报告与修复建议。

5 项校验：
1. dependency_closure：每阶段所需前置知识是否都在更早阶段出现。
2. difficulty_step：相邻阶段难度差是否 ≤ 2（不跳级）。
3. cognitive_load：每阶段知识点数是否 3-6 个。
4. milestone_feasibility：里程碑是否仅用当前及已学知识可达成。
5. gap_analysis：是否存在未解释术语或跳步（新手盲点）。

每项给 status(pass|fail|warn) 与 details（失败时具体说明）。
若任一 fail -> passed=false，并在 fixes 给可执行修复建议（如「在 Stage2 与 Stage3 间插入过渡阶段」）。
若全 pass -> passed=true，fixes 为空。

仅返回符合 schema 的 JSON 对象（不要 markdown 代码块）：
{"report":{"dependency_closure":{"status":"pass","details":""},"difficulty_step":{"status":"fail","details":"..."},
 "cognitive_load":{"status":"pass","details":""},"milestone_feasibility":{"status":"pass","details":""},
 "gap_analysis":{"status":"pass","details":""}},"passed":false,"fixes":["..."]}
"""


class CheckResult(BaseModel):
    status: str  # pass | fail | warn
    details: str = ""


class ValidationReport(BaseModel):
    dependency_closure: CheckResult
    difficulty_step: CheckResult
    cognitive_load: CheckResult
    milestone_feasibility: CheckResult
    gap_analysis: CheckResult


class ValidatePlanData(BaseModel):
    report: ValidationReport
    passed: bool
    fixes: list[str] = Field(default_factory=list)


_DEFAULT = {
    "report": {
        "dependency_closure": {"status": "warn", "details": "校验工具降级，未执行"},
        "difficulty_step": {"status": "warn", "details": "校验工具降级，未执行"},
        "cognitive_load": {"status": "warn", "details": "校验工具降级，未执行"},
        "milestone_feasibility": {"status": "warn", "details": "校验工具降级，未执行"},
        "gap_analysis": {"status": "warn", "details": "校验工具降级，未执行"},
    },
    "passed": True,
    # err.md B11：显式标记「未经校验」——passed=True 仅为不触发反馈循环，
    # 消费方（前端/导出）据 degraded 提示人工复核，而非当作校验通过
    "degraded": True,
    "fixes": ["校验工具降级，已跳过自动校验，请人工复核"],
}

validate_plan_tool = Tool(
    name="validate_plan",
    llm=True,
    skill_name="deep-tutor-validate",
    output_model=ValidatePlanData,
    inline_prompt=PROMPT,
    default_data=_DEFAULT,
)

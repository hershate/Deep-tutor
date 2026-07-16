"""design_stages 工具（plan/02 §4，LLM，Skill=deep-tutor-stages）。

设计 3-6 个渐进阶段。方法论移植 study-planner Step 3（渐进阶段、每阶段目标/3-6 知识点/里程碑）。
阶段数须 3-6（09 §二十）：用 pydantic validator 强制，不符 -> 校验失败 -> 工具调用修复重试。
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.agent.registry import Tool

PROMPT = """你是 Deep-tutor 的「学习阶段设计」工具。任务：依据知识图谱与学习意图，设计 3-6 个
渐进难度的学习阶段。

要求：
- stages：3-6 个阶段，按难度递增（difficulty 1-5）。
- 每阶段：index（从 0 起）、name、goal（该阶段目标）、difficulty(1-5)、estimated_weeks、
  topics（3-6 个知识点，每个含 name/why/must_learn）、milestone（里程碑，用已学知识可达成）。
- 阶段间难度差 ≤ 2（避免跳级）；前置知识须在更早阶段出现。
- milestone 必须仅用当前阶段及之前的知识可达成，不要依赖未来阶段。

仅返回符合 schema 的 JSON 对象（不要 markdown 代码块）：
{"stages":[{"index":0,"name":"...","goal":"...","difficulty":1,"estimated_weeks":1,
 "topics":[{"name":"...","why":"...","must_learn":true}],"milestone":"..."}]}
"""


class StageTopic(BaseModel):
    name: str
    why: str = ""
    must_learn: bool = False


class Stage(BaseModel):
    index: int
    name: str
    goal: str
    difficulty: int = 1
    estimated_weeks: float = 1.0
    topics: list[StageTopic] = Field(default_factory=list)
    milestone: str = ""


class StagesData(BaseModel):
    stages: list[Stage]

    @model_validator(mode="after")
    def _check_stage_count(self) -> "StagesData":
        if not (3 <= len(self.stages) <= 6):
            raise ValueError(f"阶段数须 3-6，当前 {len(self.stages)}")
        return self


_DEFAULT_STAGES = {
    "stages": [
        {"index": 0, "name": "入门", "goal": "建立基础", "difficulty": 1, "estimated_weeks": 1, "topics": [], "milestone": "理解基础概念"},
        {"index": 1, "name": "进阶", "goal": "深入核心", "difficulty": 3, "estimated_weeks": 2, "topics": [], "milestone": "完成核心练习"},
        {"index": 2, "name": "实战", "goal": "综合应用", "difficulty": 5, "estimated_weeks": 2, "topics": [], "milestone": "完成实战项目"},
    ]
}

design_stages_tool = Tool(
    name="design_stages",
    llm=True,
    skill_name="deep-tutor-stages",
    output_model=StagesData,
    inline_prompt=PROMPT,
    default_data=_DEFAULT_STAGES,
)

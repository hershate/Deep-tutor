"""generate_study_materials 渲染测试（iter 34，无 LLM）。"""

from __future__ import annotations

from pathlib import Path

from app.tools.generate_study_materials import (
    StudyMaterialsSpec,
    StudyUnitSpec,
    default_materials_from_plan,
    render_materials_md,
)

_PROJECT_SKILLS = Path(__file__).resolve().parents[2] / "skills"


def test_loader_loads_self_study_guide():
    """loader 扫描到 vendored self-study-guide。"""
    from app.skills.loader import load_skills

    reg = load_skills(_PROJECT_SKILLS)
    assert "self-study-guide" in reg


def test_render_basic_spec():
    spec = StudyMaterialsSpec(
        title="React 自学材料",
        units=[
            StudyUnitSpec(
                title="HTML 基础",
                objectives=["组织页面内容", "区分块级/行内"],
                prerequisites=["无"],
                estimated_time="45 min",
                content_md="### 概述\n\nHTML 是页面结构。\n\n### 实践\n\n- 写一个页面",
            ),
            StudyUnitSpec(title="JS 基础", content_md="### 概述\n\nJS 是交互逻辑。"),
        ],
    )
    md = render_materials_md(spec)
    assert "# React 自学材料" in md
    assert "共 2 个自学单元" in md
    assert "## 1. HTML 基础" in md
    assert "## 2. JS 基础" in md
    assert "**预计时长**：45 min" in md
    assert "**学习目标**：组织页面内容; 区分块级/行内" in md
    assert "**前置知识**：无" in md
    assert "HTML 是页面结构。" in md
    assert "---" in md


def test_render_default_from_plan():
    plan = {
        "intent": {"topic": "React"},
        "stages": [
            {
                "index": 0,
                "name": "基础",
                "goal": "掌握 JS",
                "estimated_weeks": 1,
                "topics": [{"name": "JS"}, {"name": "DOM"}],
                "milestone": "静态页",
            }
        ],
    }
    spec = default_materials_from_plan(plan)
    md = render_materials_md(spec)
    assert "# React" in md
    assert "## 1. 基础" in md
    assert "掌握 JS" in md  # goal in objectives + 概述
    assert "- JS" in md and "- DOM" in md  # topics
    assert "静态页" in md  # milestone
    assert "1 周" in md  # estimated_time


def test_default_empty_plan_fallback():
    """plan 无 stages -> 回退单单元占位，不崩。"""
    spec = default_materials_from_plan({})
    md = render_materials_md(spec)
    assert "## 1." in md  # 至少一单元


# --- LLM 工具（iter 35）：plan -> StudyMaterialsSpec，复用 run_tool 修复链 ---


import asyncio  # noqa: E402

from app.agent import ExecContext, ToolRegistry, run_tool  # noqa: E402
from app.llm.base import LLMProvider, LLMResponse, LLMUsage  # noqa: E402
from app.tools import default_registry  # noqa: E402
from app.tools.base import ToolInput  # noqa: E402
from app.tools.generate_study_materials import generate_study_materials_tool  # noqa: E402


class _ScriptedProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._r = list(responses)

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True) -> LLMResponse:
        return self._r.pop(0) if self._r else LLMResponse(content="{}", usage=LLMUsage(model="claude-sonnet-5"))


def _ctx(provider: LLMProvider) -> ExecContext:
    reg = ToolRegistry()
    reg.register(generate_study_materials_tool)
    return ExecContext(session_id="s", trace_id="t", registry=reg, llm_provider=provider)


def test_tool_registered_in_default_registry():
    reg = default_registry()
    assert reg.has("generate_study_materials")
    assert reg.get("generate_study_materials").llm is True


def test_tool_valid_spec():
    """LLM 返回合法 StudyMaterialsSpec JSON -> run_tool ok + data=spec。"""
    spec_json = (
        '{"title":"React 自学材料","units":['
        '{"title":"JS 基础","objectives":["学完后能够写基础函数"],"prerequisites":["无"],'
        '"estimated_time":"45 min","content_md":"### 概述\\n\\nJS 是交互逻辑。"},'
        '{"title":"React 组件","objectives":["学完后能够写函数组件"],"prerequisites":["JS 基础"],'
        '"estimated_time":"60 min","content_md":"### 概述\\n\\n组件是 UI 单元。"}]}'
    )
    provider = _ScriptedProvider([LLMResponse(content=spec_json, usage=LLMUsage(model="claude-sonnet-5"))])
    out = asyncio.run(
        run_tool(
            "generate_study_materials",
            ToolInput(tool="generate_study_materials", session_id="s", args={"plan": {"intent": {"topic": "React"}}}),
            _ctx(provider),
        )
    )
    assert out.status == "ok"
    assert out.data["title"] == "React 自学材料"
    assert len(out.data["units"]) == 2
    assert out.data["units"][0]["title"] == "JS 基础"
    assert out.data["units"][1]["prerequisites"] == ["JS 基础"]


def test_tool_invalid_degrades_to_default():
    """LLM 持续返回非法 JSON -> 修复重试耗尽 -> 降级 repaired + 默认规约。"""
    provider = _ScriptedProvider(
        [LLMResponse(content="not json", usage=LLMUsage(model="claude-sonnet-5"))] * 5
    )
    out = asyncio.run(
        run_tool(
            "generate_study_materials",
            ToolInput(tool="generate_study_materials", session_id="s", args={"plan": {}}),
            _ctx(provider),
        )
    )
    assert out.status == "repaired"
    assert out.data["title"] == "学习方案"  # 默认降级规约
    assert len(out.data["units"]) >= 1

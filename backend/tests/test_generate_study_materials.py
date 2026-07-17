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

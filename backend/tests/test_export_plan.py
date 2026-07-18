"""export_plan 工具测试。"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app.agent.context import ExecContext
from app.agent.loop import run_tool
from app.tools import default_registry
from app.tools.base import ToolInput


def _ctx(data_dir: str) -> ExecContext:
    return ExecContext(session_id="s", trace_id="t", registry=default_registry(), data_dir=data_dir)


def _run(args, data_dir, upstream=None):
    inp = ToolInput(tool="export_plan", session_id="s", args=args, upstream=upstream or {})
    return asyncio.run(run_tool("export_plan", inp, _ctx(data_dir)))


def _sample_plan():
    return {
        "intent": {"topic": "React", "level": "零基础", "goal": "做项目", "daily_minutes": 90, "deadline": None},
        "knowledge_map": {"nodes": [{"id": "k1", "name": "JS 基础", "difficulty": 1, "must_learn": True}], "edges": []},
        "stages": [{"index": 0, "name": "入门", "goal": "基础", "difficulty": 1, "estimated_weeks": 1,
                    "topics": [{"name": "JS", "why": "前置", "must_learn": True}], "milestone": "理解 JS"}],
        "resources": [{"title": "React 文档", "url": "https://react.dev", "type": "doc", "verified": "ok", "note": ""}],
        "schedule": [{"date": "2026-07-16", "stage_index": 0, "tasks": ["学 JS"], "est_minutes": 90}],
        "validation_report": {"report": {}, "passed": True, "fixes": []},
    }


def test_export_md_writes_file():
    with tempfile.TemporaryDirectory() as d:
        out = _run({"plan": _sample_plan(), "format": "md", "plan_id": "p1"}, d)
        assert out.status == "ok"
        assert out.data["format"] == "md"
        assert out.data["chars"] > 0
        fpath = Path(out.data["file_url"])
        assert fpath.exists()
        content = fpath.read_text(encoding="utf-8")
        assert "# React" in content
        assert "## 学习阶段" in content
        assert "React 文档" in content
        assert "## 时间安排" in content


def test_unsupported_format_degrades_to_md():
    with tempfile.TemporaryDirectory() as d:
        out = _run({"plan": _sample_plan(), "format": "pdf", "plan_id": "p2"}, d)
        assert out.status == "repaired"
        assert out.data["format"] == "md"
        assert out.errors[0].code == "unsupported_format"
        assert Path(out.data["file_url"]).exists()


def test_empty_plan_is_error():
    with tempfile.TemporaryDirectory() as d:
        out = _run({"format": "md"}, d, upstream={})
        assert out.status == "error"
        assert out.errors[0].code == "empty_plan"


def test_assemble_from_upstream():
    upstream = {
        "understand_intent": {"data": {"topic": "Go", "level": None, "goal": None, "daily_minutes": 60, "deadline": None, "needs_clarify": False, "clarify_questions": []}},
        "design_stages": {"data": {"stages": [{"index": 0, "name": "基础", "goal": "g", "difficulty": 1, "estimated_weeks": 1, "topics": [], "milestone": "m"}]}},
    }
    with tempfile.TemporaryDirectory() as d:
        out = _run({"format": "md", "plan_id": "p3"}, d, upstream=upstream)
        assert out.status == "ok"
        content = Path(out.data["file_url"]).read_text(encoding="utf-8")
        assert "# Go" in content
        assert "基础" in content


def test_export_md_returns_content_and_quizzes_section():
    """iter 73：md 正文随响应返回（前端 Blob 下载）；含 quizzes 时渲染测验题章节。"""
    import asyncio

    from app.agent.context import ExecContext
    from app.tools.base import ToolInput
    from app.tools.export_plan import export_plan

    plan = {
        "title": "T",
        "quizzes": [
            {"question": "Q1?", "options": ["A. x", "B. y"], "answer": "B. y", "explanation": "因为"},
            {"question": "Q2?", "answer": "简答"},
        ],
    }
    inp = ToolInput(tool="export_plan", session_id="s-content", args={"plan_id": "p1", "format": "md", "plan": plan})
    out = asyncio.run(export_plan(inp, ExecContext(session_id="s-content", trace_id="t", data_dir="data-test-tmp")))
    assert out.status == "ok"
    md = out.data["content"]
    assert out.data["filename"] == "p1.md"
    assert "## 测验题" in md
    assert "1. Q1?" in md and "A. x" in md and "答案：B. y" in md and "解析：因为" in md
    assert "2. Q2?" in md and "答案：简答" in md
    # 清理测试产物
    import shutil

    shutil.rmtree("data-test-tmp", ignore_errors=True)

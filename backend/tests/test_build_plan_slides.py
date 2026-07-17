"""build_plan_slides 渲染测试（iter 29，无 LLM）。

用仓库 vendored 的 base.html 模板测 render_slides_html + default_spec_from_plan。
"""

from __future__ import annotations

from pathlib import Path

from app.tools.build_plan_slides import (
    SlideContent,
    SlideSpec,
    default_spec_from_plan,
    render_slides_html,
)

_PROJECT_SKILLS = Path(__file__).resolve().parents[2] / "skills"
_BASE_HTML = (_PROJECT_SKILLS / "ppt-slide-builder" / "templates" / "base.html").read_text(encoding="utf-8")


def test_loader_loads_ppt_slide_builder():
    """loader 扫描到 vendored ppt-slide-builder（作模板/文档源）。"""
    from app.skills.loader import load_skills

    reg = load_skills(_PROJECT_SKILLS)
    assert "ppt-slide-builder" in reg


def test_render_basic_spec():
    spec = SlideSpec(
        title="React 学习方案",
        theme={"--bg": "#0a0e1a", "--accent": "#3b82f6"},
        transition="trans-slide-left",
        slides=[
            SlideContent(title="React", content_html="<p>零基础</p>", layout="center", animation="anim-fade-in"),
            SlideContent(title="阶段 1", content_html="<ul><li>JS</li></ul>", layout="left", animation="anim-slide-up"),
        ],
    )
    html = render_slides_html(spec, _BASE_HTML)
    # 标题占位符替换
    assert "React 学习方案" in html
    assert "{{PRESENTATION_TITLE}}" not in html
    assert "{{SLIDE_COUNT}}" not in html
    # 主题变量覆盖（--bg 改了，--bg-alt 不受影响）
    assert "--bg: #0a0e1a" in html
    assert "--accent: #3b82f6" in html
    assert "--bg-alt: #f8f9fa" in html  # 未覆盖，沿用模板默认
    # 过渡类
    assert 'class="presentation trans-slide-left"' in html
    # slide 计数 = 2
    assert 'id="slideTotal">2<' in html
    # 两页 section
    assert 'id="slide-1"' in html and 'id="slide-2"' in html
    assert 'class="slide active"' in html  # 首页 active
    assert 'aria-hidden="false"' in html and 'aria-hidden="true"' in html
    # h1 首页、h2 余页；content_html 原样插入
    assert "<h1" in html and "<h2" in html
    assert "<ul><li>JS</li></ul>" in html


def test_render_default_spec_from_plan():
    plan = {
        "intent": {"topic": "React", "level": "零基础", "goal": "做项目", "daily_minutes": 60},
        "stages": [
            {"index": 0, "name": "基础", "goal": "学 JS", "topics": [{"name": "JS"}], "milestone": "静态页"}
        ],
        "resources": [{"title": "React 文档"}],
        "schedule": [{"date": "2026-07-17", "tasks": ["学 JS"]}],
        "validation_report": {"passed": True},
    }
    spec = default_spec_from_plan(plan)
    html = render_slides_html(spec, _BASE_HTML)
    assert "React" in html
    assert "阶段 1：基础" in html
    assert "配套资源" in html
    assert "时间安排" in html
    assert "校验报告" in html
    assert 'id="slide-1"' in html
    assert "--bg: #0f172a" in html  # 默认深色主题


def test_render_empty_slides_fallback():
    """空 slides -> 回退单页占位，不崩。"""
    spec = SlideSpec(title="空", theme={}, transition="", slides=[])
    html = render_slides_html(spec, _BASE_HTML)
    assert 'id="slide-1"' in html


def test_render_escapes_title_not_content():
    """title 纯文本转义；content_html 原样（不转义）。"""
    spec = SlideSpec(
        title="<script>x</script>",
        slides=[SlideContent(title="<b>", content_html="<p>ok</p>")],
    )
    html = render_slides_html(spec, _BASE_HTML)
    assert "&lt;script&gt;" in html  # title 转义
    assert "<p>ok</p>" in html  # content_html 不转义

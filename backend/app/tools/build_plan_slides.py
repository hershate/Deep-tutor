"""build_plan_slides 工具（plan/02 §12，P1 输出增强）。

学习方案 -> PPT 风格自包含 HTML 幻灯片。复用 ppt-slide-builder 的 templates/base.html。
- LLM 生成幻灯片规约（SlideSpec：主题 CSS 变量 + 每页内容 + 动画/过渡类），Python 渲染。
- LLM 失败/无 provider -> 降级 default_spec_from_plan（纯 Python 默认 deck）。

iter 29：仅模型 + 默认规约 + 渲染（无 LLM 调用，纯基础）。LLM 调用 + 工具注册在 iter 30。
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.agent.registry import Tool

# 渲染所用模板（vendor 自 ~/.claude/skills/ppt-slide-builder）
TEMPLATE_REL = Path("ppt-slide-builder") / "templates" / "base.html"

# 默认主题（覆盖 base.html 的 :root 变量；未列出的沿用模板默认）
_DEFAULT_THEME: dict[str, str] = {
    "--bg": "#0f172a",  # 深色背景，演示感
    "--bg-alt": "#1e293b",
    "--text": "#e2e8f0",
    "--text-secondary": "#94a3b8",
    "--accent": "#3b82f6",
    "--accent-hover": "#2563eb",
    "--border": "#334155",
    "--font-display": "'Georgia', serif",
    "--font-body": "system-ui, sans-serif",
}


class SlideContent(BaseModel):
    """单页幻灯片内容。content_html 为可嵌入 HTML（由 LLM 生成或默认构造）。"""

    title: str = ""
    content_html: str = ""
    layout: str = "left"  # center | left | grid-2 | grid-3
    animation: str = "anim-fade-in"  # anim-* 类名


class SlideSpec(BaseModel):
    """幻灯片规约：LLM 输出 schema + 渲染输入。"""

    title: str
    theme: dict[str, str] = Field(default_factory=dict)  # CSS 变量 -> 值
    transition: str = "trans-fade"  # trans-* 类名
    slides: list[SlideContent]


def default_spec_from_plan(plan: dict[str, Any]) -> SlideSpec:
    """从 plan 构造默认 deck（降级用，纯 Python 确定性）。

    标题页 + 每阶段一页 + 资源页 + 排期页 + 校验页。
    """
    intent = plan.get("intent") or {}
    topic = intent.get("topic") or "学习方案"
    slides: list[SlideContent] = []

    # 标题页
    sub_parts = []
    if intent.get("level"):
        sub_parts.append(f"基础：{intent['level']}")
    if intent.get("goal"):
        sub_parts.append(f"目标：{intent['goal']}")
    if intent.get("daily_minutes"):
        sub_parts.append(f"每日：{intent['daily_minutes']} 分钟")
    subtitle = "<br>".join(sub_parts)
    slides.append(
        SlideContent(
            title=topic,
            content_html=f'<p style="font-size:1.3em;">{subtitle}</p>' if subtitle else "",
            layout="center",
            animation="anim-fade-in",
        )
    )

    # 每阶段一页
    for s in plan.get("stages") or []:
        topics_html = "".join(f"<li>{_esc(t.get('name', ''))}</li>" for t in (s.get("topics") or []))
        body = f"<p>{_esc(s.get('goal', ''))}</p>"
        if topics_html:
            body += f"<ul>{topics_html}</ul>"
        if s.get("milestone"):
            body += f'<p style="margin-top:1em;">里程碑：{_esc(s["milestone"])}</p>'
        slides.append(
            SlideContent(
                title=f"阶段 {s.get('index', 0) + 1}：{_esc(s.get('name', ''))}",
                content_html=body,
                layout="left",
                animation="anim-slide-up",
            )
        )

    # 资源页
    resources = plan.get("resources") or []
    if resources:
        items = "".join(f"<li>{_esc(r.get('title', ''))}</li>" for r in resources[:20])
        slides.append(SlideContent(title="配套资源", content_html=f"<ul>{items}</ul>", layout="left"))

    # 排期页
    schedule = plan.get("schedule") or []
    if schedule:
        rows = "".join(
            f"<li>{_esc(d.get('date', ''))}：{'、'.join(_esc(t) for t in d.get('tasks', []))}</li>"
            for d in schedule[:20]
        )
        slides.append(SlideContent(title="时间安排", content_html=f"<ul>{rows}</ul>", layout="left"))

    # 校验页
    report = plan.get("validation_report") or {}
    if report:
        passed = "通过 ✅" if report.get("passed") else "未通过 ⚠️"
        slides.append(
            SlideContent(title="校验报告", content_html=f"<p>总体：{passed}</p>", layout="center")
        )

    return SlideSpec(title=topic, theme=dict(_DEFAULT_THEME), transition="trans-fade", slides=slides)


def render_slides_html(spec: SlideSpec, base_template: str) -> str:
    """SlideSpec + base.html 模板 -> 自包含 HTML 字符串。

    逐 slide 容错：content_html 仅插入 section 内，不破全局结构；非法 CSS/类浏览器忽略。
    """
    if not spec.slides:
        spec = spec.model_copy(update={"slides": [SlideContent(title=spec.title or "学习方案", layout="center")]})

    html = base_template

    # 1. 主题 CSS 变量（仅覆盖 spec.theme 列出的）
    for var, val in spec.theme.items():
        pattern = re.compile(rf"({re.escape(var)}:\s*)[^;]+;")
        html = pattern.sub(lambda m, v=val: f"{m.group(1)}{v};", html)

    # 2. 过渡类（.presentation）
    if spec.transition:
        html = html.replace(
            '<div class="presentation" id="presentation">',
            f'<div class="presentation {spec.transition}" id="presentation">',
        )

    # 3. 占位符
    total = len(spec.slides)
    html = html.replace("{{PRESENTATION_TITLE}}", _esc(spec.title))
    html = html.replace("{{SLIDE_COUNT}}", str(total))
    html = html.replace("{{PROGRESS_PERCENT}}", f"{round(100 / total, 2)}")

    # 4. 生成 N 个 slide section，替换模板的 2 个示例 slide
    rendered = "\n\n".join(_render_slide(i + 1, s, i == 0) for i, s in enumerate(spec.slides))
    html = re.sub(r"<!-- SLIDE:.*<!-- /SLIDE -->", rendered, html, count=1, flags=re.DOTALL)
    return html


def _render_slide(idx: int, slide: SlideContent, is_first: bool) -> str:
    """渲染单个 slide section。"""
    tag = "h1" if is_first else "h2"
    active = " active" if is_first else ""
    hidden = "false" if is_first else "true"
    layout = slide.layout or "left"
    anim = slide.animation or "anim-fade-in"
    title_html = f'<{tag} class="{anim}">{_esc(slide.title)}</{tag}>' if slide.title else ""
    content = slide.content_html or ""
    return (
        f"  <!-- SLIDE: slide-{idx} -->\n"
        f'  <section id="slide-{idx}" class="slide{active}" data-slide="{idx}" '
        f'role="region" aria-label="Slide {idx}" aria-hidden="{hidden}">\n'
        f'    <div class="content-{layout}">\n'
        f"      {title_html}\n"
        f'      <div class="{anim} anim-delay-1">{content}</div>\n'
        f"    </div>\n"
        f"  </section>\n"
        f"  <!-- /SLIDE -->"
    )


def _esc(s: Any) -> str:
    """HTML 转义纯文本（content_html 不转义，仅 title 等纯文本字段）。"""
    return _html.escape(str(s), quote=True)


# --- LLM 工具（iter 30）：plan -> SlideSpec，复用 loop 修复链 ---

PROMPT = """你是 Deep-tutor 的「幻灯片规约生成」工具。任务：从学习方案（args.plan）生成 PPT 风格幻灯片规约（SlideSpec JSON），供渲染为自包含 HTML 幻灯片。非交互，直接输出规约。

输入：user message 的 args.plan（学习方案 dict，含 intent{topic,level,goal,daily_minutes}、stages[{index,name,goal,topics[{name}],milestone}]、resources[{title,url}]、schedule[{date,tasks}]、validation_report）。

仅返回符合如下 schema 的 JSON 对象（不要 markdown 代码块、不要解释）：
{"title":"方案标题","theme":{"--bg":"#色值","--bg-alt":"#色值","--text":"#色值","--text-secondary":"#色值","--accent":"#色值","--accent-hover":"#色值","--border":"#色值","--font-display":"字体栈","--font-body":"字体栈"},"transition":"trans-fade|trans-slide-left|trans-slide-right|trans-slide-up|trans-zoom|trans-flip|trans-none","slides":[{"title":"页标题","content_html":"HTML 内容","layout":"center|left|grid-2|grid-3","animation":"anim-fade-in|anim-slide-up|anim-slide-down|anim-slide-left|anim-slide-right|anim-scale-in|anim-rotate-in|anim-blur-in|anim-flip-in|anim-bounce-in"}]}

规则：
- 第 1 页标题页：layout=center，title=intent.topic，content_html 含 level/goal/daily_minutes 副标题。
- 每个学习阶段一页：title=阶段名，content_html 含 goal、知识点 <ul>、里程碑。
- 资源页、排期页、校验页各一页（按需，内容从 plan 取）。
- 总页数 5-15。
- content_html 仅用 <p>/<ul>/<ol>/<li>/<strong>/<em>/<br>；禁 <script>/<style>/<iframe>/<img>。
- theme 选合适配色（如深色科技 #0a0e1a/#3b82f6 或浅色商务 #ffffff/#1a1a1a）。
- animation/transition 用上述类名之一。
- 不编造 plan 中没有的内容；资料未体现的字段省略。

仅返回符合 schema 的 JSON 对象。
"""

# 工具降级默认（LLM 失败时的静态兜底；export 端点在非 ok 时会改用 default_spec_from_plan(plan) 渲染）
_DEFAULT_SPEC_DATA = {
    "title": "学习方案",
    "theme": {},
    "transition": "trans-fade",
    "slides": [
        {"title": "学习方案", "content_html": "<p>（幻灯片规约生成失败，已降级）</p>", "layout": "center", "animation": "anim-fade-in"}
    ],
}


build_plan_slides_tool = Tool(
    name="build_plan_slides",
    llm=True,
    skill_name=None,  # 用聚焦 inline_prompt（SKILL.md 为交互式，不宜作 spec 生成 system prompt）
    output_model=SlideSpec,
    inline_prompt=PROMPT,
    default_data=_DEFAULT_SPEC_DATA,
)

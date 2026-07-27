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


# --- 单元 -> 幻灯片（iter 359：自学单元逐个导出 HTML-PPT，确定性兜底）---


def _split_md_sections(md: str) -> list[tuple[str, str]]:
    """按 ### 小标题切分 markdown 为 [(heading, body)]。无 ### 小标题 -> []。

    单元 content_md 约定为「### 概述 / ### 核心概念 / ### 实践练习 / …」结构，
    每节自然成一页。代码块内的 # 不误判（``` 围栏内跳过）。
    """
    if not md:
        return []
    out: list[tuple[str, str]] = []
    in_code = False
    cur_head: str | None = None
    buf: list[str] = []
    for line in md.split("\n"):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            buf.append(line)
            continue
        if not in_code:
            m = re.match(r"^###\s+(.+?)\s*#*\s*$", line)
            if m:
                if cur_head is not None:
                    out.append((cur_head, "\n".join(buf).strip()))
                cur_head = m.group(1).strip()
                buf = []
                continue
        buf.append(line)
    if cur_head is not None:
        out.append((cur_head, "\n".join(buf).strip()))
    return out


def _inline_md(s: str) -> str:
    """内联 markdown -> 安全 HTML：先整体转义，再恢复 **粗体** 与 `行内代码`。"""
    s = _esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def _md_block_to_html(body: str) -> str:
    """简易 markdown -> HTML（确定性兜底用，非完整 md 解析器）：

    连续 `- / * / +` 列表项 -> <ul><li>；其余非空行 -> <p>；空行分隔块。
    足以把单元小节正文转为可演示的要点；LLM 路径产出更精炼 HTML。
    """
    if not body or not body.strip():
        return ""
    blocks: list[str] = []
    cur_list: list[str] = []

    def _flush() -> None:
        if cur_list:
            items = "".join(f"<li>{_inline_md(x)}</li>" for x in cur_list)
            blocks.append(f"<ul>{items}</ul>")
            cur_list.clear()

    for raw in body.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            _flush()
            continue
        m = re.match(r"^\s*[-*+]\s+(.+)$", line)
        if m:
            cur_list.append(m.group(1).strip())
            continue
        _flush()
        blocks.append(f"<p>{_inline_md(line.strip())}</p>")
    _flush()
    return "".join(blocks)


def default_spec_from_unit(title: str, content_md: str) -> SlideSpec:
    """从单个自学单元 markdown 构造默认 deck（降级用，纯 Python 确定性）。

    标题页 + 按 ### 小标题每节一页（无 ### 小标题时整段一页）。与 default_spec_from_plan
    同为 LLM 失败/无 key 时的稳定兜底，复用 _DEFAULT_THEME 深色主题。
    """
    slides: list[SlideContent] = [
        SlideContent(title=title or "自学单元", content_html="", layout="center", animation="anim-fade-in")
    ]
    sections = _split_md_sections(content_md)
    for heading, body in sections:
        slides.append(
            SlideContent(
                title=heading,
                content_html=_md_block_to_html(body),
                layout="left",
                animation="anim-slide-up",
            )
        )
    # 无 ### 小标题但有正文 -> 整段一页（避免只剩光秃秃标题页）
    if len(slides) == 1:
        body_html = _md_block_to_html(content_md)
        if body_html:
            slides.append(
                SlideContent(
                    title=title or "内容", content_html=body_html, layout="left", animation="anim-slide-up"
                )
            )
    return SlideSpec(title=title or "自学单元", theme=dict(_DEFAULT_THEME), transition="trans-fade", slides=slides)


def render_slides_html(spec: SlideSpec, base_template: str) -> str:
    """SlideSpec + base.html 模板 -> 自包含 HTML 字符串。

    逐 slide 容错：content_html 仅插入 section 内，不破全局结构；非法 CSS/类浏览器忽略。
    """
    if not spec.slides:
        spec = spec.model_copy(update={"slides": [SlideContent(title=spec.title or "学习方案", layout="center")]})

    html = base_template

    # 1. 主题 CSS 变量（仅覆盖 spec.theme 列出的；值净化防 CSS 越界）
    for var, val in spec.theme.items():
        pattern = re.compile(rf"({re.escape(var)}:\s*)[^;]+;")
        safe_val = _sanitize_theme_value(val)
        html = pattern.sub(lambda m, v=safe_val: f"{m.group(1)}{v};", html)

    # 2. 过渡类（.presentation；类名净化防属性越界）
    trans = _sanitize_class(spec.transition)
    if trans:
        html = html.replace(
            '<div class="presentation" id="presentation">',
            f'<div class="presentation {trans}" id="presentation">',
        )

    # 3. 占位符
    total = len(spec.slides)
    html = html.replace("{{PRESENTATION_TITLE}}", _esc(spec.title))
    html = html.replace("{{SLIDE_COUNT}}", str(total))
    html = html.replace("{{PROGRESS_PERCENT}}", f"{round(100 / total, 2)}")

    # 4. 生成 N 个 slide section，替换模板的 2 个示例 slide
    rendered = "\n\n".join(_render_slide(i + 1, s, i == 0) for i, s in enumerate(spec.slides))
    # 替换串必须走 lambda：字符串替换会把 rendered 当模板处理——LLM 内容含反斜杠
    # （LaTeX \alpha、Windows 路径 C:\new）时 \p 等抛 re.error 崩溃、\a 静默变响铃符
    html = re.sub(r"<!-- SLIDE:.*<!-- /SLIDE -->", lambda _m: rendered, html, count=1, flags=re.DOTALL)
    return html


def _render_slide(idx: int, slide: SlideContent, is_first: bool) -> str:
    """渲染单个 slide section。LLM 字段净化（content_html 去 XSS，类名防越界）。"""
    tag = "h1" if is_first else "h2"
    active = " active" if is_first else ""
    hidden = "false" if is_first else "true"
    layout = _sanitize_class(slide.layout) or "left"
    anim = _sanitize_class(slide.animation) or "anim-fade-in"
    title_html = f'<{tag} class="{anim}">{_esc(slide.title)}</{tag}>' if slide.title else ""
    content = _sanitize_content_html(slide.content_html or "")
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


# --- 防 XSS/CSS 注入（LLM 输出兜底净化）---
# content_html 去 script/style/iframe 等块与残留标签、on* 事件属性；
# 主题值去 <>{} 防 </style> 突破；类名仅留 alnum/-/_ 防 class 属性越界。
_SCRIPT_STYLE_BLOCK = re.compile(
    r"<\s*(script|style|iframe|object|embed|link|meta)\b[\s\S]*?<\s*/\s*\1\s*>", re.IGNORECASE
)
_UNSAFE_TAG = re.compile(r"<\s*/?\s*(script|style|iframe|object|embed|link|meta)\b[^>]*>", re.IGNORECASE)
_EVENT_ATTRS = re.compile(r'\s+on\w+\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)', re.IGNORECASE)
# iter 365：javascript:/vbscript: 链接 XSS 防护。浏览器解析 URL scheme 前会先剥离 ASCII
# 控制字符（tab/newline/CR/NUL 等，WHATWG URL spec），故 java&#9;cript: 经 unescape 成
# java\tscript: 后，旧字面正则（要求连续 "javascript:"）不匹配、链接保留 -> 浏览器剥 \t
# 后仍执行。改为匹配 href/src 属性值 -> 剥控制字符 -> 判危险 scheme。仅禁 javascript/vbscript
# （data:image/... 是合法图片内联，不禁）。
_URL_ATTR = re.compile(r'(href|src)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))', re.IGNORECASE)
_DANGEROUS_SCHEME = re.compile(r"^(javascript|vbscript):", re.IGNORECASE)
_CLASS_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


def _neutralize_url_attr(m: re.Match) -> str:
    """href/src 属性值：剥 ASCII 控制字符后判 javascript/vbscript -> 置 '#'，否则原样。"""
    attr = m.group(1)
    # group(3)=双引号值、group(4)=单引号值、group(5)=裸值
    val = m.group(3) if m.group(3) is not None else (m.group(4) if m.group(4) is not None else m.group(5))
    if _DANGEROUS_SCHEME.match(re.sub(r"[\x00-\x20\x7f]", "", val or "")):
        return f'{attr}="#"'
    return m.group(0)


def _sanitize_content_html(s: str) -> str:
    """清理 LLM 生成的 content_html：去危险块/标签、on* 事件属性与 javascript: 链接（防 XSS）。"""
    import html

    # iter 364：先解码 HTML 实体——浏览器属性值实体解码先于 URL scheme 解析，故
    # java&#115;cript: 经 _JS_URL_ATTR（字面 javascript:）不匹配后浏览器仍执行。解码后过滤覆盖
    s = html.unescape(s)
    s = _SCRIPT_STYLE_BLOCK.sub("", s)  # <script>...</script> 整块
    s = _UNSAFE_TAG.sub("", s)  # 残留开/闭危险标签
    s = _EVENT_ATTRS.sub("", s)  # on*= 事件属性
    s = _URL_ATTR.sub(_neutralize_url_attr, s)  # href/src 的 javascript:/vbscript:（剥控制字符后判）-> "#"
    return s


def _sanitize_class(s: str) -> str:
    """类名仅保留 alnum/-/_（防 class 属性越界注入，如 `foo" onclick=`）。"""
    return _CLASS_SAFE.sub("", s or "")


def _sanitize_theme_value(v: str) -> str:
    """主题值去 <>{} 防 CSS 越界（`</style>` 突破 style 块）。"""
    return re.sub(r"[<>{}]", "", v or "")


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


# --- 单元 -> 幻灯片 LLM 工具（iter 359：逐单元导出 HTML-PPT）---

UNIT_SLIDES_PROMPT = """你是 Deep-tutor 的「自学单元幻灯片规约生成」工具。任务：从单个自学单元的标题与 Markdown 正文（args.title / args.content_md）生成 PPT 风格幻灯片规约（SlideSpec JSON），供渲染为自包含 HTML 幻灯片。非交互，直接输出规约。

输入：user message 的 args.title（单元标题）、args.content_md（单元 markdown 正文，通常含 ### 概述 / ### 核心概念 / ### 实践练习 / ### 检查理解 / ### 总结 / ### 扩展阅读 等小节）。

仅返回符合如下 schema 的 JSON 对象（不要 markdown 代码块、不要解释）：
{"title":"单元标题","theme":{"--bg":"#色值","--bg-alt":"#色值","--text":"#色值","--text-secondary":"#色值","--accent":"#色值","--accent-hover":"#色值","--border":"#色值","--font-display":"字体栈","--font-body":"字体栈"},"transition":"trans-fade|trans-slide-left|trans-slide-right|trans-slide-up|trans-zoom|trans-flip|trans-none","slides":[{"title":"页标题","content_html":"HTML 内容","layout":"center|left|grid-2|grid-3","animation":"anim-fade-in|anim-slide-up|anim-slide-down|anim-slide-left|anim-slide-right|anim-scale-in|anim-rotate-in|anim-blur-in|anim-flip-in|anim-bounce-in"}]}

规则：
- 第 1 页标题页：layout=center，title=args.title。
- 按单元正文小节/逻辑切分为多页（通常 4-8 页），每页聚焦一个要点；勿把全部内容塞一页，也不要为凑页数拆得过碎。
- content_html 用 <p>/<ul>/<ol>/<li>/<strong>/<em>/<br> 把正文**提炼为演示要点**（精炼、可视化，勿整段照搬 markdown 长文）；禁 <script>/<style>/<iframe>/<img>。
- theme 选合适配色（深色科技 #0a0e1a/#3b82f6 或浅色商务 #ffffff/#1a1a1a）；animation/transition 用上述类名之一。
- 不编造 args.content_md 中没有的内容；保留关键示例与「检查理解」问题。
- 总页数 4-10。

仅返回符合 schema 的 JSON 对象。
"""

# 工具降级默认（LLM 失败时的静态兜底；端点在非 ok 时改用 default_spec_from_unit(title, content_md) 渲染）
_UNIT_DEFAULT_SPEC_DATA = {
    "title": "自学单元",
    "theme": {},
    "transition": "trans-fade",
    "slides": [
        {"title": "自学单元", "content_html": "<p>（幻灯片规约生成失败，已降级）</p>", "layout": "center", "animation": "anim-fade-in"}
    ],
}


build_unit_slides_tool = Tool(
    name="build_unit_slides",
    llm=True,
    skill_name=None,  # 同 build_plan_slides：聚焦 inline_prompt
    output_model=SlideSpec,
    inline_prompt=UNIT_SLIDES_PROMPT,
    default_data=_UNIT_DEFAULT_SPEC_DATA,
)


# --- 单元幻灯片审校（iter 359 续：用户反馈硬模板格式不对 -> LLM 检查并修正）---

REVIEW_UNIT_SLIDES_PROMPT = """你是 Deep-tutor 的「自学单元幻灯片审校」工具。任务：审查并修正一份从单元内容生成的幻灯片规约（SlideSpec）草稿，确保格式正确、内容忠实、可演示。非交互，直接输出修正后的规约。

输入：args.title（单元标题）、args.content_md（单元原始 markdown 正文）、args.draft_spec（已生成的 SlideSpec 草稿 JSON，含 title/theme/transition/slides[]，每个 slide 有 title/content_html/layout/animation）。

仅返回符合如下 schema 的 JSON 对象（修正后的完整 SlideSpec，不要 markdown 代码块、不要解释）：
{"title":"单元标题","theme":{"--bg":"#色值","--bg-alt":"#色值","--text":"#色值","--text-secondary":"#色值","--accent":"#色值","--accent-hover":"#色值","--border":"#色值","--font-display":"字体栈","--font-body":"字体栈"},"transition":"trans-fade|trans-slide-left|trans-slide-right|trans-slide-up|trans-zoom|trans-flip|trans-none","slides":[{"title":"页标题","content_html":"HTML 内容","layout":"center|left|grid-2|grid-3","animation":"anim-fade-in|anim-slide-up|anim-slide-down|anim-slide-left|anim-slide-right|anim-scale-in|anim-rotate-in|anim-blur-in|anim-flip-in|anim-bounce-in"}]}

审校与修正规则（务必逐条检查并改正）：
- **格式正确（最重要）**：每页 content_html 仅用 <p>/<ul>/<ol>/<li>/<strong>/<em>/<br>；**移除**任何 <script>/<style>/<iframe>/<object>/<embed>/<img>、on* 事件属性、javascript: 链接（防 XSS）。修正裸文本堆砌、标签未闭合、列表项缺失 <li> 包裹、段落缺 <p> 等格式错误。
- **内容忠实**：slides 覆盖 args.content_md 的关键要点（概述/核心概念/实践练习/检查理解/总结/扩展阅读），不编造、不遗漏重要内容；草稿漏了关键节则补上，重复或冗余则合并。
- **分页合理**：总页数 4-10；第 1 页标题页 layout=center；单页勿过载（长段落拆分或精炼为要点列表），也勿过碎。
- **主题/动画**：theme 配色合理（深色科技 #0a0e1a/#3b82f6 或浅色商务 #ffffff/#1a1a1a，全套变量给出）；animation/transition 用合法类名之一。
- 草稿已无明显问题时**原样返回**（不必为改而改）；仅在实际有格式/内容问题时才修正。

仅返回符合 schema 的 JSON 对象。
"""


review_unit_slides_tool = Tool(
    name="review_unit_slides",
    llm=True,
    skill_name=None,
    output_model=SlideSpec,
    inline_prompt=REVIEW_UNIT_SLIDES_PROMPT,
    default_data=_UNIT_DEFAULT_SPEC_DATA,  # 审校失败时调用方回退用草稿（见 generate_unit_slides_spec）
)


async def generate_unit_slides_spec(title: str, content_md: str, ctx, *, session_id: str, unit_id: str = ""):
    """LLM 生成 + 审校修正单元幻灯片规约（iter 359 续：硬模板格式不对 -> LLM 检查修正）。

    两阶段：build_unit_slides 生成草稿 -> review_unit_slides 审校修正（格式/内容/分页）。
    任一阶段成功即用 LLM 产物；两者皆败才用 default_spec_from_unit 确定性兜底。
    无 provider（ctx.llm_provider 为 None）-> 直接确定性兜底（零 LLM，离线/无 key 路径）。

    返回 (spec, metadatas, degraded)：
    - spec：最终 SlideSpec（LLM 审校 > LLM 草稿 > 确定性兜底）。
    - metadatas：build/review 的 ToolOutput.metadata 列表（供调用方记 api_usage；确定性路径为空）。
    - degraded：True 表示最终为确定性兜底（LLM 两阶段皆败），调用方可据此附降级告警。
    """
    from app.agent import run_tool
    from app.tools.base import ToolInput

    title = title or "自学单元"
    if getattr(ctx, "llm_provider", None) is None:
        return default_spec_from_unit(title, content_md), [], True

    metadatas = []
    build_out = await run_tool(
        "build_unit_slides",
        ToolInput(
            tool="build_unit_slides",
            session_id=session_id,
            args={"unit_id": unit_id, "title": title, "content_md": content_md},
        ),
        ctx,
    )
    metadatas.append(build_out.metadata)
    build_ok = build_out.status == "ok"
    draft = (
        SlideSpec.model_validate(build_out.data) if build_ok else default_spec_from_unit(title, content_md)
    )

    review_out = await run_tool(
        "review_unit_slides",
        ToolInput(
            tool="review_unit_slides",
            session_id=session_id,
            args={
                "unit_id": unit_id,
                "title": title,
                "content_md": content_md,
                "draft_spec": draft.model_dump(),
            },
        ),
        ctx,
    )
    metadatas.append(review_out.metadata)
    review_ok = review_out.status == "ok"
    spec = SlideSpec.model_validate(review_out.data) if review_ok else draft

    degraded = not build_ok and not review_ok
    return spec, metadatas, degraded

"""build_unit_slides 测试（iter 359：自学单元逐个导出 HTML-PPT）。

确定性兜底（default_spec_from_unit + markdown 切分/转 HTML 辅助）与 LLM 工具路径，
均不联网（_ScriptedProvider mock）。模板用仓库 vendored 的 ppt-slide-builder base.html。
"""

from __future__ import annotations

from pathlib import Path

from app.tools.build_plan_slides import (
    _md_block_to_html,
    _split_md_sections,
    build_unit_slides_tool,
    default_spec_from_unit,
    render_slides_html,
)

_PROJECT_SKILLS = Path(__file__).resolve().parents[2] / "skills"
_BASE_HTML = (_PROJECT_SKILLS / "ppt-slide-builder" / "templates" / "base.html").read_text(encoding="utf-8")

_UNIT_MD = """### 概述

这是单元概述，讲基本概念。

### 核心概念

- 概念一
- 概念二 **重点**

### 实践练习

完成下面的练习。

### 检查理解

什么是 X？
"""


# --- 确定性切分与 markdown -> HTML 辅助 ---


def test_split_md_sections_by_h3():
    secs = _split_md_sections(_UNIT_MD)
    assert [h for h, _ in secs] == ["概述", "核心概念", "实践练习", "检查理解"]
    # body 去除标题行、首尾空白
    assert "这是单元概述" in secs[0][1]
    assert "- 概念一" in secs[1][1]


def test_split_md_sections_empty():
    assert _split_md_sections("") == []
    assert _split_md_sections("无小标题的整段文本") == []


def test_split_md_sections_ignores_hash_in_code_fence():
    md = "### 概述\n\n正文\n\n```\n### 不是标题\n```\n\n### 真标题\n\n尾"
    secs = _split_md_sections(md)
    assert [h for h, _ in secs] == ["概述", "真标题"]


def test_md_block_to_html_lists_and_paragraphs():
    html = _md_block_to_html("- a\n- b\n\n段落文本\n\n- c")
    assert "<ul><li>a</li><li>b</li></ul>" in html
    assert "<p>段落文本</p>" in html
    assert html.endswith("<ul><li>c</li></ul>")


def test_md_block_to_html_inline_bold_and_code():
    html = _md_block_to_html("这是 **重点** 与 `code`")
    assert "<strong>重点</strong>" in html
    assert "<code>code</code>" in html


def test_md_block_to_html_escapes_raw_html():
    """原始 < > 被转义（_inline_md 先 _esc 再恢复内联标记）。"""
    html = _md_block_to_html("- <script>x</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_default_spec_from_unit_sections():
    spec = default_spec_from_unit("测试单元", _UNIT_MD)
    # 标题页 + 4 个小节页
    assert len(spec.slides) == 5
    assert spec.slides[0].title == "测试单元"
    assert spec.slides[0].layout == "center"
    assert [s.title for s in spec.slides[1:]] == ["概述", "核心概念", "实践练习", "检查理解"]
    # 概念页正文为列表 HTML
    assert "<ul><li>概念一</li>" in spec.slides[2].content_html
    assert "<strong>重点</strong>" in spec.slides[2].content_html
    # 默认深色主题
    assert spec.theme["--bg"] == "#0f172a"


def test_default_spec_from_unit_no_sections_whole_body():
    """无 ### 小标题但有正文 -> 标题页 + 整段一页（非光秃标题页）。"""
    spec = default_spec_from_unit("无小节单元", "整段正文，没有小标题。")
    assert len(spec.slides) == 2
    assert spec.slides[0].title == "无小节单元"
    assert "<p>整段正文" in spec.slides[1].content_html


def test_default_spec_from_unit_empty_content():
    """空正文 -> 仅标题页（不崩）。"""
    spec = default_spec_from_unit("空", "")
    assert len(spec.slides) == 1
    assert spec.slides[0].title == "空"


def test_render_unit_default_spec():
    spec = default_spec_from_unit("测试单元", _UNIT_MD)
    html = render_slides_html(spec, _BASE_HTML)
    assert "测试单元" in html
    assert "{{PRESENTATION_TITLE}}" not in html
    assert 'id="slide-1"' in html and 'id="slide-5"' in html  # 5 页
    assert "概念一" in html
    assert "--bg: #0f172a" in html  # 默认主题


# --- LLM 工具（iter 359）：unit markdown -> SlideSpec，复用 run_tool 修复链 ---

import asyncio  # noqa: E402

from app.agent import ExecContext, ToolRegistry, run_tool  # noqa: E402
from app.llm.base import LLMProvider, LLMResponse, LLMUsage  # noqa: E402
from app.tools import default_registry  # noqa: E402
from app.tools.base import ToolInput  # noqa: E402


class _ScriptedProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._r = list(responses)

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True) -> LLMResponse:
        return self._r.pop(0) if self._r else LLMResponse(content="{}", usage=LLMUsage(model="claude-sonnet-5"))


def _ctx(provider: LLMProvider) -> ExecContext:
    reg = ToolRegistry()
    reg.register(build_unit_slides_tool)
    return ExecContext(session_id="s", trace_id="t", registry=reg, llm_provider=provider)


def test_tool_registered_in_default_registry():
    reg = default_registry()
    assert reg.has("build_unit_slides")
    assert reg.get("build_unit_slides").llm is True


def test_tool_valid_spec():
    """LLM 返回合法 SlideSpec JSON -> run_tool ok + data=spec。"""
    spec_json = (
        '{"title":"概述单元","theme":{"--bg":"#0a0e1a"},"transition":"trans-fade",'
        '"slides":[{"title":"概述","content_html":"<p>要点</p>","layout":"center","animation":"anim-fade-in"}]}'
    )
    provider = _ScriptedProvider([LLMResponse(content=spec_json, usage=LLMUsage(model="claude-sonnet-5"))])
    out = asyncio.run(
        run_tool(
            "build_unit_slides",
            ToolInput(
                tool="build_unit_slides",
                session_id="s",
                args={"title": "概述单元", "content_md": "### 概述\n\n要点"},
            ),
            _ctx(provider),
        )
    )
    assert out.status == "ok"
    assert out.data["title"] == "概述单元"
    assert out.data["slides"][0]["title"] == "概述"
    assert out.data["theme"]["--bg"] == "#0a0e1a"


def test_tool_invalid_degrades_to_default():
    """LLM 持续返回非法 JSON -> 修复重试耗尽 -> 降级 repaired + 默认 spec。"""
    provider = _ScriptedProvider([LLMResponse(content="not json", usage=LLMUsage(model="claude-sonnet-5"))] * 5)
    out = asyncio.run(
        run_tool(
            "build_unit_slides",
            ToolInput(tool="build_unit_slides", session_id="s", args={"title": "T", "content_md": ""}),
            _ctx(provider),
        )
    )
    assert out.status == "repaired"
    assert out.data["title"] == "自学单元"  # 静态降级默认
    assert len(out.data["slides"]) >= 1


# --- review_unit_slides 审校工具 + generate_unit_slides_spec 两阶段编排（iter 359 续）---

from app.tools.build_plan_slides import (  # noqa: E402
    generate_unit_slides_spec,
    review_unit_slides_tool,
)

_DRAFT_JSON = (
    '{"title":"草稿单元","theme":{},"transition":"trans-fade",'
    '"slides":[{"title":"草稿","content_html":"<p>原始</p>","layout":"center","animation":"anim-fade-in"}]}'
)
_REVIEWED_JSON = (
    '{"title":"审校单元","theme":{"--bg":"#0a0e1a"},"transition":"trans-slide-left",'
    '"slides":[{"title":"审校后","content_html":"<p>已修正</p>","layout":"left","animation":"anim-slide-up"},'
    '{"title":"补充页","content_html":"<ul><li>要点</li></ul>","layout":"left","animation":"anim-fade-in"}]}'
)


def _ctx_both(provider: LLMProvider) -> ExecContext:
    reg = ToolRegistry()
    reg.register(build_unit_slides_tool)
    reg.register(review_unit_slides_tool)
    return ExecContext(session_id="s", trace_id="t", registry=reg, llm_provider=provider)


def test_review_tool_registered_in_default_registry():
    reg = default_registry()
    assert reg.has("review_unit_slides")
    assert reg.get("review_unit_slides").llm is True


def test_review_tool_returns_corrected_spec():
    """review 工具接收 draft_spec -> 返回修正后的 SlideSpec。"""
    provider = _ScriptedProvider([LLMResponse(content=_REVIEWED_JSON, usage=LLMUsage(model="claude-sonnet-5"))])
    out = asyncio.run(
        run_tool(
            "review_unit_slides",
            ToolInput(
                tool="review_unit_slides",
                session_id="s",
                args={"title": "T", "content_md": "### 概述\n\n要点", "draft_spec": {}},
            ),
            _ctx_both(provider),
        )
    )
    assert out.status == "ok"
    assert out.data["title"] == "审校单元"
    assert len(out.data["slides"]) == 2
    assert out.data["slides"][0]["title"] == "审校后"


def test_generate_helper_build_then_review():
    """两阶段皆成功 -> 用审校产物；metadatas 含 2 条；非降级。"""
    provider = _ScriptedProvider(
        [
            LLMResponse(content=_DRAFT_JSON, usage=LLMUsage(model="claude-sonnet-5")),
            LLMResponse(content=_REVIEWED_JSON, usage=LLMUsage(model="claude-sonnet-5")),
        ]
    )
    spec, metas, degraded = asyncio.run(
        generate_unit_slides_spec("单元", "### 概述\n\n要点", _ctx_both(provider), session_id="s", unit_id="u1")
    )
    assert spec.title == "审校单元"  # 用了 review 产物，非草稿
    assert len(spec.slides) == 2
    assert len(metas) == 2  # build + review
    assert degraded is False


def test_generate_helper_build_fail_review_ok():
    """build 失败（草稿走确定性）+ review 成功 -> 用 review 产物；非降级。"""
    provider = _ScriptedProvider(
        [
            LLMResponse(content="not json", usage=LLMUsage(model="claude-sonnet-5")),
            LLMResponse(content=_REVIEWED_JSON, usage=LLMUsage(model="claude-sonnet-5")),
        ]
    )
    spec, metas, degraded = asyncio.run(
        generate_unit_slides_spec("单元", "### 概述\n\n要点", _ctx_both(provider), session_id="s")
    )
    assert spec.title == "审校单元"  # review 救回
    assert degraded is False


def test_generate_helper_both_fail_degrades():
    """build 与 review 皆失败 -> 确定性兜底；degraded=True。"""
    provider = _ScriptedProvider(
        [LLMResponse(content="not json", usage=LLMUsage(model="claude-sonnet-5"))] * 10
    )
    spec, metas, degraded = asyncio.run(
        generate_unit_slides_spec("测试单元", "### 概述\n\n要点", _ctx_both(provider), session_id="s")
    )
    assert degraded is True
    assert spec.title == "测试单元"  # default_spec_from_unit 标题
    assert len(spec.slides) >= 1


def test_generate_helper_no_provider_deterministic():
    """无 provider（ctx.llm_provider=None）-> 直接确定性兜底，零 LLM（metadatas 空）。"""
    ctx = _ctx_both(None)  # provider=None
    spec, metas, degraded = asyncio.run(
        generate_unit_slides_spec("测试单元", "### 概述\n\n要点", ctx, session_id="s")
    )
    assert degraded is True
    assert metas == []  # 未调 LLM
    assert spec.title == "测试单元"

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


# --- iter 348：知识点驱动单元切分（group_nodes_into_units 纯函数）---

from app.tools.generate_study_materials import (  # noqa: E402
    UnitPlan,
    brief_unit_from_unitplan,
    group_nodes_into_units,
)

_STAGES = [
    {"index": 0, "name": "基础", "difficulty": 1, "topics": [{"name": "HTML"}, {"name": "CSS"}, {"name": "JS"}]},
    {"index": 1, "name": "进阶", "difficulty": 3, "topics": [{"name": "组件"}, {"name": "DOM"}]},
    {"index": 2, "name": "实战", "difficulty": 5, "topics": [{"name": "状态管理"}]},
]


def _node(i, name, d, ml=True):
    return {"id": f"k{i}", "name": name, "difficulty": d, "must_learn": ml}


def test_group_fallback_when_few_nodes():
    """知识点 <3 -> 返回空（调用方回退阶段制，保持旧行为）。"""
    assert group_nodes_into_units([_node(1, "x", 1)], _STAGES) == []
    assert group_nodes_into_units([_node(1, "x", 1), _node(2, "y", 2)], _STAGES) == []


def test_group_hard_nodes_become_solo_units():
    """difficulty≥4 的难点单独成单元。"""
    nodes = [
        _node(1, "HTML", 1), _node(2, "CSS", 1), _node(3, "JS", 2),
        _node(4, "组件", 4), _node(5, "Hooks", 4), _node(6, "状态管理", 5),
    ]
    plans = group_nodes_into_units(nodes, _STAGES)
    assert len(plans) >= 4  # 3 难点各 1 单元 + 易点聚合
    solo = [p for p in plans if len(p.nodes) == 1]
    hard_names = {"组件", "Hooks", "状态管理"}
    assert {p.nodes[0]["name"] for p in solo if p.nodes[0]["name"] in hard_names} == hard_names


def test_group_easy_nodes_batched_and_covered():
    """易点聚合（≥2 同单元）；全部节点被覆盖无遗漏。"""
    nodes = [_node(i, f"易点{i}", 1) for i in range(1, 5)]  # 4 个 d1 易点
    plans = group_nodes_into_units(nodes, _STAGES)
    assert len(plans) >= 2  # 每单元最多 3 节点 -> 4 点至少 2 单元
    assert any(len(p.nodes) >= 2 for p in plans)
    covered = {n["id"] for p in plans for n in p.nodes}
    assert covered == {f"k{i}" for i in range(1, 5)}


def test_group_node_sids_and_stage_index():
    """UnitPlan.node_sids / stage_index 正确（按 stage.topics 名匹配归阶段）。"""
    nodes = [_node(1, "HTML", 1), _node(2, "CSS", 1), _node(3, "组件", 4)]
    plans = group_nodes_into_units(nodes, _STAGES)
    by_sid = {sid: p for p in plans for sid in p.node_sids}
    assert by_sid["k1"].stage_index == 0
    assert by_sid["k2"].stage_index == 0
    assert by_sid["k3"].stage_index == 1


def test_group_unit_count_scales_with_nodes():
    """单元数随知识点数缩放（iter 348 核心诉求：不再固定 3）。"""
    small = group_nodes_into_units([_node(i, f"n{i}", 2) for i in range(1, 4)], _STAGES)
    large = group_nodes_into_units([_node(i, f"n{i}", 2) for i in range(1, 16)], _STAGES)
    assert len(large) > len(small)
    assert len(small) >= 1
    assert len(large) >= 5


def test_brief_unit_from_unitplan_lists_nodes():
    """降级简要大纲列出单元覆盖的知识点 + 阶段目标。"""
    plan = UnitPlan(
        title="HTML、CSS",
        stage={"goal": "掌握页面结构", "estimated_weeks": 1},
        stage_index=0,
        nodes=[{"name": "HTML"}, {"name": "CSS"}],
        node_sids=["k1", "k2"],
    )
    spec = brief_unit_from_unitplan(plan)
    assert "HTML" in spec.content_md and "CSS" in spec.content_md
    assert "本单元学习：" in spec.content_md
    assert "掌握页面结构" in spec.content_md


def test_brief_unit_from_unitplan_empty_nodes_uses_title():
    """stage 回退路径（nodes 空）-> 用标题 + 阶段目标。"""
    plan = UnitPlan(title="基础", stage={"goal": "g", "estimated_weeks": 2}, stage_index=0, nodes=[], node_sids=[])
    spec = brief_unit_from_unitplan(plan)
    assert "本单元学习「基础」" in spec.content_md
    assert "2 周" in spec.estimated_time


# --- iter 360（issue 3：引文相关性硬校验）---


def test_collect_material_urls_only_verified():
    """收集可引用 URL 池：仅显式 ok/uncertain 入池；invalid/缺失/None/未知一律排除（强制门宁缺勿滥）。"""
    from app.tools.generate_study_materials import collect_material_urls

    plan = {"resources": [
        {"url": "https://a.com/1", "verified": "ok"},
        {"url": "https://b.com/2", "verified": "uncertain"},
        {"url": "https://c.com/3", "verified": "invalid"},
        {"url": "https://d.com/4"},  # 缺失 verified -> 不入池
        {"url": "https://e.com/5", "verified": None},  # None -> 不入池
        {"url": "https://f.com/6", "verified": "unknown"},  # 未知值 -> 不入池
    ]}
    unit_res = [{"url": "https://g.com/7", "verified": "ok"}, {"url": "https://h.com/8", "verified": "invalid"}]
    pool = collect_material_urls(plan, unit_res)
    assert "https://a.com/1" in pool
    assert "https://b.com/2" in pool
    assert "https://g.com/7" in pool
    for blocked in ("https://c.com/3", "https://d.com/4", "https://e.com/5", "https://f.com/6", "https://h.com/8"):
        assert blocked not in pool
    # 末尾斜杠/fragment 容错 + scheme/host 小写规范化
    assert collect_material_urls({"resources": [{"url": "https://x.com/p/", "verified": "ok"}]}) == {"https://x.com/p"}


def test_sanitize_citations_strips_unverified_and_drops_bullet():
    """剥离白名单外的引用：列表项整行丢弃；行内链接降为纯文本。"""
    from app.tools.generate_study_materials import sanitize_citations

    allowed = {"https://good.example.com/intro"}
    md = (
        "### 扩展阅读\n"
        "- [好的教程](https://good.example.com/intro) - 官方\n"
        "- [臆造链接](https://fabricated.example.com/xyz) - 不应保留\n"
        "详见 [某文](https://other.example.com/a) 的说明。\n"
    )
    out = sanitize_citations(md, allowed)
    # 白名单内链接保留
    assert "https://good.example.com/intro" in out
    # 臆造/未验证链接被剥离（URL 不再出现）
    assert "fabricated.example.com" not in out
    assert "other.example.com" not in out
    # 行内链接降为纯文本（文字保留）
    assert "某文" in out
    # 列表项整行丢弃（不残留空占位）
    assert "臆造链接" not in out


def test_sanitize_citations_empty_pool_strips_all_links():
    """白名单为空（无已验证资源）-> 所有引用链接剥离。"""
    from app.tools.generate_study_materials import sanitize_citations

    md = "- [x](https://a.example.com/) - 1\n- [y](https://b.example.com/) - 2\n"
    out = sanitize_citations(md, set())
    assert "example.com" not in out
    assert out.strip() == ""  # 两个列表项全丢


def test_sanitize_citations_non_string_safe():
    from app.tools.generate_study_materials import sanitize_citations

    assert sanitize_citations("", {"x"}) == ""
    assert sanitize_citations("无链接正文", {"x"}) == "无链接正文"


def test_sanitize_citations_blocks_title_and_uppercase():
    """iter 363：标准链接带 title、大写 scheme/host 不再绕过（旧正则仅匹配小写无 title 形式）。"""
    from app.tools.generate_study_materials import sanitize_citations

    md = '- [a](https://evil.example.com/x "标题")\n- [b](HTTP://EVIL.example.com/y)\n'
    out = sanitize_citations(md, set())
    assert "evil.example.com" not in out
    assert "EVIL.example.com" not in out
    assert out.strip() == ""  # 两个列表项全丢


def test_sanitize_citations_blocks_autolink():
    """iter 363：autolink <url> 非白名单移除。"""
    from app.tools.generate_study_materials import sanitize_citations

    out = sanitize_citations("前 <https://evil.example.com/x> 后", set())
    assert "evil.example.com" not in out
    assert out.strip() == "前  后"


def test_sanitize_citations_blocks_link_def():
    """iter 363：引用定义 [id]: url 非白名单整行移除（否则 [id] 引用会复活链接）。"""
    from app.tools.generate_study_materials import sanitize_citations

    out = sanitize_citations("[1]: https://evil.example.com/x\n正文", set())
    assert "evil.example.com" not in out
    assert "[1]:" not in out
    assert "正文" in out


def test_sanitize_citations_neutralizes_bare_url():
    """iter 363：裸 URL（remark-gfm 自动链接）非白名单 -> 改行内代码（不可点击，URL 文字保留供参考）。"""
    from app.tools.generate_study_materials import sanitize_citations

    out = sanitize_citations("见 https://evil.example.com/x 了解", set())
    assert "`https://evil.example.com/x`" in out  # 被反引号包裹成行内代码


def test_sanitize_citations_preserves_code_urls():
    """iter 363：行内代码与代码块内的 URL 不被处理（本就不可点击，避免误伤正文/示例）。"""
    from app.tools.generate_study_materials import sanitize_citations

    md = "代码 `https://evil.example.com/x` 内\n```\nhttps://evil.example.com/y\n```\n"
    out = sanitize_citations(md, set())
    assert "`https://evil.example.com/x`" in out  # 行内代码原样
    assert "https://evil.example.com/y\n" in out  # 代码块内原样


def test_sanitize_citations_keeps_whitelist_across_forms():
    """iter 363：白名单 URL 在标准链接/autolink 形式下都保留可点击。"""
    from app.tools.generate_study_materials import sanitize_citations

    allowed = {"https://good.example.com/a"}
    md = "[g](https://good.example.com/a) 和 <https://good.example.com/a>"
    out = sanitize_citations(md, allowed)
    assert out.count("https://good.example.com/a") == 2

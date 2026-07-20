"""search_resources 工具测试。"""

from __future__ import annotations

import asyncio

from app.agent.context import ExecContext
from app.core import settings as settings_mod
from app.tools import default_registry
from app.tools import search_resources as sr_mod
from app.tools.base import ToolInput, ToolOutput
from app.tools.search_resources import search_resources, search_unit_resources


def _ctx():
    return ExecContext(session_id="s", trace_id="t", registry=default_registry())


def _run(args):
    inp = ToolInput(tool="search_resources", session_id="s", args=args)
    return asyncio.run(search_resources(inp, _ctx()))


def test_no_key_degraded(monkeypatch):
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "")
    out = _run({"stage_topics": [["React"], ["JS 基础"]]})
    assert out.status == "repaired"
    assert out.data["resources"] == []
    assert out.errors[0].code == "no_search_key"
    assert "React" in out.data["suggested_keywords"]


def test_aggregation_with_mocks(monkeypatch):
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")

    async def fake_bing(query, api_key, count=5):
        return [
            {"title": f"{query} 教程", "url": f"https://example.com/{query}"},
            {"title": f"{query} 文档", "url": f"https://example.com/doc/{query}"},
        ]

    async def fake_verify(inp, ctx):
        url = inp.args.get("url", "")
        return ToolOutput(
            tool="verify_url",
            status="ok",
            data={"ok": True, "status": 200, "suitable": True, "final_url": url, "label": "ok"},
        )

    monkeypatch.setattr(sr_mod, "_bing_search", fake_bing)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)

    out = _run({"stage_topics": [["React"], ["JS"]]})
    assert out.status == "ok"
    assert len(out.data["resources"]) == 4  # 2 阶段 × 2 候选
    assert all(r["verified"] == "ok" for r in out.data["resources"])
    stages = {r["stage_index"] for r in out.data["resources"]}
    assert stages == {0, 1}


def test_invalid_candidates_dropped(monkeypatch):
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")

    async def fake_bing(query, api_key, count=5):
        return [{"title": "good", "url": "https://example.com/ok"}, {"title": "bad", "url": "https://example.com/dead"}]

    async def fake_verify(inp, ctx):
        url = inp.args.get("url", "")
        label = "invalid" if "dead" in url else "ok"
        return ToolOutput(tool="verify_url", status="ok", data={"ok": label == "ok", "status": 200 if label == "ok" else 404, "suitable": label == "ok", "final_url": url, "label": label})

    monkeypatch.setattr(sr_mod, "_bing_search", fake_bing)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)

    out = _run({"stage_topics": [["React"]]})
    assert out.status == "ok"
    assert len(out.data["resources"]) == 1  # invalid 被丢弃
    assert out.data["resources"][0]["verified"] == "ok"


# --- err.md B21：stage_topics 缺省时从 upstream.design_stages 派生（生产空转修复）---


def test_derive_stage_topics_from_upstream(monkeypatch):
    """args 无 stage_topics -> 从 upstream.design_stages 派生（每阶段 topics[].name，去重）。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")
    seen_queries: list[str] = []

    async def fake_bing(query, api_key, count=5):
        seen_queries.append(query)
        return [{"title": f"{query} 教程", "url": f"https://example.com/{query}"}]

    async def fake_verify(inp, ctx):
        url = inp.args.get("url", "")
        return ToolOutput(tool="verify_url", status="ok", data={"final_url": url, "label": "ok"})

    monkeypatch.setattr(sr_mod, "_bing_search", fake_bing)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)

    inp = ToolInput(
        tool="search_resources",
        session_id="s",
        args={},  # 生产形态：args_by_tool 不含 stage_topics
        upstream={
            "design_stages": {
                "data": {
                    "stages": [
                        {"name": "入门", "topics": [{"name": "React 基础"}, {"name": "JSX"}, {"name": "React 基础"}]},
                        {"name": "进阶", "topics": [{"name": "Hooks"}, "非字典项", {"name": "  "}]},
                    ]
                }
            }
        },
    )
    out = asyncio.run(search_resources(inp, _ctx()))
    assert out.status == "ok"
    assert seen_queries == ["React 基础", "JSX", "Hooks"]  # 派生 + 阶段内去重 + 非法项剔除
    assert {r["stage_index"] for r in out.data["resources"]} == {0, 1}


def test_explicit_stage_topics_take_precedence(monkeypatch):
    """args 显式提供 stage_topics -> 优先于 upstream 派生（契约/测试兼容）。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")
    seen_queries: list[str] = []

    async def fake_bing(query, api_key, count=5):
        seen_queries.append(query)
        return []

    monkeypatch.setattr(sr_mod, "_bing_search", fake_bing)

    inp = ToolInput(
        tool="search_resources",
        session_id="s",
        args={"stage_topics": [["显式主题"]]},
        upstream={"design_stages": {"data": {"stages": [{"name": "x", "topics": [{"name": "派生主题"}]}]}}},
    )
    asyncio.run(search_resources(inp, _ctx()))
    assert seen_queries == ["显式主题"]


# --- iter 48：search_unit_resources（单单元 WebSearch 补充资料，plan/07 §三 步骤 2）---


def _run_unit(query, limit=3):
    return asyncio.run(search_unit_resources(query, _ctx(), limit=limit))


def test_unit_resources_no_key_or_empty_query(monkeypatch):
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "")
    assert _run_unit("React 基础") == []  # 无 key -> 空（不编造 URL）
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")
    assert _run_unit("   ") == []  # 空 query -> 空


def test_unit_resources_verify_and_cap(monkeypatch):
    """候选逐个 verify_url：invalid 丢弃；保留 ≤limit 条 {title,url,verified,note}。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")

    async def fake_bing(query, api_key, count=5):
        assert count == 2  # limit 传递给 Bing count
        return [
            {"title": "good", "url": "https://example.com/ok"},
            {"title": "bad", "url": "https://example.com/dead"},
            {"title": "good2", "url": "https://example.com/ok2"},
        ]

    async def fake_verify(inp, ctx):
        url = inp.args.get("url", "")
        label = "invalid" if "dead" in url else "ok"
        return ToolOutput(tool="verify_url", status="ok", data={"final_url": url, "label": label})

    monkeypatch.setattr(sr_mod, "_bing_search", fake_bing)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)

    res = _run_unit("React 基础", limit=2)
    assert [r["url"] for r in res] == ["https://example.com/ok", "https://example.com/ok2"]
    assert all(r["verified"] == "ok" for r in res)
    assert set(res[0]) == {"title", "url", "verified", "note"}


def test_unit_resources_search_failure_returns_empty(monkeypatch):
    """Bing 检索异常 -> []（不中断单元生成）。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")

    async def fake_bing(query, api_key, count=5):
        raise RuntimeError("网络故障")

    monkeypatch.setattr(sr_mod, "_bing_search", fake_bing)
    assert _run_unit("React 基础") == []


# --- iter 62：LLM 内容适配性筛选（verify 可达后批量判定；失败稳定降级为不筛选）---

from app.llm.base import LLMResponse, LLMUsage  # noqa: E402


class _ScreenProvider:
    """fake LLM：记录 system/user，返回预设 content（chat 签名同 AnthropicProvider）。"""

    def __init__(self, content: str | None = None, exc: Exception | None = None):
        self.content = content
        self.exc = exc
        self.calls: list[dict] = []

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        self.calls.append({"system": system, "user": user, "cache_system": cache_system})
        if self.exc is not None:
            raise self.exc
        return LLMResponse(content=self.content or "", usage=LLMUsage(model="m"))


def _setup_two_candidates(monkeypatch):
    """两候选（ok/deep）经 fake Bing + fake verify 均可达 ok。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")

    async def fake_bing(query, api_key, count=5):
        return [
            {"title": f"{query} 入门教程", "url": "https://example.com/intro"},
            {"title": f"{query} 内核源码剖析", "url": "https://example.com/deep"},
        ]

    async def fake_verify(inp, ctx):
        url = inp.args.get("url", "")
        return ToolOutput(tool="verify_url", status="ok", data={"final_url": url, "label": "ok"})

    monkeypatch.setattr(sr_mod, "_bing_search", fake_bing)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)


def _run_with_provider(provider, skill_registry=None):
    ctx = ExecContext(
        session_id="s",
        trace_id="t",
        registry=default_registry(),
        llm_provider=provider,
        skill_registry=skill_registry,
    )
    inp = ToolInput(
        tool="search_resources",
        session_id="s",
        args={"stage_topics": [["Python 基础"]]},
        upstream={
            "understand_intent": {"data": {"topic": "Python", "level": "零基础"}},
            "design_stages": {"data": {"stages": [{"name": "入门"}]}},
        },
    )
    return asyncio.run(search_resources(inp, ctx))


def test_screening_drops_unsuitable_and_marks_uncertain(monkeypatch):
    """suitable=false 丢弃；label=uncertain 降 ⚠️ + note 写入。"""
    _setup_two_candidates(monkeypatch)
    provider = _ScreenProvider(
        '[{"url":"https://example.com/intro","suitable":true,"label":"ok","note":""},'
        '{"url":"https://example.com/deep","suitable":true,"label":"uncertain","note":"内容过深，适合进阶"}]'
    )
    out = _run_with_provider(provider)
    res = out.data["resources"]
    assert len(res) == 2
    intro = next(r for r in res if "intro" in r["url"])
    deep = next(r for r in res if "deep" in r["url"])
    assert intro["verified"] == "ok" and intro["note"] == ""
    assert deep["verified"] == "uncertain" and deep["note"] == "内容过深，适合进阶"
    # 筛选输入含 intent/stage 上下文
    assert "Python" in provider.calls[0]["user"]
    assert provider.calls[0]["cache_system"] is True


def test_screening_drops_irrelevant(monkeypatch):
    _setup_two_candidates(monkeypatch)
    provider = _ScreenProvider(
        '[{"url":"https://example.com/intro","suitable":true,"label":"ok","note":""},'
        '{"url":"https://example.com/deep","suitable":false,"label":"uncertain","note":"与主题无关"}]'
    )
    out = _run_with_provider(provider)
    res = out.data["resources"]
    assert len(res) == 1 and "intro" in res[0]["url"]


def test_screening_failure_fallback(monkeypatch):
    """LLM 异常 / 输出不可解析 -> 跳过筛选，保持原判定（稳定降级）。"""
    _setup_two_candidates(monkeypatch)
    out = _run_with_provider(_ScreenProvider(exc=RuntimeError("LLM down")))
    assert len(out.data["resources"]) == 2
    assert all(r["verified"] == "ok" for r in out.data["resources"])

    out = _run_with_provider(_ScreenProvider("这不是 JSON"))
    assert len(out.data["resources"]) == 2

    out = _run_with_provider(_ScreenProvider('{"not":"a list"}'))
    assert len(out.data["resources"]) == 2


def test_screening_partial_coverage_keeps_rest(monkeypatch):
    """响应未覆盖的 url 保持原判定；非法单项跳过不影响其余。"""
    _setup_two_candidates(monkeypatch)
    provider = _ScreenProvider(
        '[{"url":"https://example.com/deep","suitable":true,"label":"uncertain","note":"偏深"},'
        '{"url":123,"suitable":"yes"}]'  # 非法项
    )
    out = _run_with_provider(provider)
    res = out.data["resources"]
    assert len(res) == 2
    intro = next(r for r in res if "intro" in r["url"])
    deep = next(r for r in res if "deep" in r["url"])
    assert intro["verified"] == "ok"  # 未覆盖 -> 原判定
    assert deep["verified"] == "uncertain"


def test_screening_no_provider_skips(monkeypatch):
    """无 LLM provider -> 不筛选（既有行为，无需 LLM 也可出方案）。"""
    _setup_two_candidates(monkeypatch)
    out = _run({"stage_topics": [["Python 基础"]]})  # _ctx() 无 provider
    assert len(out.data["resources"]) == 2
    assert all(r["verified"] == "ok" for r in out.data["resources"])


def test_screening_uses_skill_prompt_when_available(monkeypatch):
    """skill_registry 有 deep-tutor-resources -> system 用 Skill 正文；否则 inline SCREEN_PROMPT。"""
    _setup_two_candidates(monkeypatch)
    provider = _ScreenProvider("[]")
    _run_with_provider(provider, skill_registry={"deep-tutor-resources": "SKILL 正文标记"})
    assert provider.calls[0]["system"] == "SKILL 正文标记"

    provider2 = _ScreenProvider("[]")
    _run_with_provider(provider2)  # 无 skill -> inline 回退
    assert provider2.calls[0]["system"] == sr_mod.SCREEN_PROMPT

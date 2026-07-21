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
    """SEARCH_ENGINE=bing 且未配置 key -> 兼容旧降级路径（no_search_key + 关键词建议）。"""
    monkeypatch.setattr(settings_mod.settings, "search_engine", "bing")
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "")
    out = _run({"stage_topics": [["React"], ["JS 基础"]]})
    assert out.status == "repaired"
    assert out.data["resources"] == []
    assert out.errors[0].code == "no_search_key"
    assert "React" in out.data["suggested_keywords"]


def test_aggregation_with_mocks(monkeypatch):
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")

    async def fake_search(query, engine, count=5, key=""):
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

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)

    out = _run({"stage_topics": [["React"], ["JS"]]})
    assert out.status == "ok"
    assert len(out.data["resources"]) == 4  # 2 阶段 × 2 候选
    assert all(r["verified"] == "ok" for r in out.data["resources"])
    stages = {r["stage_index"] for r in out.data["resources"]}
    assert stages == {0, 1}


def test_invalid_candidates_dropped(monkeypatch):
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")

    async def fake_search(query, engine, count=5, key=""):
        return [{"title": "good", "url": "https://example.com/ok"}, {"title": "bad", "url": "https://example.com/dead"}]

    async def fake_verify(inp, ctx):
        url = inp.args.get("url", "")
        label = "invalid" if "dead" in url else "ok"
        return ToolOutput(tool="verify_url", status="ok", data={"ok": label == "ok", "status": 200 if label == "ok" else 404, "suitable": label == "ok", "final_url": url, "label": label})

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
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

    async def fake_search(query, engine, count=5, key=""):
        seen_queries.append(query)
        return [{"title": f"{query} 教程", "url": f"https://example.com/{query}"}]

    async def fake_verify(inp, ctx):
        url = inp.args.get("url", "")
        return ToolOutput(tool="verify_url", status="ok", data={"final_url": url, "label": "ok"})

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
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

    async def fake_search(query, engine, count=5, key=""):
        seen_queries.append(query)
        return []

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)

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
    monkeypatch.setattr(settings_mod.settings, "search_engine", "bing")
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "")
    assert _run_unit("React 基础") == []  # 无 key -> 空（不编造 URL）
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")
    assert _run_unit("   ") == []  # 空 query -> 空


def test_unit_resources_verify_and_cap(monkeypatch):
    """候选逐个 verify_url：invalid 丢弃；保留 ≤limit 条 {title,url,verified,note}。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")

    async def fake_search(query, engine, count=5, key=""):
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

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)

    res = _run_unit("React 基础", limit=2)
    assert [r["url"] for r in res] == ["https://example.com/ok", "https://example.com/ok2"]
    assert all(r["verified"] == "ok" for r in res)
    assert set(res[0]) == {"title", "url", "verified", "note"}


def test_unit_resources_search_failure_returns_empty(monkeypatch):
    """Bing 检索异常 -> []（不中断单元生成）。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")

    async def fake_search(query, engine, count=5, key=""):
        raise RuntimeError("网络故障")

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
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

    async def fake_search(query, engine, count=5, key=""):
        return [
            {"title": f"{query} 入门教程", "url": "https://example.com/intro"},
            {"title": f"{query} 内核源码剖析", "url": "https://example.com/deep"},
        ]

    async def fake_verify(inp, ctx):
        url = inp.args.get("url", "")
        return ToolOutput(tool="verify_url", status="ok", data={"final_url": url, "label": "ok"})

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
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


# --- iter 222：BING_API_KEY 可选化——引擎决策矩阵 + Bing 网页抓取兜底引擎 ---

import httpx  # noqa: E402,F401
import pytest  # noqa: E402


def test_effective_engine_matrix(monkeypatch):
    """决策矩阵：auto+key->bing_api；auto/bing_web 无 key->bing_web；bing 无 key->none；非法值归一化 auto。"""
    eff = settings_mod.effective_search_engine
    for engine, key, expected in [
        ("auto", "k", "bing_api"),
        ("auto", "", "bing_web"),
        ("bing", "k", "bing_api"),
        ("bing", "", "none"),
        ("bing_web", "k", "bing_web"),
        ("bing_web", "", "bing_web"),
        ("google", "", "bing_web"),  # 非法值 -> 归一化 auto -> 无 key -> bing_web
        (" AUTO ", "k", "bing_api"),  # strip/lower
    ]:
        monkeypatch.setattr(settings_mod.settings, "search_engine", engine)
        monkeypatch.setattr(settings_mod.settings, "bing_api_key", key)
        assert eff() == expected, f"{engine=},{key=}"
    assert settings_mod.normalize_search_engine("google") == "auto"


def test_auto_no_key_uses_web_engine(monkeypatch):
    """auto + 无 key -> 以 bing_web 引擎检索（零配置可用）。"""
    monkeypatch.setattr(settings_mod.settings, "search_engine", "auto")
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "")
    seen: list[str] = []

    async def fake_search(query, engine, count=5, key=""):
        seen.append(engine)
        return [{"title": "t", "url": "https://example.com/x"}]

    async def fake_verify(inp, ctx):
        url = inp.args.get("url", "")
        return ToolOutput(tool="verify_url", status="ok", data={"final_url": url, "label": "ok"})

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)

    out = _run({"stage_topics": [["React"]]})
    assert out.status == "ok"
    assert seen == ["bing_web"]
    assert out.data["engine"] == "bing_web"
    assert len(out.data["resources"]) == 1


def test_bing_web_preferred_even_with_key(monkeypatch):
    """SEARCH_ENGINE=bing_web 显式强制网页版（即使已配 key）。"""
    monkeypatch.setattr(settings_mod.settings, "search_engine", "bing_web")
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")
    seen: list[str] = []

    async def fake_search(query, engine, count=5, key=""):
        seen.append(engine)
        return []

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    out = _run({"stage_topics": [["React"]]})
    assert seen == ["bing_web"]
    assert out.data["engine"] == "bing_web"


def test_web_scrape_total_failure_degraded(monkeypatch):
    """网页引擎全面失败（attempted>0 且全部异常）-> repaired + search_unavailable + 关键词建议。"""
    monkeypatch.setattr(settings_mod.settings, "search_engine", "auto")
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "")

    async def fake_search(query, engine, count=5, key=""):
        raise RuntimeError("反爬拦截")

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    out = _run({"stage_topics": [["React"], ["JS"]]})
    assert out.status == "repaired"
    assert out.data["resources"] == []
    assert out.data["engine"] == "bing_web"
    assert out.errors[0].code == "search_unavailable"
    assert "React" in out.data["suggested_keywords"]


def test_web_scrape_empty_results_ok(monkeypatch):
    """引擎成功但真空结果（不抛异常）-> ok + 空 resources（真空 ≠ 引擎故障）。"""
    monkeypatch.setattr(settings_mod.settings, "search_engine", "auto")
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "")

    async def fake_search(query, engine, count=5, key=""):
        return []

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    out = _run({"stage_topics": [["生僻主题"]]})
    assert out.status == "ok"
    assert out.data["resources"] == []
    assert out.data["engine"] == "bing_web"


def test_no_topics_not_unavailable(monkeypatch):
    """空主题（attempted==0）不得误报 search_unavailable（守卫回归）。"""
    monkeypatch.setattr(settings_mod.settings, "search_engine", "auto")
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "")

    async def fake_search(query, engine, count=5, key=""):
        raise AssertionError("不应发起检索")

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    out = _run({"stage_topics": []})
    assert out.status == "ok"
    assert out.data["resources"] == []


def test_unit_resources_auto_no_key(monkeypatch):
    """单元补充资料：auto + 无 key -> 走网页引擎正常返回。"""
    monkeypatch.setattr(settings_mod.settings, "search_engine", "auto")
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "")
    seen: list[str] = []

    async def fake_search(query, engine, count=5, key=""):
        seen.append(engine)
        return [{"title": "t", "url": "https://example.com/u"}]

    async def fake_verify(inp, ctx):
        url = inp.args.get("url", "")
        return ToolOutput(tool="verify_url", status="ok", data={"final_url": url, "label": "ok"})

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)
    res = _run_unit("React 基础")
    assert seen == ["bing_web"]
    assert len(res) == 1 and res[0]["verified"] == "ok"


# --- _parse_bing_web_html 纯函数（罐头 HTML，不触网）---

_BING_WEB_HTML = """
<html><body><ol id="b_results">
<li class="b_algo"><h2><a href="https://example.com/a">示例 <strong>标题</strong> A</a></h2></li>
<li class="b_algo"><h2><a href="https://example.com/b">标题 &amp; B</a></h2></li>
<li class="b_algo"><h2><a href="https://example.com/a">重复 URL 应去重</a></h2></li>
<li class="b_algo"><h2><a href="https://cn.bing.com/ck/a?u=a1">跳转壳应剔除</a></h2></li>
<li class="b_algo"><h2><a href="javascript:void(0)">非 http 链接应剔除</a></h2></li>
</ol></body></html>
"""


def test_parse_bing_web_html_extracts_direct_links():
    res = sr_mod._parse_bing_web_html(_BING_WEB_HTML, 10)
    assert [r["url"] for r in res] == ["https://example.com/a", "https://example.com/b"]
    assert res[0]["title"] == "示例 标题 A"  # 内层标签去除
    assert res[1]["title"] == "标题 & B"  # HTML 实体 unescape


def test_parse_bing_web_html_count_and_malformed():
    assert len(sr_mod._parse_bing_web_html(_BING_WEB_HTML, 1)) == 1  # count 截断
    assert sr_mod._parse_bing_web_html("", 5) == []
    assert sr_mod._parse_bing_web_html("<html>无关内容</html>", 5) == []  # 不抛


# --- _bing_web_search HTTP 层（假 httpx 客户端，不触网）---


class _FakeStreamResp:
    def __init__(self, body: bytes, *, url="https://cn.bing.com/search?q=x", content_type="text/html; charset=utf-8"):
        self._body = body
        self.url = url
        self.headers = {"content-type": content_type}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        yield self._body


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method, url, headers=None, params=None):
        return self._resp


def _patch_client(monkeypatch, resp):
    monkeypatch.setattr(sr_mod.httpx, "AsyncClient", lambda **kw: _FakeClient(resp))


def test_bing_web_search_ok(monkeypatch):
    _patch_client(monkeypatch, _FakeStreamResp(_BING_WEB_HTML.encode()))
    res = asyncio.run(sr_mod._bing_web_search("计算机组成原理"))
    assert len(res) == 2
    assert res[0]["url"] == "https://example.com/a"


def test_bing_web_search_antibot_page_raises(monkeypatch):
    """无 b_results 容器（疑似反爬/验证码页）-> 抛（引擎失败，非静默空结果）。"""
    _patch_client(monkeypatch, _FakeStreamResp(b"<html><body>verify you are human</body></html>"))
    with pytest.raises(ValueError, match="b_results"):
        asyncio.run(sr_mod._bing_web_search("x"))


def test_bing_web_search_redirect_offsite_raises(monkeypatch):
    """终态 host 非 bing.com -> 抛（防重定向劫持）。"""
    _patch_client(monkeypatch, _FakeStreamResp(_BING_WEB_HTML.encode(), url="https://evil.example.com/x"))
    with pytest.raises(ValueError, match="Bing"):
        asyncio.run(sr_mod._bing_web_search("x"))


def test_bing_web_search_non_html_raises(monkeypatch):
    _patch_client(monkeypatch, _FakeStreamResp(b"{}", content_type="application/json"))
    with pytest.raises(ValueError, match="HTML"):
        asyncio.run(sr_mod._bing_web_search("x"))


def test_bing_web_search_oversize_raises(monkeypatch):
    body = b'<ol id="b_results">' + b"x" * (sr_mod.BING_WEB_MAX_BYTES + 1)
    _patch_client(monkeypatch, _FakeStreamResp(body))
    with pytest.raises(ValueError, match="上限"):
        asyncio.run(sr_mod._bing_web_search("x"))


# ---------------------------------------------------------------------------
# iter 228：检索/校验有界并发
# ---------------------------------------------------------------------------


def test_parallel_selection_order_deterministic(monkeypatch):
    """并发完成顺序不泄漏到结果：选择按 阶段->主题->候选 原顺序（与旧串行逐字一致）。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")

    async def fake_search(query, engine, count=5, key=""):
        # 先发的查询反而更慢——若并发完成顺序泄漏，结果将乱序
        await asyncio.sleep(0.05 if query == "t0" else 0)
        return [{"title": f"{query}-c{i}", "url": f"https://example.com/{query}/{i}"} for i in range(2)]

    async def fake_verify(inp, ctx):
        url = inp.args.get("url", "")
        return ToolOutput(
            tool="verify_url",
            status="ok",
            data={"ok": True, "status": 200, "suitable": True, "final_url": url, "label": "ok"},
        )

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)

    out = _run({"stage_topics": [["t0", "t1"], ["t2"]]})
    assert out.status == "ok"
    got = [(r["stage_index"], r["url"]) for r in out.data["resources"]]
    assert got == [
        (0, "https://example.com/t0/0"),
        (0, "https://example.com/t0/1"),
        (0, "https://example.com/t1/0"),
        (0, "https://example.com/t1/1"),
        (1, "https://example.com/t2/0"),
        (1, "https://example.com/t2/1"),
    ]


def test_search_concurrency_bounded(monkeypatch):
    """检索并发真实发生且不超过 _SEARCH_CONCURRENCY（bing_web 反爬保守上限）。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")
    state = {"cur": 0, "max": 0}

    async def fake_search(query, engine, count=5, key=""):
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        await asyncio.sleep(0.05)
        state["cur"] -= 1
        return []

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    out = _run({"stage_topics": [["q0", "q1", "q2"], ["q3"]]})
    assert out.status == "ok"
    assert state["max"] == sr_mod._SEARCH_CONCURRENCY


def test_verify_dedup_and_exception_skips(monkeypatch):
    """同 URL 跨阶段只验一次；单候选校验异常按 invalid 跳过、不拖垮整批。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")
    calls: list[str] = []

    async def fake_search(query, engine, count=5, key=""):
        return [
            {"title": "shared", "url": "https://example.com/shared"},
            {"title": "boom", "url": "https://example.com/boom"},
        ]

    async def fake_verify(inp, ctx):
        url = inp.args.get("url", "")
        calls.append(url)
        if "boom" in url:
            raise RuntimeError("verify bug")
        return ToolOutput(
            tool="verify_url",
            status="ok",
            data={"ok": True, "status": 200, "suitable": True, "final_url": url, "label": "ok"},
        )

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)

    out = _run({"stage_topics": [["a"], ["b"]]})
    assert out.status == "ok"  # 异常候选未拖垮整批
    assert calls.count("https://example.com/shared") == 1  # 去重只验一次
    got = {(r["stage_index"], r["url"]) for r in out.data["resources"]}
    assert (0, "https://example.com/shared") in got
    assert (1, "https://example.com/shared") in got  # 两阶段仍各自收录（同旧行为）
    assert all("boom" not in u for _, u in got)

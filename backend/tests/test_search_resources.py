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


def test_bing_no_key_falls_back_to_web(monkeypatch):
    """SEARCH_ENGINE=bing 且未配置 key -> 隐式回退免 Key 网页检索（iter 346）。

    旧行为返回 no_search_key 降级（resources 恒空），用户感知「联网搜索失败」却无报错；
    现与 auto 的免 Key 兜底语义对齐——bing+无 key 走 bing_web 网页抓取。
    """
    monkeypatch.setattr(settings_mod.settings, "search_engine", "bing")
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "")
    seen: list[str] = []

    async def fake_search(query, engine, count=5, key=""):
        seen.append(engine)
        return [{"title": f"{query} 教程", "url": f"https://example.com/{query}"}]

    async def fake_verify(inp, ctx, client=None):
        url = inp.args.get("url", "")
        return ToolOutput(tool="verify_url", status="ok", data={"final_url": url, "label": "ok"})

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)

    out = _run({"stage_topics": [["React"], ["JS 基础"]]})
    assert out.status == "ok"
    assert seen and all(e == "bing_web" for e in seen)  # 多查询均回退免 Key 网页引擎，而非静默空
    assert out.data["engine"] == "bing_web"
    assert len(out.data["resources"]) >= 1


def test_none_engine_degraded_path(monkeypatch):
    """effective_search_engine 显式返回 'none'（未来禁用检索开关）-> search_resources 走 no_search_key 降级路径。

    iter 346 后 effective_search_engine 不再对 bing+无 key 返回 none，但 search_resources 的
    none 分支保留为防御性降级（保完全性）；此测试固化该路径不被误删。
    """
    monkeypatch.setattr(sr_mod, "effective_search_engine", lambda key=None: "none")
    out = _run({"stage_topics": [["React"], ["JS 基础"]]})
    assert out.status == "repaired"
    assert out.data["resources"] == []
    assert out.data["engine"] == "none"
    assert out.errors[0].code == "no_search_key"
    assert "React" in out.data["suggested_keywords"]


def test_aggregation_with_mocks(monkeypatch):
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")

    async def fake_search(query, engine, count=5, key=""):
        return [
            {"title": f"{query} 教程", "url": f"https://example.com/{query}"},
            {"title": f"{query} 文档", "url": f"https://example.com/doc/{query}"},
        ]

    async def fake_verify(inp, ctx, client=None):
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

    async def fake_verify(inp, ctx, client=None):
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

    async def fake_verify(inp, ctx, client=None):
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


def _run_unit(query, limit=3, provider=None):
    ctx = ExecContext(
        session_id="s", trace_id="t", registry=default_registry(), llm_provider=provider
    )
    return asyncio.run(search_unit_resources(query, ctx, limit=limit))


def test_unit_resources_no_key_or_empty_query(monkeypatch):
    """无 key 回退免 Key 网页（iter 346）但网页无结果时不编造 URL；空 query 直接空。"""
    monkeypatch.setattr(settings_mod.settings, "search_engine", "bing")
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "")

    async def fake_search(query, engine, count=5, key=""):
        return []  # 模拟网页无候选

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    # iter 361：注入放行 provider 使候选能过强制筛选门，从而真正测「网页无候选 -> 空」
    assert _run_unit("React 基础", provider=_PassScreenProvider()) == []  # 回退 bing_web 但无结果 -> 空
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")
    assert _run_unit("   ", provider=_PassScreenProvider()) == []  # 空 query -> 空


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

    async def fake_verify(inp, ctx, client=None):
        url = inp.args.get("url", "")
        label = "invalid" if "dead" in url else "ok"
        return ToolOutput(tool="verify_url", status="ok", data={"final_url": url, "label": label})

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)

    res = _run_unit("React 基础", limit=2, provider=_PassScreenProvider())
    assert [r["url"] for r in res] == ["https://example.com/ok", "https://example.com/ok2"]
    assert all(r["verified"] == "ok" for r in res)
    assert set(res[0]) == {"title", "url", "verified", "note"}


def test_unit_resources_search_failure_returns_empty(monkeypatch):
    """Bing 检索异常 -> []（不中断单元生成）。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")

    async def fake_search(query, engine, count=5, key=""):
        raise RuntimeError("网络故障")

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    assert _run_unit("React 基础", provider=_PassScreenProvider()) == []


def test_unit_resources_no_provider_returns_empty(monkeypatch):
    """iter 361 强制门：无 provider（降级材料路径真实可达）-> 直接返回 []，
    不发起检索/不提供仅 verify 可达性（未经 AI 筛选）的网络补充资料（避免不良信息）。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")

    async def fake_search(query, engine, count=5, key=""):
        raise AssertionError("无 provider 应在检索前短路，不应发起检索")

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    assert _run_unit("React 基础", provider=None) == []


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


class _PassScreenProvider:
    """fake LLM：对收到的任意候选均回 suitable=true/ok。

    供需「保留资源」的 search/verify 逻辑测试注入——iter 361 强制门后，无 provider 的
    search_unit_resources 直接返回 []，故测 verify/去重/限额等非筛选逻辑时须注入一个
    「全放行」的筛选 provider，使候选能通过强制 AI 筛选门。
    """

    def __init__(self):
        self.calls: list[dict] = []

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        import json

        self.calls.append({"system": system, "user": user})
        try:
            cands = json.loads(user or "{}").get("candidates", [])
        except Exception:  # noqa: BLE001
            cands = []
        verdicts = [{"url": c.get("url"), "suitable": True, "label": "ok", "note": ""} for c in cands]
        return LLMResponse(content=json.dumps(verdicts, ensure_ascii=False), usage=LLMUsage(model="m"))


def _setup_two_candidates(monkeypatch):
    """两候选（ok/deep）经 fake Bing + fake verify 均可达 ok。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")

    async def fake_search(query, engine, count=5, key=""):
        return [
            {"title": f"{query} 入门教程", "url": "https://example.com/intro"},
            {"title": f"{query} 内核源码剖析", "url": "https://example.com/deep"},
        ]

    async def fake_verify(inp, ctx, client=None):
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


def test_screening_failure_drops_all_with_note(monkeypatch):
    """iter 361 强制门：有 provider 但 LLM 异常 / 输出不可解析（重试耗尽）-> 丢弃全部候选 +
    data.note 告警。不再像旧 _mark_unscreened 那样降标保留未筛选候选（避免给学生引入未核验/不良信息）。"""
    _setup_two_candidates(monkeypatch)
    # 异常：每次调用都抛 -> 重试 _SCREEN_MAX_ATTEMPTS 次后全丢弃
    p_exc = _ScreenProvider(exc=RuntimeError("LLM down"))
    out = _run_with_provider(p_exc)
    assert out.data["resources"] == []
    assert len(p_exc.calls) == sr_mod._SCREEN_MAX_ATTEMPTS  # 重试发生
    assert "已过滤全部候选资源" in out.data.get("note", "")

    # 不可解析（非 JSON）：重试耗尽 -> 全丢弃 + 告警
    out = _run_with_provider(_ScreenProvider("这不是 JSON"))
    assert out.data["resources"] == []
    assert "已过滤全部候选资源" in out.data.get("note", "")

    # 不可解析（非数组）：同上
    out = _run_with_provider(_ScreenProvider('{"not":"a list"}'))
    assert out.data["resources"] == []
    assert "已过滤全部候选资源" in out.data.get("note", "")


def test_screening_uncovered_url_dropped(monkeypatch):
    """iter 361 强制门：LLM 响应未覆盖的 url -> 丢弃（未通过 AI 核验）；非法单项跳过不影响其余。"""
    _setup_two_candidates(monkeypatch)
    provider = _ScreenProvider(
        '[{"url":"https://example.com/deep","suitable":true,"label":"uncertain","note":"偏深"},'
        '{"url":123,"suitable":"yes"}]'  # 非法项
    )
    out = _run_with_provider(provider)
    res = out.data["resources"]
    assert len(res) == 1  # intro 未覆盖 -> 丢弃；deep 通过
    assert "deep" in res[0]["url"]
    assert res[0]["verified"] == "uncertain"


def test_screening_no_provider_skips(monkeypatch):
    """无 LLM provider -> 不筛选（search_resources 在真实方案生成中经 plans._run_start 的
    no_llm_provider 守卫永不至此；此分支仅测试/独立调用走，保留 search/verify 逻辑测试兼容）。"""
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


# --- iter 361：强制门稳健性（URL 规范化匹配 / suitable 宽容 / 动态 max_tokens）---


def test_screen_url_key_normalizes():
    """URL 规范化匹配键：容错真实 LLM 返回 url 与候选的细微差异（iter 361 实机回归根因）。"""
    assert sr_mod._screen_url_key("https://Example.com/intro") == sr_mod._screen_url_key("https://example.com/intro")
    assert sr_mod._screen_url_key("https://example.com/intro/") == sr_mod._screen_url_key("https://example.com/intro")
    assert sr_mod._screen_url_key("https://example.com/intro#sec") == sr_mod._screen_url_key("https://example.com/intro")
    assert sr_mod._screen_url_key(" https://example.com/intro ") == sr_mod._screen_url_key("https://example.com/intro")
    # path/query 大小写语义敏感，保留
    assert sr_mod._screen_url_key("https://example.com/A?B=1") == "https://example.com/A?B=1"
    assert sr_mod._screen_url_key("") == ""
    assert sr_mod._screen_url_key(None) == ""


def test_coerce_suitable_lenient():
    """真实 LLM 偶把布尔写成字符串/数字；宽容解析（无法判定才 None -> 按未覆盖丢弃）。"""
    assert sr_mod._coerce_suitable(True) is True
    assert sr_mod._coerce_suitable(False) is False
    assert sr_mod._coerce_suitable("true") is True
    assert sr_mod._coerce_suitable("FALSE") is False
    assert sr_mod._coerce_suitable(1) is True
    assert sr_mod._coerce_suitable(0) is False
    assert sr_mod._coerce_suitable("maybe") is None
    assert sr_mod._coerce_suitable(None) is None
    # iter 363：整数非 0/1（如 LLM 偶发评分 2/3）-> None（旧 `==1` 误判 False -> 候选被误丢）
    assert sr_mod._coerce_suitable(2) is None
    assert sr_mod._coerce_suitable(-1) is None
    # float 1.0/0.0；简写 y/n/t/f；去尾标点
    assert sr_mod._coerce_suitable(1.0) is True
    assert sr_mod._coerce_suitable(0.0) is False
    assert sr_mod._coerce_suitable("Y") is True
    assert sr_mod._coerce_suitable("n") is False
    assert sr_mod._coerce_suitable("yes,") is True


def test_screen_max_tokens_scales_and_capped():
    """候选数 -> max_tokens 单调递增且有上限（候选多时不截断输出）。"""
    assert sr_mod._screen_max_tokens(0) >= 500
    assert sr_mod._screen_max_tokens(10) > sr_mod._screen_max_tokens(0)
    assert sr_mod._screen_max_tokens(1000) <= 8000  # 封顶


def test_screening_matches_via_url_normalization(monkeypatch):
    """iter 361 实机回归：LLM 返回的 url 带尾斜杠/fragment/host 大小写差异时，规范化匹配仍命中候选，
    不再因「未覆盖」全丢（旧精确匹配会全丢 -> 方案无配套资源）。"""
    _setup_two_candidates(monkeypatch)
    provider = _ScreenProvider(
        # LLM 把 intro 加尾斜杠、deep 加 fragment 且 host 大写——均应规范化后匹配
        '[{"url":"https://example.com/intro/","suitable":true,"label":"ok","note":""},'
        '{"url":"https://EXAMPLE.com/deep#x","suitable":true,"label":"uncertain","note":"偏深"}]'
    )
    out = _run_with_provider(provider)
    res = out.data["resources"]
    assert len(res) == 2  # 规范化匹配 -> 两候选均保留（修复前会全丢）


def test_screening_accepts_string_suitable(monkeypatch):
    """iter 361：suitable 写成字符串 "true"/"false" 也按布尔处理（不因类型不符被跳过 -> 未覆盖全丢）。"""
    _setup_two_candidates(monkeypatch)
    provider = _ScreenProvider(
        '[{"url":"https://example.com/intro","suitable":"true","label":"ok","note":""},'
        '{"url":"https://example.com/deep","suitable":"false","label":"uncertain","note":""}]'
    )
    out = _run_with_provider(provider)
    res = out.data["resources"]
    assert len(res) == 1 and "intro" in res[0]["url"]  # intro 通过、deep 丢弃


# --- iter 222：BING_API_KEY 可选化——引擎决策矩阵 + Bing 网页抓取兜底引擎 ---

import httpx  # noqa: E402,F401
import pytest  # noqa: E402


def test_effective_engine_matrix(monkeypatch):
    """决策矩阵：auto+key->bing_api；auto/bing_web/bing 无 key->bing_web（iter 346 bing 回退）；非法值归一化 auto。"""
    eff = settings_mod.effective_search_engine
    for engine, key, expected in [
        ("auto", "k", "bing_api"),
        ("auto", "", "bing_web"),
        ("bing", "k", "bing_api"),
        ("bing", "", "bing_web"),  # iter 346：bing+无 key 回退免 Key 网页（旧为 none）
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

    async def fake_verify(inp, ctx, client=None):
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

    async def fake_verify(inp, ctx, client=None):
        url = inp.args.get("url", "")
        return ToolOutput(tool="verify_url", status="ok", data={"final_url": url, "label": "ok"})

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)
    res = _run_unit("React 基础", provider=_PassScreenProvider())
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

    async def fake_verify(inp, ctx, client=None):
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

    async def fake_verify(inp, ctx, client=None):
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


def test_verify_candidates_shares_one_client(monkeypatch):
    """R4（iter 231）：一批候选共享同一安全工厂 client（连接池复用），用毕恰好关闭一次。"""
    seen_clients: list[object] = []

    class _SentinelClient:
        def __init__(self):
            self.closed = 0

        async def aclose(self):
            self.closed += 1

    sentinel = _SentinelClient()
    monkeypatch.setattr(sr_mod, "make_verify_client", lambda ctx: sentinel)

    async def fake_verify(inp, ctx, client=None):
        seen_clients.append(client)
        url = inp.args.get("url", "")
        return ToolOutput(
            tool="verify_url",
            status="ok",
            data={"ok": True, "status": 200, "suitable": True, "final_url": url, "label": "ok"},
        )

    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)

    out = asyncio.run(
        sr_mod._verify_candidates(
            [(0, {"url": "https://a.com/x"}), (1, {"url": "https://b.com/y"}), (1, {"url": "https://a.com/x"})],
            "s",
            _ctx(),
        )
    )
    assert set(out) == {"https://a.com/x", "https://b.com/y"}  # URL 去重后两候选
    assert len(seen_clients) == 2 and all(c is sentinel for c in seen_clients)  # 全批共享
    assert sentinel.closed == 1  # 用毕恰好关闭一次


# --- iter 360：引文数量随复杂度动态（issue 4）---


def test_dynamic_quota_functions():
    """复杂度感知配额纯函数：难度越高/知识点越多 -> 参考文献越多（封顶）。"""
    from app.tools.search_resources import stage_resource_cap, total_resource_cap, stage_topic_cap

    assert stage_resource_cap({"difficulty": 1}, 0, []) == 3
    assert stage_resource_cap({"difficulty": 3}, 0, []) == 5
    assert stage_resource_cap({"difficulty": 5}, 0, []) == 7  # 封顶 _HARD_MAX_PER_STAGE
    assert stage_resource_cap(None, 0, []) == 5  # 无信号 -> 默认 MAX_PER_STAGE
    assert total_resource_cap(0) == 20
    assert total_resource_cap(30) == 50  # 20+30
    assert total_resource_cap(100) == 50  # 封顶 _HARD_MAX_TOTAL
    assert stage_topic_cap({"difficulty": 3}) == 3
    assert stage_topic_cap({"difficulty": 4}) == 4  # 难阶段检索更多主题
    assert stage_topic_cap(None) == 3


def test_search_topic_count_scales_with_difficulty(monkeypatch):
    """难度高的阶段检索更多主题（stage_topic_cap 生效）。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")
    seen: list[int] = []

    async def fake_search(query, engine, count=5, key=""):
        seen.append(count)
        return [{"title": query, "url": f"https://example.com/{query}"}]

    async def fake_verify(inp, ctx, client=None):
        url = inp.args.get("url", "")
        return ToolOutput(tool="verify_url", status="ok", data={"final_url": url, "label": "ok"})

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)

    inp = ToolInput(
        tool="search_resources",
        session_id="s",
        args={},
        upstream={
            "design_stages": {"data": {"stages": [
                {"index": 0, "name": "入门", "difficulty": 1, "topics": [{"name": "t1"}, {"name": "t2"}, {"name": "t3"}, {"name": "t4"}]},
                {"index": 1, "name": "高难", "difficulty": 5, "topics": [{"name": "h1"}, {"name": "h2"}, {"name": "h3"}, {"name": "h4"}]},
            ]}},
        },
    )
    asyncio.run(search_resources(inp, _ctx()))
    # 难度1阶段检索3主题，难度5阶段检索4主题 -> 共7次（seen 长度）；难度5阶段 count 更大（3+5=8）
    assert len(seen) == 7
    assert max(seen) == 8  # 难阶段候选数更多


def test_screening_receives_snippet(monkeypatch):
    """iter 360：verify 返回的正文摘要 snippet 传入筛选 LLM（据正文判相关性，issue 2c/3）。"""
    _setup_two_candidates(monkeypatch)

    async def fake_verify(inp, ctx, client=None):  # 带 snippet
        url = inp.args.get("url", "")
        return ToolOutput(
            tool="verify_url", status="ok",
            data={"final_url": url, "label": "ok", "snippet": f"{url} 的正文摘要内容"},
        )

    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)
    provider = _PassScreenProvider()  # 放行 -> 候选保留，验证 snippet 传入且最终剥离
    out = _run_with_provider(provider)
    payload = provider.calls[0]["user"]
    assert "snippet" in payload
    assert "正文摘要内容" in payload
    assert len(out.data["resources"]) == 2  # 放行后两候选保留
    # snippet 不进最终方案 resources（仅供筛选）
    assert all("snippet" not in r for r in out.data["resources"])


def test_unit_resources_llm_screening_drops_irrelevant(monkeypatch):
    """iter 360（issue 2a）：单元补料也经 LLM 相关性筛选，不相关丢弃。"""
    monkeypatch.setattr(settings_mod.settings, "bing_api_key", "fake-key")

    async def fake_search(query, engine, count=5, key=""):
        return [
            {"title": "相关", "url": "https://example.com/rel"},
            {"title": "无关", "url": "https://example.com/unrel"},
        ]

    async def fake_verify(inp, ctx, client=None):
        url = inp.args.get("url", "")
        return ToolOutput(tool="verify_url", status="ok", data={"final_url": url, "label": "ok", "snippet": "s"})

    monkeypatch.setattr(sr_mod, "_search_web", fake_search)
    monkeypatch.setattr(sr_mod, "verify_url", fake_verify)

    provider = _ScreenProvider(
        '[{"url":"https://example.com/rel","suitable":true,"label":"ok","note":""},'
        '{"url":"https://example.com/unrel","suitable":false,"label":"uncertain","note":"与单元无关"}]'
    )
    ctx = ExecContext(session_id="s", trace_id="t", registry=default_registry(), llm_provider=provider)
    res = asyncio.run(search_unit_resources("React 组件", ctx, limit=3))
    assert [r["url"] for r in res] == ["https://example.com/rel"]  # 无关被丢
    assert all("snippet" not in r for r in res)  # 摘要不持久化

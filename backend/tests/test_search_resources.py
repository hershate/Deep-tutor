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

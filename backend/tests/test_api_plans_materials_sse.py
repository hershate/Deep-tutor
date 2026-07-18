"""POST /plans/{id}/materials SSE 逐单元流测试（iter 46，plan/07 §三「每单元一次 LLM」）。

内容协商：Accept: text/event-stream 且需生成（无已存单元或 refresh=true）-> SSE 流；
幂等命中（已持久化未 refresh）仍返回 JSON。mock 模式复用 test_api_plans_materials 的
CannedProvider/_make_plan。SSE 解析跳过 `: keepalive` 注释行（仅认 event:/data: 行）。
"""

from __future__ import annotations

import json

import pytest

from app.llm.base import LLMProvider, LLMUsage

from test_api_plans_materials import _make_plan, _resp, _units_of

_SSE_HEADERS = {"Accept": "text/event-stream"}


@pytest.fixture(autouse=True)
def _stub_unit_search(monkeypatch):
    """默认 stub 单元补充资料检索（防 .env BING_API_KEY 致测试真实联网）；单测可覆盖。"""
    from app.api import plans as plans_mod

    async def _none(query, ctx):
        return []

    monkeypatch.setattr(plans_mod, "search_unit_resources", _none)


def _unit_resp(content_md: str):
    return _resp(
        json.dumps(
            {
                "objectives": ["学完后能够写出示例"],
                "prerequisites": ["无"],
                "estimated_time": "45 min",
                "content_md": content_md,
            },
            ensure_ascii=False,
        )
    )


class _UnitProvider(LLMProvider):
    """逐单元 provider：items 逐项消费；遇 Exception 实例则抛出（模拟单单元 LLM 失败）。"""

    def __init__(self, items):
        self._items = list(items)

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        item = self._items.pop(0) if self._items else _resp("{}")
        if isinstance(item, Exception):
            raise item
        return item


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """解析 SSE 文本为 [(event, data)]；跳过 `: keepalive` 注释行与空块。"""
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        event = data = None
        for line in block.split("\n"):
            if line.startswith(":"):
                continue  # keepalive 注释行
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if event is not None:
            events.append((event, data))
    return events


def _post_sse(client, auth_headers, plan_id, body=None):
    headers = {**auth_headers, **_SSE_HEADERS}
    return client.post(f"/api/plans/{plan_id}/materials", json=body or {"depth": "full"}, headers=headers)


def test_materials_sse_all_ok(client, auth_headers, monkeypatch):
    """SSE 全 ok：unit_start/unit_done 逐单元交错 + done 收尾；落库 3 单元；文件含 LLM 内容。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)
    provider = _UnitProvider(
        [
            _unit_resp("### 概述\n\n基础单元正文。"),
            _unit_resp("### 概述\n\n进阶单元正文。"),
            _unit_resp("### 概述\n\n实战单元正文。"),
        ]
    )
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: provider)

    r = _post_sse(client, auth_headers, plan_id)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(r.text)
    # plan 有 3 阶段（基础/进阶/实战）-> 3 单元
    assert [e for e, _ in events] == [
        "unit_start",
        "unit_done",
        "unit_start",
        "unit_done",
        "unit_start",
        "unit_done",
        "done",
    ]
    assert events[0][1] == {"order": 1, "title": "基础", "total": 3}
    assert events[1][1] == {"order": 1, "title": "基础", "degraded": False}
    assert events[2][1] == {"order": 2, "title": "进阶", "total": 3}
    assert events[3][1]["degraded"] is False
    assert events[6][1] == {"unit_count": 3, "degraded_count": 0}

    # 落库：3 单元，标题取 stage 名，order 0 起
    units = _units_of(plan_id)
    assert [u.title for u in units] == ["基础", "进阶", "实战"]
    assert [u.order for u in units] == [0, 1, 2]
    assert units[0].objectives == ["学完后能够写出示例"]
    assert units[0].estimated_time == "45 min"

    # 内容文件含 LLM 正文
    from pathlib import Path

    path = Path(units[0].materials[0].content_md_path)
    assert path.exists()
    assert "基础单元正文。" in path.read_text(encoding="utf-8")


def test_materials_sse_unit_failure_degrades(client, auth_headers, monkeypatch):
    """某单元 LLM 抛异常 -> 该单元 degraded=true 且 brief 简要大纲落库；其余单元正常。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)
    provider = _UnitProvider(
        [
            _unit_resp("### 概述\n\n基础单元正文。"),
            RuntimeError("LLM 服务不可用"),
            _unit_resp("### 概述\n\n实战单元正文。"),
        ]
    )
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: provider)

    r = _post_sse(client, auth_headers, plan_id)
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    done_events = [d for e, d in events if e == "unit_done"]
    assert [d["degraded"] for d in done_events] == [False, True, False]
    assert events[-1] == ("done", {"unit_count": 3, "degraded_count": 1})

    # 降级单元落库为 brief 简要大纲（_stage_to_brief_unit：概述 + 目标）
    from pathlib import Path

    units = _units_of(plan_id)
    assert len(units) == 3
    brief_path = Path(units[1].materials[0].content_md_path)
    brief_text = brief_path.read_text(encoding="utf-8")
    assert "本单元学习「进阶」" in brief_text
    assert "### 概述" in brief_text
    # 正常单元内容不受影响
    ok_path = Path(units[0].materials[0].content_md_path)
    assert "基础单元正文。" in ok_path.read_text(encoding="utf-8")


def test_materials_sse_no_provider_all_degraded(client, auth_headers, monkeypatch):
    """无 LLM provider（无 key）-> 全部单元降级 brief；done degraded_count=total；仍落库。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)

    def _raise(*a, **k):
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    monkeypatch.setattr(plans_mod, "get_provider", _raise)
    r = _post_sse(client, auth_headers, plan_id)
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    assert events[-1] == ("done", {"unit_count": 3, "degraded_count": 3})
    done_events = [d for e, d in events if e == "unit_done"]
    assert all(d["degraded"] for d in done_events)

    units = _units_of(plan_id)
    assert len(units) == 3
    from pathlib import Path

    text0 = Path(units[0].materials[0].content_md_path).read_text(encoding="utf-8")
    assert "本单元学习「基础」" in text0  # brief 降级内容


def test_materials_sse_idempotent_returns_json(client, auth_headers, monkeypatch):
    """幂等命中：已持久化未 refresh，即使 Accept 是 SSE 也返回 JSON（免重复 LLM 成本）。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)
    provider = _UnitProvider([_unit_resp("### 概述\n\n正文。")])
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: provider)
    r = _post_sse(client, auth_headers, plan_id)
    assert r.headers["content-type"].startswith("text/event-stream")
    assert _parse_sse(r.text)[-1][0] == "done"

    def _raise(*a, **k):
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    monkeypatch.setattr(plans_mod, "get_provider", _raise)
    r2 = _post_sse(client, auth_headers, plan_id)
    assert r2.status_code == 200, r2.text
    assert r2.headers["content-type"].startswith("application/json")
    body = r2.json()
    assert body["unit_count"] == 3
    assert "## 1. 基础" in body["markdown"]


def test_materials_sse_refresh_regenerates(client, auth_headers, monkeypatch):
    """refresh=true + SSE Accept -> 走 SSE 重新生成（替换旧单元）。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: _UnitProvider([_unit_resp("### 概述\n\n第一版。")]))
    r = _post_sse(client, auth_headers, plan_id)
    assert r.headers["content-type"].startswith("text/event-stream")

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: _UnitProvider([_unit_resp("### 概述\n\n第二版。")]))
    r2 = _post_sse(client, auth_headers, plan_id, body={"depth": "full", "refresh": True})
    assert r2.headers["content-type"].startswith("text/event-stream")
    assert _parse_sse(r2.text)[-1] == ("done", {"unit_count": 3, "degraded_count": 0})

    from pathlib import Path

    units = _units_of(plan_id)
    assert len(units) == 3  # 替换而非追加
    text0 = Path(units[0].materials[0].content_md_path).read_text(encoding="utf-8")
    assert "第二版。" in text0


def test_materials_sse_unit_resources_persisted(client, auth_headers, monkeypatch):
    """iter 48：单元 WebSearch 补充资料落库 study_materials.resources，GET /materials/{unit_id} 返回。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: _UnitProvider([_unit_resp("### 概述\n\n正文。")]))
    canned = [
        {"title": "React 官方文档", "url": "https://react.dev", "verified": "ok", "note": ""},
        {"title": "某教程", "url": "https://example.com/t", "verified": "uncertain", "note": ""},
    ]
    seen_queries: list[str] = []

    async def fake_search(query, ctx):
        seen_queries.append(query)
        return canned

    monkeypatch.setattr(plans_mod, "search_unit_resources", fake_search)

    r = _post_sse(client, auth_headers, plan_id)
    assert _parse_sse(r.text)[-1][0] == "done"
    # 每单元一次检索；query 含 topic（React）与阶段名
    assert len(seen_queries) == 3
    assert "React" in seen_queries[0] and "基础" in seen_queries[0]

    unit_id = _units_of(plan_id)[0].id
    r2 = client.get(f"/api/materials/{unit_id}", headers=auth_headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["resources"] == canned

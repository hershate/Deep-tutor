"""POST /plans/{id}/materials SSE 逐单元流测试（iter 46，plan/07 §三「每单元一次 LLM」）。

内容协商：Accept: text/event-stream 且需生成（无已存单元或 refresh=true）-> SSE 流；
幂等命中（已持久化未 refresh）仍返回 JSON。mock 模式复用 test_api_plans_materials 的
CannedProvider/_make_plan。SSE 解析跳过 `: keepalive` 注释行（仅认 event:/data: 行）。
"""

from __future__ import annotations

import json

import pytest

from app.llm.base import LLMProvider

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
    """SSE 全 ok：unit_start 全部前置（iter 230 R3 并发）+ unit_done 按序 + done 收尾；落库 3 单元；文件含 LLM 内容。"""
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
    # plan 有 3 阶段（基础/进阶/实战）-> 3 单元；
    # iter 230 R3：worker 并发执行，unit_start 全部前置发出，unit_done 仍按 order 顺序
    assert [e for e, _ in events] == [
        "unit_start",
        "unit_start",
        "unit_start",
        "unit_done",
        "unit_done",
        "unit_done",
        "done",
    ]
    assert events[0][1] == {"order": 1, "title": "基础", "total": 3}
    assert events[1][1] == {"order": 2, "title": "进阶", "total": 3}
    assert events[2][1] == {"order": 3, "title": "实战", "total": 3}
    assert events[3][1] == {"order": 1, "title": "基础", "degraded": False}
    assert [d["order"] for e, d in events if e == "unit_done"] == [1, 2, 3]
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


def test_materials_sse_units_concurrent_and_ordered(client, auth_headers, monkeypatch):
    """iter 230 R3：单元 worker 真实并发（重叠窗口 ≥2）；完成先后不泄漏——unit_done 严格按 order。"""
    import asyncio as _aio

    from app.tools.base import ToolOutput

    plan_id = _make_plan(client, auth_headers)
    track = {"cur": 0, "max": 0}

    async def spy_run_tool(name, inp, ctx):
        track["cur"] += 1
        track["max"] = max(track["max"], track["cur"])
        # 单元 1 故意最慢：若完成顺序泄漏到事件流，unit_done 将乱序
        await _aio.sleep(0.06 if inp.args.get("order") == 1 else 0.01)
        track["cur"] -= 1
        return ToolOutput(
            tool=name,
            status="ok",
            data={
                "objectives": ["o"],
                "prerequisites": [],
                "estimated_time": "1h",
                "content_md": f"### 概述\n\n单元 {inp.args.get('order')} 正文。",
            },
        )

    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "run_tool", spy_run_tool)
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: object())  # provider 非 None 即可（run_tool 已桩）

    r = _post_sse(client, auth_headers, plan_id)
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    assert track["max"] >= 2  # 并发真实发生（旧串行恒 1）
    assert [d["order"] for e, d in events if e == "unit_done"] == [1, 2, 3]  # 确定性顺序
    assert events[-1] == ("done", {"unit_count": 3, "degraded_count": 0})


def _make_plan_multinode(client, auth_headers):
    """多知识点 plan（8 节点，含 3 个 difficulty≥4 难点）——用于 iter 348 节点驱动切分测试。"""
    from app.api import plans as plans_mod

    class _MultiCanned(LLMProvider):
        def __init__(self):
            self.i = 0
            self.seq = [
                _resp('{"topic":"React","level":"零基础","goal":"做项目","daily_minutes":60,"deadline":null,"needs_clarify":false,"clarify_questions":[]}'),
                _resp('{"nodes":['
                      '{"id":"k1","name":"HTML","difficulty":1,"must_learn":true,"parent_id":null},'
                      '{"id":"k2","name":"CSS","difficulty":1,"must_learn":true,"parent_id":null},'
                      '{"id":"k3","name":"JS","difficulty":2,"must_learn":true,"parent_id":null},'
                      '{"id":"k4","name":"DOM","difficulty":3,"must_learn":true,"parent_id":"k3"},'
                      '{"id":"k5","name":"组件","difficulty":4,"must_learn":true,"parent_id":"k4"},'
                      '{"id":"k6","name":"Hooks","difficulty":4,"must_learn":false,"parent_id":"k5"},'
                      '{"id":"k7","name":"状态管理","difficulty":5,"must_learn":false,"parent_id":"k5"},'
                      '{"id":"k8","name":"路由","difficulty":3,"must_learn":false,"parent_id":"k5"}'
                      '],"edges":[]}'),
                _resp('{"stages":['
                      '{"index":0,"name":"基础","goal":"g","difficulty":1,"estimated_weeks":1,"topics":[{"name":"HTML"},{"name":"CSS"},{"name":"JS"}],"milestone":"m"},'
                      '{"index":1,"name":"进阶","goal":"g","difficulty":3,"estimated_weeks":1,"topics":[{"name":"DOM"},{"name":"组件"}],"milestone":"m"},'
                      '{"index":2,"name":"实战","goal":"g","difficulty":5,"estimated_weeks":1,"topics":[{"name":"状态管理"}],"milestone":"m"}]}'),
                _resp('{"schedule":[]}'),
                _resp('{"report":{"dependency_closure":{"status":"pass","details":""},"difficulty_step":{"status":"pass","details":""},"cognitive_load":{"status":"pass","details":""},"milestone_feasibility":{"status":"pass","details":""},"gap_analysis":{"status":"pass","details":""}},"passed":true,"fixes":[]}'),
            ]

        async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
            if self.i >= len(self.seq):
                return _resp("{}")
            r = self.seq[self.i]
            self.i += 1
            return r

    plans_mod.get_provider = lambda *a, **k: _MultiCanned()
    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "学 React"}, headers=auth_headers)
    import json as _json

    for block in r.text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: ") and '"plan_id"' in line:
                return _json.loads(line[6:])["plan_id"]
    raise AssertionError(f"未拿到 plan_id: {r.text}")


def test_materials_sse_multinode_more_units_and_node_sids(client, auth_headers, monkeypatch):
    """iter 348：知识点 ≥3 -> 单元数随知识点增加（>3，不再固定 3）；node_sids 持久化且全覆盖。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan_multinode(client, auth_headers)
    # 8 知识点（含 3 个 d≥4 难点）-> group_nodes_into_units 产出 >3 单元；提供足够单元响应
    provider = _UnitProvider([_unit_resp(f"### 概述\n\n单元正文 {i}.") for i in range(8)])
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: provider)

    r = _post_sse(client, auth_headers, plan_id)
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    done = [d for e, d in events if e == "done"][0]
    assert done["unit_count"] > 3  # 多知识点 -> 多于旧固定 3

    units = _units_of(plan_id)
    assert any(u.node_sids for u in units)  # node_sids 持久化
    covered = {sid for u in units for sid in (u.node_sids or [])}
    assert {"k1", "k2", "k3", "k4", "k5", "k6", "k7", "k8"} <= covered  # 全节点覆盖


def test_materials_sse_scope_node_ids_regenerates_covering_units(client, auth_headers, monkeypatch):
    """iter 348：scope.node_ids 精确选中覆盖该知识点的单元重生成（node 级粒度）。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan_multinode(client, auth_headers)
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: _UnitProvider([_unit_resp(f"### 概述\n\nv1 {i}.") for i in range(8)]))
    _post_sse(client, auth_headers, plan_id)  # 首次全量生成

    # 局部重生成：仅覆盖 k5（组件，difficulty 4，单独成单元）的单元
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: _UnitProvider([_unit_resp("### 概述\n\nv2 重生成。")]))
    r = _post_sse(client, auth_headers, plan_id, body={"scope": {"node_ids": ["k5"]}})
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    done = [d for e, d in events if e == "done"][0]
    assert done["unit_count"] == 1  # 仅 1 个单元覆盖 k5（partial 标志在 materials_completed bus 事件）

    # 该单元内容更新为 v2，其余单元保持 v1
    units = _units_of(plan_id)
    target = [u for u in units if "k5" in (u.node_sids or [])]
    assert len(target) == 1
    from pathlib import Path

    assert "v2 重生成" in Path(target[0].materials[0].content_md_path).read_text(encoding="utf-8")


def test_materials_sse_triggers_quiz_background_on_full(client, auth_headers, monkeypatch):
    """iter 349B：全量材料生成完成 -> 触发后台测验预生成调度；partial 局部重生成 -> 不触发。"""
    from app.api import plans as plans_mod

    plan_id = _make_plan(client, auth_headers)
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: _UnitProvider([_unit_resp("### 概述\n\n正文。") for _ in range(3)]))
    calls: list[tuple] = []
    monkeypatch.setattr(plans_mod, "_schedule_quiz_background", lambda pid, sid, uid: calls.append((pid, sid, uid)))

    # 全量生成 -> 触发
    r = _post_sse(client, auth_headers, plan_id)
    assert r.status_code == 200, r.text
    assert len(calls) == 1 and calls[0][0] == plan_id

    # refresh 全量重生成 -> 再次触发
    r2 = _post_sse(client, auth_headers, plan_id, body={"depth": "full", "refresh": True})
    assert r2.status_code == 200, r2.text
    assert len(calls) == 2

    # partial（scope stage）-> 不触发
    calls.clear()
    _post_sse(client, auth_headers, plan_id, body={"scope": {"stage_indexes": [0]}})
    assert calls == []

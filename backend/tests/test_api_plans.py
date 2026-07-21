"""plans/generate SSE 端点测试（mock LLM provider，不联网）。"""

from __future__ import annotations

import asyncio
import json

from app.llm.base import LLMProvider, LLMResponse, LLMUsage


def _resp(content: str) -> LLMResponse:
    return LLMResponse(content=content, usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))


# 5 个 LLM 工具的罐装响应（understand_intent / build_knowledge_map / design_stages / schedule_plan / validate_plan）
_CANNED = [
    _resp('{"topic":"React","level":"零基础","goal":"做项目","daily_minutes":60,"deadline":null,"needs_clarify":false,"clarify_questions":[]}'),
    _resp('{"nodes":[{"id":"k1","name":"JS","difficulty":1,"must_learn":true,"parent_id":null},{"id":"k2","name":"React","difficulty":3,"must_learn":true,"parent_id":"k1"}],"edges":[{"from":"k1","to":"k2","relation":"前置"}]}'),
    _resp('{"stages":[{"index":0,"name":"基础","goal":"g","difficulty":1,"estimated_weeks":1,"topics":[{"name":"JS","why":"w","must_learn":true}],"milestone":"m"},{"index":1,"name":"进阶","goal":"g","difficulty":3,"estimated_weeks":1,"topics":[],"milestone":"m"},{"index":2,"name":"实战","goal":"g","difficulty":5,"estimated_weeks":1,"topics":[],"milestone":"m"}]}'),
    _resp('{"schedule":[{"date":"2026-07-16","stage_index":0,"tasks":["学 JS"],"est_minutes":60}]}'),
    _resp('{"report":{"dependency_closure":{"status":"pass","details":""},"difficulty_step":{"status":"pass","details":""},"cognitive_load":{"status":"pass","details":""},"milestone_feasibility":{"status":"pass","details":""},"gap_analysis":{"status":"pass","details":""}},"passed":true,"fixes":[]}'),
]


class CannedProvider(LLMProvider):
    def __init__(self) -> None:
        self.i = 0

    async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
        r = _CANNED[self.i]
        self.i += 1
        return r


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        ev = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if ev:
            out.append((ev, json.loads(data) if data else {}))
    return out


def _session(client, auth_headers):
    return client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]


def test_generate_happy_path(client, auth_headers, monkeypatch):
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: CannedProvider())

    sid = _session(client, auth_headers)
    r = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "我想学 React，零基础"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    ev_names = [e for e, _ in events]
    assert "tool_start" in ev_names
    assert "tool_end" in ev_names
    assert "done" in ev_names
    # iter 184 B2：SSE 事件携带步骤序号（前端进度条）
    first_start = next(d for e, d in events if e == "tool_start")
    assert first_start["step_index"] >= 1
    assert first_start["total_steps"] == 8
    assert first_start["attempt"] == 1

    done_data = next(d for e, d in events if e == "done")
    plan_id = done_data["plan_id"]
    assert plan_id > 0

    # 列表
    lst = client.get(f"/api/plans?session_id={sid}", headers=auth_headers).json()
    assert lst["total"] >= 1
    assert any(p["id"] == plan_id for p in lst["items"])

    # 详情
    detail = client.get(f"/api/plans/{plan_id}", headers=auth_headers).json()
    assert detail["id"] == plan_id
    plan = detail["plan"]
    # plan 是 export_plan 输出 或 组装 dict
    assert isinstance(plan, dict)


def test_generate_backfills_auto_title(client, auth_headers, monkeypatch):
    """iter 184 B3：生成完成后用 intent.topic 回填自动标题（空/「学习方案」/raw_text 前缀截断）。"""
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: CannedProvider())
    raw = "我想学 React，零基础，每天一小时"
    # Home 同款截断标题（raw_text 前缀）-> 应被 topic 覆盖
    sid = client.post("/api/sessions", json={"title": raw[:40]}, headers=auth_headers).json()["session_id"]
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": raw}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "event: done" in r.text
    d = client.get(f"/api/sessions/{sid}", headers=auth_headers).json()
    assert d["title"] == "React"

    # 手工标题（非 raw_text 前缀）-> 不覆盖
    sid2 = client.post("/api/sessions", json={"title": "我的手工标题"}, headers=auth_headers).json()["session_id"]
    r2 = client.post("/api/plans/generate", json={"session_id": sid2, "raw_text": raw}, headers=auth_headers)
    assert r2.status_code == 200, r2.text
    assert "event: done" in r2.text
    d2 = client.get(f"/api/sessions/{sid2}", headers=auth_headers).json()
    assert d2["title"] == "我的手工标题"


def test_generate_multi_materials_concatenated(client, auth_headers, monkeypatch):
    """批量导入（P2，plan/README §输入形式9）：多份资料全部拼接注入 understand_intent。"""
    from app.api import plans as plans_mod

    class CaptureProvider(LLMProvider):
        def __init__(self) -> None:
            self.i = 0
            self.first_user = ""

        async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
            if self.i == 0:
                self.first_user = user
            r = _CANNED[self.i]
            self.i += 1
            return r

    provider = CaptureProvider()
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: provider)
    sid = _session(client, auth_headers)
    for text in ("资料甲：变量与函数。", "资料乙：组件与状态。"):
        r = client.post("/api/materials/text", json={"session_id": sid, "text": text}, headers=auth_headers)
        assert r.status_code == 200, r.text
    r = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "学 React"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert "done" in [e for e, _ in _parse_sse(r.text)]
    # understand_intent 的 user 消息（args.parsed + upstream.parse_material）含两份资料全文
    assert "资料甲：变量与函数。" in provider.first_user
    assert "资料乙：组件与状态。" in provider.first_user


def test_generate_no_provider_emits_error(client, auth_headers, monkeypatch):
    from app.api import plans as plans_mod

    def _raise(*a, **k):
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    monkeypatch.setattr(plans_mod, "get_provider", _raise)
    sid = _session(client, auth_headers)
    r = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "学 X"},
        headers=auth_headers,
    )
    assert r.status_code == 200  # SSE 流本身 200，错误在事件里
    events = _parse_sse(r.text)
    assert any(e == "error" for e, _ in events)


def test_generate_session_not_found(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.api.plans.get_provider", lambda *a, **k: CannedProvider())
    r = client.post(
        "/api/plans/generate",
        json={"session_id": 999999, "raw_text": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_generate_emits_increasing_event_ids(client, auth_headers, monkeypatch):
    """SSE 每事件带递增 id（plan/10 §十二 续接游标），终态持久化 last_event_id。"""
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: CannedProvider())
    sid = _session(client, auth_headers)
    r = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "学 React"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    # 解析所有 id: 行
    ids: list[int] = []
    for block in r.text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("id: "):
                ids.append(int(line[4:]))
    assert ids, "SSE 未发出任何 id 行"
    assert ids == sorted(ids), "id 非递增"
    assert len(ids) == len(set(ids)), "id 有重复"

    # done 事件应携带最大的 id
    events = _parse_sse(r.text)
    assert any(e == "done" for e, _ in events)

    # 终态 last_event_id 持久化到 orchestrator_state
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        st = db.get(models.OrchestratorState, sid)
        assert st is not None
        assert st.status == models.OrchestratorStatus.done
        assert st.last_event_id == str(ids[-1])
    finally:
        db.close()


def test_generate_done_fast_path(client, auth_headers, monkeypatch):
    """done 态下重连 /generate -> 直接回放 done {plan_id}（不重跑，无新 plan，plan/10 §十二）。"""
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: CannedProvider())
    sid = _session(client, auth_headers)
    r1 = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "学 React"},
        headers=auth_headers,
    )
    assert r1.status_code == 200, r1.text
    plan_id = next(d for e, d in _parse_sse(r1.text) if e == "done")["plan_id"]

    # 重连：应仅回放 done（同一 plan_id）；若误重跑，CannedProvider 已耗尽会报错
    r2 = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "学 React"},
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text
    ev2 = _parse_sse(r2.text)
    assert ev2 == [("done", {"plan_id": plan_id})], ev2

    # 未产生新 plan
    lst = client.get(f"/api/plans?session_id={sid}", headers=auth_headers).json()
    assert lst["total"] == 1


def test_generate_paused_clarify_fast_path(client, auth_headers, monkeypatch):
    """paused_clarify 态下重连 /generate -> 直接回放 clarify_needed（不重跑，plan/10 §十二）。"""
    from app.api import plans as plans_mod

    clarify_resp = _resp(
        '{"topic":"编程","level":null,"goal":null,"daily_minutes":null,"deadline":null,'
        '"needs_clarify":true,"clarify_questions":["想学哪方面？","每天多少时间？"]}'
    )

    class ClarifyOnly(LLMProvider):
        async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
            return clarify_resp

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: ClarifyOnly())
    sid = _session(client, auth_headers)
    r1 = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "学编程"},
        headers=auth_headers,
    )
    assert r1.status_code == 200, r1.text
    assert any(e == "clarify_needed" for e, _ in _parse_sse(r1.text))

    # 重连：应仅回放 clarify_needed（不重跑 understand_intent）
    r2 = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "学编程"},
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text
    ev2 = _parse_sse(r2.text)
    assert ev2 == [("clarify_needed", {"questions": ["想学哪方面？", "每天多少时间？"]})], ev2


def test_generate_error_fast_path(client, auth_headers, monkeypatch):
    """error 态下重连 /generate -> 回放终态 error（不自动重跑，plan/10 §十二）。"""
    from app.api import plans as plans_mod

    def _raise(*a, **k):
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    monkeypatch.setattr(plans_mod, "get_provider", _raise)
    sid = _session(client, auth_headers)
    r1 = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "x"},
        headers=auth_headers,
    )
    assert any(e == "error" for e, _ in _parse_sse(r1.text))

    # 重连：回放终态 error（iter 184 B4：带持久化的真实错误码 + retryable），不重跑（_raise 不会再被调用）
    r2 = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "x"},
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text
    ev2 = _parse_sse(r2.text)
    assert len(ev2) == 1 and ev2[0][0] == "error", ev2
    assert ev2[0][1]["code"] == "no_llm_provider"
    assert ev2[0][1]["retryable"] is True


def test_cancel_no_running_generation(client, auth_headers):
    """无运行中生成 -> cancel 409。"""
    sid = _session(client, auth_headers)
    r = client.post(f"/api/plans/{sid}/cancel", headers=auth_headers)
    assert r.status_code == 409


def test_cancel_after_done(client, auth_headers, monkeypatch):
    """生成完成后 cancel -> 409（无运行中任务）。"""
    from app.api import plans as plans_mod

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: CannedProvider())
    sid = _session(client, auth_headers)
    client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "x"}, headers=auth_headers)
    r = client.post(f"/api/plans/{sid}/cancel", headers=auth_headers)
    assert r.status_code == 409


def test_finalize_cancelled_publishes_error(client, auth_headers):
    """_finalize 处理被取消的任务 -> 发 error{cancelled} + 置 error 态（plan/10 §十一）。"""
    from app.api import plans as plans_mod
    from app.db import models
    from app.db.base import SessionLocal

    sid = _session(client, auth_headers)
    # err.md A12 后 _finalize 仅当 state.trace_id 匹配才写库：先按真实占位路径置 running
    db0 = SessionLocal()
    try:
        plans_mod._set_state(db0, sid, models.OrchestratorStatus.running, "trace_cancel")
    finally:
        db0.close()

    async def scenario():
        stream = plans_mod._SessionStream(sid, "trace_cancel")
        plans_mod._STREAMS[sid] = stream

        async def slow():
            await asyncio.sleep(100)

        task = asyncio.create_task(slow())
        stream.task = task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        plans_mod._finalize(sid, stream, task)
        return stream

    stream = asyncio.run(scenario())
    assert stream.terminal_id is not None
    _, ev, data = stream._events[-1]
    assert ev == "error"
    assert data["code"] == "cancelled"

    db = SessionLocal()
    try:
        st = db.get(models.OrchestratorState, sid)
        assert st is not None
        assert st.status == models.OrchestratorStatus.error
    finally:
        db.close()


def test_finalize_deleted_session_skips_db_no_fk_violation(client, auth_headers):
    """会话已删除时 _finalize 不写 DB（防 FK 违约），发 error{cancelled}（非 internal_error）。

    场景：DELETE /sessions 取消运行中任务 -> _finalize 见会话已删 -> 跳过 _set_state
    （否则重建 OrchestratorState 撞已删会话 FK，被 broad-except 吞成 internal_error 噪声）。
    """
    from app.api import plans as plans_mod
    from app.db import models
    from app.db.base import SessionLocal

    sid = _session(client, auth_headers)
    # 删除会话（级联删 orchestrator_state）
    assert client.delete(f"/api/sessions/{sid}", headers=auth_headers).status_code == 200

    async def scenario():
        stream = plans_mod._SessionStream(sid, "trace_del")
        plans_mod._STREAMS[sid] = stream

        async def slow():
            await asyncio.sleep(100)

        task = asyncio.create_task(slow())
        stream.task = task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        plans_mod._finalize(sid, stream, task)
        return stream

    stream = asyncio.run(scenario())
    # 发 error{cancelled}（guard 命中），非 internal_error（FK 违约被吞的旧路径）
    assert stream.terminal_id is not None
    _, ev, data = stream._events[-1]
    assert ev == "error"
    assert data["code"] == "cancelled"
    # 会话已删 -> 不重建 OrchestratorState（无 FK 违约）
    db = SessionLocal()
    try:
        assert db.get(models.OrchestratorState, sid) is None
    finally:
        db.close()


def test_regenerate_partial_rerun(client, auth_headers, monkeypatch):
    """POST /plans/{id}/regenerate {scope} -> 从 scope 级联重跑，新 plan version+1（plan/10 §十三）。"""

    class RegenProvider(LLMProvider):
        """初始生成 5 响应 + 重生成（design_stages 起）3 响应（design_stages/schedule/validate）。"""

        def __init__(self) -> None:
            self.i = 0
            self._resp = list(_CANNED) + [_CANNED[2], _CANNED[3], _CANNED[4]]

        async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
            r = self._resp[self.i]
            self.i += 1
            return r

    from app.api import plans as plans_mod

    # 共享单例：初始生成消费 _CANNED[0..4]（5 次），重生成应仅消费 [5..7]
    # （design_stages/schedule/validate 3 次）。若误从 understand_intent 全量重跑
    # （旧 bug：_run_start 硬编码 start_tool）会耗尽响应 -> error，且 i != 8。
    provider = RegenProvider()
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: provider)
    sid = _session(client, auth_headers)

    # 1. 初始生成 -> plan v1
    r1 = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "学 React"},
        headers=auth_headers,
    )
    plan_id = next(d for e, d in _parse_sse(r1.text) if e == "done")["plan_id"]

    # 2. 局部重生成（从 design_stages 起级联重跑）
    r2 = client.post(
        f"/api/plans/{plan_id}/regenerate",
        json={"scope": "design_stages", "feedback": "简化阶段"},
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text
    ev2 = _parse_sse(r2.text)
    assert any(e == "done" for e, _ in ev2), ev2
    new_plan_id = next(d for e, d in ev2 if e == "done")["plan_id"]
    assert new_plan_id != plan_id  # 新 plan

    # 3. 列表 2 个 plan；新 plan version=2
    lst = client.get(f"/api/plans?session_id={sid}", headers=auth_headers).json()
    assert lst["total"] == 2
    detail = client.get(f"/api/plans/{new_plan_id}", headers=auth_headers).json()
    assert detail["version"] == 2
    # 重跑范围最小化（plan/10 §十三）：仅 design_stages/schedule/validate 3 次 LLM 调用
    # （初始 5 + 重生成 3 = 8）；若误从 understand_intent 全量重跑则 i > 8 或耗尽报错。
    assert provider.i == 8, f"期望 8 次 LLM 调用（5 初始 + 3 重生成），实际 {provider.i}"


def test_regenerate_invalid_scope(client, auth_headers, monkeypatch):
    """scope 非有效工具名 -> 400。"""
    monkeypatch.setattr("app.api.plans.get_provider", lambda *a, **k: CannedProvider())
    sid = _session(client, auth_headers)
    r1 = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "x"},
        headers=auth_headers,
    )
    plan_id = next(d for e, d in _parse_sse(r1.text) if e == "done")["plan_id"]
    r2 = client.post(
        f"/api/plans/{plan_id}/regenerate",
        json={"scope": "nonexistent_tool"},
        headers=auth_headers,
    )
    assert r2.status_code == 400


def test_regenerate_unsupported_scope(client, auth_headers, monkeypatch):
    """scope=understand_intent（无上游 understand_intent）-> 400。"""
    monkeypatch.setattr("app.api.plans.get_provider", lambda *a, **k: CannedProvider())
    sid = _session(client, auth_headers)
    r1 = client.post(
        "/api/plans/generate",
        json={"session_id": sid, "raw_text": "x"},
        headers=auth_headers,
    )
    plan_id = next(d for e, d in _parse_sse(r1.text) if e == "done")["plan_id"]
    r2 = client.post(
        f"/api/plans/{plan_id}/regenerate",
        json={"scope": "understand_intent"},
        headers=auth_headers,
    )
    assert r2.status_code == 400


# --- iter 260：今日任务 GET /api/plans/today（首页聚合，有界扫描） ---


def _seed_plan(db, user_id: int, topic: str, schedule: list, version: int = 1) -> int:
    from app.db import models

    s = models.Session(user_id=user_id, title="t")
    db.add(s)
    db.flush()
    p = models.Plan(session_id=s.id, version=version, content={"intent": {"topic": topic}, "schedule": schedule})
    db.add(p)
    db.commit()
    return p.id


def test_today_returns_only_todays_days(client, auth_headers):
    from datetime import date

    from app.db.base import SessionLocal
    from app.db.models import User

    today = date.today().isoformat()
    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(admin)
        db.commit()
        pid = _seed_plan(
            db,
            admin.id,
            "Python 入门",
            [
                {"date": today, "stage_index": 0, "tasks": ["学变量", "做练习"], "est_minutes": 45},
                {"date": "2000-01-01", "stage_index": 1, "tasks": ["历史任务"], "est_minutes": 30},
            ],
        )
    finally:
        db.close()
    r = client.get("/api/plans/today", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == today
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["plan_id"] == pid and item["topic"] == "Python 入门"
    assert item["days"] == [{"stage_index": 0, "tasks": ["学变量", "做练习"], "est_minutes": 45}]


def test_today_isolation_and_version_dedupe(client, auth_headers):
    """他人方案的今日任务不可见；同会话多版本只扫最新版。"""
    from datetime import date

    from app.db.base import SessionLocal
    from app.db.models import User

    today = date.today().isoformat()
    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        other = User(name="other", username="other", password_hash="x", is_admin=False)
        db.add_all([admin, other])
        db.commit()
        # 他人：今天有任务——不可见
        _seed_plan(db, other.id, "他人方案", [{"date": today, "tasks": ["他人任务"], "est_minutes": 10}])
        # 自己：同会话 v1 有今日任务、v2 无（重生成后改期）——只扫最新版 v2 -> 空
        from app.db import models

        s = models.Session(user_id=admin.id, title="t")
        db.add(s)
        db.flush()
        db.add(models.Plan(session_id=s.id, version=1, content={"intent": {"topic": "旧版"}, "schedule": [{"date": today, "tasks": ["旧任务"]}]}))
        db.add(models.Plan(session_id=s.id, version=2, content={"intent": {"topic": "新版"}, "schedule": [{"date": "2000-01-01", "tasks": ["改期"]}]}))
        db.commit()
    finally:
        db.close()
    body = client.get("/api/plans/today", headers=auth_headers).json()
    assert body["items"] == []


def test_today_unauthenticated_401(client):
    assert client.get("/api/plans/today").status_code == 401

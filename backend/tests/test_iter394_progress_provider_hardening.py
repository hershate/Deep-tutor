"""iter 394 回归：进度 CAS/行删除、供应商 kind 语义与掩码、连通性测试计量与预算。

覆盖本轮落地的修复：

1. P1-4 ``test_provider``：真实计费 LLM 调用必须过日预算守卫 + 落 api_usage
   + 走全局 LLM 背压槽（此前三者皆无：预算对该调用失明、用量页不显示、可绕开信号量）。
2. P2-7 ``ProviderUpdate.kind``：显式 null / 不传 -> 保留原协议；``''`` -> 重置 anthropic
   （此前显式 null 被归一为 ''，把插件协议静默重置）。
3. P2-12 progress CAS 谓词覆盖 status：并发 status-only 变更须被检出并重放。
4. progress 行在并发窗口被级联删除 -> 返回 None（不再抛 InvalidRequestError 变 500）。
5. 同 ``(plan_id, sid)`` 重复节点 -> 取 id 最小的一条（不再 MultipleResultsFound 变 500）。
6. 掩码阈值：<8 位密钥整体掩码（此前 4~7 位密钥被完整回显）。
"""

from __future__ import annotations

import contextlib

BASE = "/api/providers"


def _create_provider(client, auth_headers, name="中转", api_key="sk-test-1234"):
    r = client.post(
        BASE,
        json={"name": name, "base_url": "https://relay.example.com", "api_key": api_key},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _mock_provider(monkeypatch, usage=None, calls=None):
    """把 providers.AnthropicProvider 换成记录调用的假实现。"""
    from app.api import providers as providers_mod
    from app.llm.base import LLMResponse, LLMUsage

    class _FakeProvider:
        def __init__(self, **kw):
            pass

        async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
            if calls is not None:
                calls.append({"model": model, "max_tokens": max_tokens})
            u = usage if usage is not None else LLMUsage(input_tokens=7, output_tokens=3, model="claude-test")
            return LLMResponse(content="pong", usage=u)

    monkeypatch.setattr(providers_mod, "AnthropicProvider", _FakeProvider)
    return _FakeProvider


# --- P1-4：连通性测试的计量 / 预算 / 背压 ---------------------------------


def test_provider_test_records_api_usage(client, auth_headers, monkeypatch):
    """iter 394：/providers/{id}/test 发起真实计费调用 -> 必须计入 api_usage。"""
    from app.db import models
    from app.db.base import SessionLocal

    p = _create_provider(client, auth_headers)
    _mock_provider(monkeypatch)

    r = client.post(f"{BASE}/{p['id']}/test", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    db = SessionLocal()
    try:
        rows = db.query(models.ApiUsage).all()
        assert len(rows) == 1, f"应有 1 条计量行，实际 {len(rows)}"
        assert rows[0].tokens_in == 7 and rows[0].tokens_out == 3
        assert rows[0].model == "claude-test"
        assert rows[0].provider == "anthropic"
        assert rows[0].user_id is not None
    finally:
        db.close()


def test_provider_test_blocked_by_daily_budget(client, auth_headers, monkeypatch):
    """iter 394：当日预算耗尽 -> 402，且**不发起** LLM 调用（不白跑计费请求）。"""
    from app.core import settings as settings_mod
    from app.db import models
    from app.db.base import SessionLocal

    p = _create_provider(client, auth_headers)
    calls: list = []
    _mock_provider(monkeypatch, calls=calls)

    monkeypatch.setattr(settings_mod.settings, "daily_budget_usd", 0.01)
    db = SessionLocal()
    try:
        uid = db.query(models.User.id).first()[0]
        db.add(
            models.ApiUsage(
                user_id=uid,
                provider="anthropic",
                model="m",
                tokens_in=1,
                tokens_out=1,
                cost=1.0,
            )
        )
        db.commit()
    finally:
        db.close()

    r = client.post(f"{BASE}/{p['id']}/test", headers=auth_headers)
    assert r.status_code == 402, r.text
    assert r.json()["error"]["code"] == "budget_exceeded"
    assert calls == [], "预算耗尽时不得发起 LLM 调用"


def test_provider_test_uses_global_llm_slot(client, auth_headers, monkeypatch):
    """iter 394：连通性测试须走全局 LLM 背压槽（此前可绕开信号量）。"""
    from app.core import concurrency as concurrency_mod

    p = _create_provider(client, auth_headers)
    _mock_provider(monkeypatch)

    entered = {"n": 0}

    @contextlib.asynccontextmanager
    async def _fake_slot():
        entered["n"] += 1
        yield

    monkeypatch.setattr(concurrency_mod, "llm_task_slot", lambda: _fake_slot())

    r = client.post(f"{BASE}/{p['id']}/test", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert entered["n"] == 1


# --- P2-7：kind 更新语义 ---------------------------------------------------


def test_update_kind_explicit_null_keeps_protocol(client, auth_headers):
    """显式 null -> 保留原协议（此前被归一为 ''，把插件协议静默重置为 anthropic）。"""
    p = _create_provider(client, auth_headers)
    r = client.put(f"{BASE}/{p['id']}", json={"kind": "openai"}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["kind"] == "openai"

    r = client.put(f"{BASE}/{p['id']}", json={"kind": None, "model": "gpt-4o"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "openai", "显式 null 不得重置协议"
    assert r.json()["model"] == "gpt-4o"

    # 不传 kind -> 保留
    r = client.put(f"{BASE}/{p['id']}", json={"model": "gpt-4o-mini"}, headers=auth_headers)
    assert r.json()["kind"] == "openai"

    # 空串 -> 显式重置为 anthropic
    r = client.put(f"{BASE}/{p['id']}", json={"kind": ""}, headers=auth_headers)
    assert r.json()["kind"] == "anthropic"


# --- 掩码阈值 ---------------------------------------------------------------


def test_short_key_not_echoed(client, auth_headers):
    """4~7 位密钥此前被完整回显（与「不回显明文」矛盾）-> 整体掩码。"""
    r = client.post(BASE, json={"name": "短钥", "api_key": "1234567"}, headers=auth_headers)
    assert r.status_code == 201, r.text
    assert r.json()["api_key_masked"] == "****"
    assert "1234567" not in r.text

    r = client.post(BASE, json={"name": "八位", "api_key": "12345678"}, headers=auth_headers)
    assert r.json()["api_key_masked"] == "...5678"


def test_short_credential_key_not_echoed(client, auth_headers):
    """凭证档案同口径。"""
    r = client.post(
        "/api/credentials",
        json={"kind": "anthropic", "name": "短钥", "api_key": "1234567"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["api_key_masked"] == "****"
    assert "1234567" not in r.text


# --- progress：CAS 谓词 / 行删除 / 重复节点 ---------------------------------


def _make_plan(client, auth_headers, monkeypatch) -> int:
    """复用 test_api_progress 的建方案路径（knowledge_nodes 落库，sid k1/k2）。"""
    import json

    from app.api import plans as plans_mod
    from app.llm.base import LLMProvider, LLMResponse, LLMUsage

    def _resp(content: str) -> LLMResponse:
        return LLMResponse(content=content, usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"))

    canned = [
        _resp('{"topic":"React","level":"零基础","goal":"做项目","daily_minutes":60,"deadline":null,"needs_clarify":false,"clarify_questions":[]}'),
        _resp('{"nodes":[{"id":"k1","name":"JS","difficulty":1,"must_learn":true,"parent_id":null},{"id":"k2","name":"React","difficulty":3,"must_learn":true,"parent_id":"k1"}],"edges":[{"from":"k1","to":"k2","relation":"前置"}]}'),
        _resp('{"stages":[{"index":0,"name":"基础","goal":"g","difficulty":1,"estimated_weeks":1,"topics":[],"milestone":"m"},{"index":1,"name":"进阶","goal":"g","difficulty":3,"estimated_weeks":1,"topics":[],"milestone":"m"},{"index":2,"name":"实战","goal":"g","difficulty":5,"estimated_weeks":1,"topics":[],"milestone":"m"}]}'),
        _resp('{"schedule":[{"date":"2026-07-16","stage_index":0,"tasks":["学 JS"],"est_minutes":60}]}'),
        _resp('{"report":{"dependency_closure":{"status":"pass","details":""},"difficulty_step":{"status":"pass","details":""},"cognitive_load":{"status":"pass","details":""},"milestone_feasibility":{"status":"pass","details":""},"gap_analysis":{"status":"pass","details":""}},"passed":true,"fixes":[]}'),
    ]

    class CannedProvider(LLMProvider):
        def __init__(self) -> None:
            self.i = 0

        async def chat(self, system, user, *, model=None, max_tokens=4096, cache_system=True):
            r = canned[self.i]
            self.i += 1
            return r

    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: CannedProvider())
    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    r = client.post("/api/plans/generate", json={"session_id": sid, "raw_text": "学 React"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    for block in r.text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data: ") and '"plan_id"' in line:
                return json.loads(line[6:])["plan_id"]
    raise AssertionError(f"未拿到 plan_id: {r.text}")


def test_progress_cas_detects_concurrent_status_change(client, auth_headers, monkeypatch):
    """iter 394：CAS 谓词须覆盖 status——并发 status-only 变更须被检出并重放。

    构造：进度行 (status=done, mastery=1.0)；本请求 status=learning + quiz_score=1.0
    （quiz 路径下 compute_mastery 与 status 无关 -> mastery 恒为 1.0，不变）。
    旧实现只比 mastery -> 并发者改 status 后谓词仍匹配，status 被静默覆盖且不重放。
    """
    from app.api import progress as progress_mod
    from app.db import models
    from app.db.base import SessionLocal

    plan_id = _make_plan(client, auth_headers, monkeypatch)
    client.post(
        "/api/progress",
        json={"plan_id": plan_id, "node_id": "k1", "status": "done", "quiz_score": 1.0},
        headers=auth_headers,
    )

    db = SessionLocal()
    try:
        row = db.query(models.Progress).filter_by(plan_id=plan_id).first()
        assert row is not None
        row_id = row.id

        calls = {"n": 0}
        real = progress_mod.compute_mastery

        def _spy(old, status, score):
            calls["n"] += 1
            if calls["n"] == 1:
                # 模拟并发写者：只改 status，mastery 不变（status-only 更新）
                other = SessionLocal()
                try:
                    other.query(models.Progress).filter_by(id=row_id).update(
                        {"status": models.ProgressStatus.not_started}
                    )
                    other.commit()
                finally:
                    other.close()
            return real(old, status, score)

        monkeypatch.setattr(progress_mod, "compute_mastery", _spy)

        body = progress_mod.ProgressUpdate(
            plan_id=plan_id,
            node_id="k1",
            status=models.ProgressStatus.learning,
            quiz_score=1.0,
        )
        out = progress_mod._apply_progress_update(db, row, body)
        assert out is not None
        assert calls["n"] >= 2, "CAS 失配后必须重放（旧实现 mastery 未变时只算 1 次）"
        assert out.status == models.ProgressStatus.learning
        assert out.mastery == 1.0
    finally:
        db.close()


def test_apply_progress_update_returns_none_when_row_deleted(client, auth_headers, monkeypatch):
    """iter 394：进度行在并发窗口被级联删除 -> 返回 None（调用方据此放弃本次更新）。

    旧实现返回已删行实例，调用方随后 db.refresh(row) 抛 InvalidRequestError -> 500。
    """
    from app.api import progress as progress_mod
    from app.db import models
    from app.db.base import SessionLocal

    plan_id = _make_plan(client, auth_headers, monkeypatch)
    client.post(
        "/api/progress",
        json={"plan_id": plan_id, "node_id": "k1", "status": "done", "quiz_score": 1.0},
        headers=auth_headers,
    )

    db = SessionLocal()
    try:
        row = db.query(models.Progress).filter_by(plan_id=plan_id).first()
        row_id = row.id
        # 用独立会话删行（模拟并发窗口内被级联删除），本会话对象保持可读
        other = SessionLocal()
        try:
            other.query(models.Progress).filter_by(id=row_id).delete()
            other.commit()
        finally:
            other.close()

        body = progress_mod.ProgressUpdate(
            plan_id=plan_id,
            node_id="k1",
            status=models.ProgressStatus.learning,
            quiz_score=None,
        )
        out = progress_mod._apply_progress_update(db, row, body)
        assert out is None, "行已删除时必须返回 None"
    finally:
        db.close()


def test_update_progress_duplicate_node_sid_picks_first(client, auth_headers, monkeypatch):
    """同 (plan_id, sid) 重复节点 -> 取 id 最小的一条（不再 MultipleResultsFound -> 500）。"""
    from app.db import models
    from app.db.base import SessionLocal

    plan_id = _make_plan(client, auth_headers, monkeypatch)

    db = SessionLocal()
    try:
        first = db.query(models.KnowledgeNode).filter_by(plan_id=plan_id, sid="k1").first()
        assert first is not None
        db.add(
            models.KnowledgeNode(
                plan_id=plan_id,
                sid="k1",
                name="重复节点",
                difficulty=1,
                must_learn=False,
            )
        )
        db.commit()
    finally:
        db.close()

    r = client.post(
        "/api/progress",
        json={"plan_id": plan_id, "node_id": "k1", "status": "learning"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["updated"] is True
    assert r.json()["status"] == "learning"


# --- P2-11：/setup/status 的 llm_configured 按用户解析 -----------------------


def _mk_user(username: str, password: str = "pw12345678", is_admin: bool = False) -> int:
    from app.core.security import hash_password
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(
            name=username,
            username=username,
            password_hash=hash_password(password),
            is_admin=is_admin,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _login(client, username: str, password: str = "pw12345678") -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_setup_status_llm_configured_is_per_user(client, monkeypatch):
    """iter 394：llm_configured 按调用者解析——甲配了 key，乙没配 -> 乙看到「未配置」。

    此前用全局查询（任一用户的 active 档案即算已配置）：多用户下所有用户看到同一个
    值，未配 key 的用户横幅不出现而 LLM 调用必失败。
    """
    from app.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "")  # .env 无 key

    _mk_user("alice")
    _mk_user("bob")
    alice = _login(client, "alice")
    bob = _login(client, "bob")

    # 甲配置并启用供应商档案
    r = client.post(
        BASE,
        json={"name": "甲的中转", "base_url": "https://relay.example.com", "api_key": "sk-alice-1234"},
        headers=alice,
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert client.post(f"{BASE}/{pid}/activate", headers=alice).status_code == 200

    # 甲：已配置
    body = client.get("/api/setup/status", headers=alice).json()
    assert body["llm_configured"] is True
    # 乙：未配置（全局口径会误报 True）
    body = client.get("/api/setup/status", headers=bob).json()
    assert body["llm_configured"] is False
    # 无凭据（公开访问）：实例级口径——甲配过 -> 实例已配置
    body = client.get("/api/setup/status").json()
    assert body["llm_configured"] is True
    # needs_setup 仍是实例级判定：普通用户不会被弹去 /setup
    assert client.get("/api/setup/status", headers=bob).json()["needs_setup"] is False


def test_setup_status_public_get_creates_no_user(client, monkeypatch):
    """iter 394：公开 GET /setup/status 不产生建用户的写副作用。"""
    from app.core import settings as settings_mod
    from app.db.base import SessionLocal
    from app.db.models import User

    monkeypatch.setattr(settings_mod.settings, "auth_token", "")
    monkeypatch.setattr(settings_mod.settings, "anthropic_api_key", "")

    assert client.get("/api/setup/status").status_code == 200
    db = SessionLocal()
    try:
        assert db.query(User.id).count() == 0, "公开状态查询不得创建用户"
    finally:
        db.close()


# --- P2-10：配额 check-then-act 竞态 ---------------------------------------


def test_credential_quota_not_bypassed_by_concurrency(client, auth_headers):
    """iter 394：并发创建不得突破每类 20 个上限。

    配额判定是「先查后写」：计数与插入之间若无互斥，25 个并发请求可同时通过
    「未达上限」检查并写入 25 行（上限被并发绕过）。修复后：恰好 20 个成功。
    """
    from concurrent.futures import ThreadPoolExecutor

    from app.db import models
    from app.db.base import SessionLocal

    def _create(i: int):
        return client.post(
            "/api/credentials",
            json={"kind": "bing", "name": f"并发键{i}", "api_key": "sk-concurrent-0001"},
            headers=auth_headers,
        )

    with ThreadPoolExecutor(max_workers=25) as ex:
        results = list(ex.map(_create, range(25)))

    codes = [r.status_code for r in results]
    assert codes.count(201) == 20, f"应恰好 20 个成功，实际 {codes.count(201)}（{codes}）"
    assert codes.count(429) == 5, f"其余应被配额拒绝，实际 {codes}"

    db = SessionLocal()
    try:
        assert db.query(models.CredentialProfile).count() == 20
    finally:
        db.close()

"""iter 397-B9 回归：产品裁决落地（A10 模型贯通 / A2+A17 审批链路收敛）。

用户 2026-10-21 问卷定夺的三项裁决中，T3（target_kind=material）已在 B6 落地；
本文件覆盖另两项：

- **A10（模型贯通）**：`get_provider()` 此前**没有** model 参数——`RunIn.model` 只被
  写进 `runs.model`，provider 仍用档案/默认模型 -> 界面「看起来可切、实际不切」；
  且 `runs.model` 存的是请求里的名字（客户端未指定时为空），与真实调用模型对不上。
  本次：`get_provider(..., model=)` 显式覆盖 + **实际模型回写 `runs.model`**。
- **A2/A17（审批链路）**：`promote_artifact` 提议后 run 停在 `awaiting_approval`，
  而 `cancel_run` 只受理仍在流注册表内的 run（finalize 已摘除 -> 恒 409
  `not_running`）-> **提议无任何出口**：用户不能批准、不能放弃、也不会过期
  （`RunStatus.cancelled` 运行时从不写入）。本次：cancel 受理待审批 run +
  维护轮 TTL 过期收敛。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.db import models
from app.db.base import SessionLocal


def _user_and_thread(db, name="u399"):
    u = models.User(name=name, username=name, password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    t = models.Thread(user_id=u.id, title=f"t-{name}")
    db.add(t)
    db.commit()
    db.refresh(t)
    return u, t


# ---------------------------------------------------------------------------
# A10：模型覆盖贯通 + 实际模型回写
# ---------------------------------------------------------------------------


def test_get_provider_model_override_wins_over_profile():
    """A10：显式 model 覆盖档案 model（此前 provider 恒用档案/默认模型）。"""
    from app.llm import get_provider

    db = SessionLocal()
    try:
        u, _t = _user_and_thread(db)
        db.add(
            models.LlmProviderProfile(
                user_id=u.id,
                name="p1",
                api_key="sk-test",
                model="profile-model",
                base_url="",
                kind="anthropic",
                is_active=True,
            )
        )
        db.commit()
        uid = u.id
    finally:
        db.close()

    assert get_provider(uid)._model == "profile-model", "不传覆盖时行为不变"
    assert get_provider(uid, model="override-model")._model == "override-model"
    assert get_provider(uid, model="")._model == "profile-model", "空串不视为覆盖"


def test_get_provider_model_override_applies_to_env_fallback(monkeypatch):
    """A10：无档案（回退 .env key）时覆盖同样生效。"""
    from app.core.settings import settings
    from app.llm import get_provider
    from app.llm.anthropic_provider import AnthropicProvider

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-ant-fallback-test")

    db = SessionLocal()
    try:
        u, _t = _user_and_thread(db)
        uid = u.id
    finally:
        db.close()

    p = get_provider(uid, model="fallback-override")
    assert isinstance(p, AnthropicProvider)
    assert p._model == "fallback-override"


def test_run_chat_writes_back_actual_model(monkeypatch):
    """A10：实际模型回写 `runs.model`（此前存的是请求里的名字，可能为空）。"""
    from app.agent.agent_loop import AgentRunResult
    from app.api import memory as mem_mod
    from app.llm.anthropic_provider import AnthropicProvider
    from app.llm.base import LLMUsage

    db = SessionLocal()
    try:
        u, t = _user_and_thread(db)
        r = models.Run(
            thread_id=t.id,
            user_id=u.id,
            kind=models.RunKind.chat,
            status=models.RunStatus.running,
            model="requested-model",
        )
        db.add(r)
        db.commit()
        db.refresh(r)
        run_id, uid, tid = r.id, u.id, t.id
    finally:
        db.close()

    seen: dict = {}

    def _fake_get_provider(user_id=None, db=None, model=None):
        seen["model"] = model
        return AnthropicProvider(api_key="sk-test", model="actual-model")

    async def _fake_loop(ctx, **kwargs):
        return AgentRunResult(status="done", final_text="ok", steps=1, usage=LLMUsage())

    monkeypatch.setattr(mem_mod, "get_provider", _fake_get_provider)
    monkeypatch.setattr(mem_mod, "run_agent_loop", _fake_loop)

    stream = mem_mod._ChatStream()

    async def _drive():
        await mem_mod._run_chat(
            run_id,
            tid,
            uid,
            "hello",
            stream,
            max_steps=3,
            thinking=False,
            model_override="requested-model",
        )

    asyncio.run(_drive())

    assert seen["model"] == "requested-model", "模型覆盖必须传到 provider 工厂"
    db2 = SessionLocal()
    try:
        row = db2.get(models.Run, run_id)
        assert row.model == "actual-model", f"实际模型未回写：{row.model}"
    finally:
        db2.close()


def test_start_run_passes_effective_model(monkeypatch):
    """A10：生效模型 = 请求指定 > 会话默认（thread.model），并传给 run。"""
    from app.api import memory as mem_mod
    from app.api.memory import RunIn

    db = SessionLocal()
    try:
        u, t = _user_and_thread(db)
        t.model = "thread-model"
        db.commit()
        db.refresh(t)
        tid, uid = t.id, u.id
    finally:
        db.close()

    seen: dict = {}

    async def _fake_run_chat(run_id, thread_id, user_id, message, stream, **kwargs):
        seen.update(kwargs)
        stream.publish("done", {"run_id": run_id, "status": "done"})

    monkeypatch.setattr(mem_mod, "_run_chat", _fake_run_chat)

    async def _drive():
        resp = await mem_mod.start_run(
            tid,
            RunIn(message="hi"),
            SimpleNamespace(headers={}),
            SimpleNamespace(id=uid),
            db,
        )
        for task in list(mem_mod._CHAT_RUN_TASKS):
            await task
        return resp

    db = SessionLocal()
    try:
        asyncio.run(_drive())
    finally:
        db.close()

    assert seen.get("model_override") == "thread-model", seen


# ---------------------------------------------------------------------------
# A2/A17：待审批 run 的取消入口
# ---------------------------------------------------------------------------


def test_cancel_run_accepts_awaiting_approval(client, auth_headers):
    """A2/A17：待审批 run 可被取消（此前恒 409 not_running，用户无法放弃提议）。"""
    db = SessionLocal()
    try:
        u, t = _user_and_thread(db)
        r = models.Run(
            thread_id=t.id,
            user_id=u.id,
            kind=models.RunKind.chat,
            status=models.RunStatus.awaiting_approval,
        )
        db.add(r)
        db.commit()
        db.refresh(r)
        run_id = r.id
    finally:
        db.close()

    resp = client.post(f"/api/runs/{run_id}/cancel", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json().get("status") == "cancelled", resp.text

    db2 = SessionLocal()
    try:
        row = db2.get(models.Run, run_id)
        assert row.status == models.RunStatus.cancelled
        assert row.ended_at is not None, "收敛必须落 ended_at"
    finally:
        db2.close()


def test_cancel_run_still_409_for_finished_run(client, auth_headers):
    """A2/A17：非待审批、非在流的 run 仍 409 not_running（语义不变）。"""
    db = SessionLocal()
    try:
        u, t = _user_and_thread(db)
        r = models.Run(
            thread_id=t.id,
            user_id=u.id,
            kind=models.RunKind.chat,
            status=models.RunStatus.done,
        )
        db.add(r)
        db.commit()
        db.refresh(r)
        run_id = r.id
    finally:
        db.close()

    resp = client.post(f"/api/runs/{run_id}/cancel", headers=auth_headers)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["message"] == "not_running", resp.text


def test_approve_after_cancel_conflicts(client, auth_headers):
    """A2/A17：取消后不可再批准（CAS 抢占：不得把已收敛的提议复活）。"""
    db = SessionLocal()
    try:
        u, t = _user_and_thread(db)
        r = models.Run(
            thread_id=t.id,
            user_id=u.id,
            kind=models.RunKind.chat,
            status=models.RunStatus.awaiting_approval,
        )
        db.add(r)
        db.commit()
        db.refresh(r)
        run_id = r.id
        db.add(
            models.RunStep(
                run_id=run_id,
                step_index=0,
                type="approval",
                name="promote_artifact",
                input={"name": "x.md", "target_kind": "note", "artifact_id": 999},
            )
        )
        db.commit()
    finally:
        db.close()

    resp = client.post(f"/api/runs/{run_id}/cancel", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    resp2 = client.post(f"/api/runs/{run_id}/approve", headers=auth_headers, json={})
    assert resp2.status_code == 409, resp2.text
    assert resp2.json()["error"]["message"] == "not_awaiting_approval", resp2.text


# ---------------------------------------------------------------------------
# A2/A17：TTL 过期收敛
# ---------------------------------------------------------------------------


def _mk_awaiting(db, u, t, started_at, proposal_at):
    r = models.Run(
        thread_id=t.id,
        user_id=u.id,
        kind=models.RunKind.chat,
        status=models.RunStatus.awaiting_approval,
        started_at=started_at,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    db.add(
        models.RunStep(
            run_id=r.id,
            step_index=0,
            type="approval",
            name="promote_artifact",
            input={"name": "x.md", "target_kind": "note", "artifact_id": 1},
            created_at=proposal_at,
        )
    )
    db.commit()
    return r


def test_expire_stale_approvals_converges_only_overdue(monkeypatch):
    """A2/A17：超时未处理的提议收敛为 cancelled；未超时的不动。"""
    from app.core.maintenance import expire_stale_approvals
    from app.core.settings import settings

    monkeypatch.setattr(settings, "awaiting_approval_ttl_seconds", 3600)
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        u, t = _user_and_thread(db)
        old = _mk_awaiting(db, u, t, now - timedelta(days=2), now - timedelta(days=2))
        fresh = _mk_awaiting(db, u, t, now - timedelta(minutes=30), now - timedelta(minutes=30))
        # 长 run、刚提出的提议：判据取**提议时刻**，不得误判为超时
        long_run = _mk_awaiting(db, u, t, now - timedelta(days=2), now - timedelta(minutes=5))
        ids = (old.id, fresh.id, long_run.id)
    finally:
        db.close()

    n = expire_stale_approvals()
    assert n == 1, f"应收敛 1 个，实际 {n}"

    db2 = SessionLocal()
    try:
        assert db2.get(models.Run, ids[0]).status == models.RunStatus.cancelled
        assert db2.get(models.Run, ids[1]).status == models.RunStatus.awaiting_approval
        assert db2.get(models.Run, ids[2]).status == models.RunStatus.awaiting_approval
        assert db2.get(models.Run, ids[0]).ended_at is not None
    finally:
        db2.close()


def test_expire_stale_approvals_disabled(monkeypatch):
    """A2/A17：TTL=0 时不收敛（禁用自动过期）。"""
    from app.core.maintenance import expire_stale_approvals
    from app.core.settings import settings

    monkeypatch.setattr(settings, "awaiting_approval_ttl_seconds", 0)
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        u, t = _user_and_thread(db)
        r = _mk_awaiting(db, u, t, now - timedelta(days=9), now - timedelta(days=9))
        rid = r.id
    finally:
        db.close()

    assert expire_stale_approvals() == 0
    db2 = SessionLocal()
    try:
        assert db2.get(models.Run, rid).status == models.RunStatus.awaiting_approval
    finally:
        db2.close()


def test_run_maintenance_reports_expired_approvals(monkeypatch):
    """A2/A17：维护轮汇总包含收敛计数（运维可观测）。"""
    from app.core import maintenance

    monkeypatch.setattr(maintenance, "expire_stale_approvals", lambda: 3)
    stats = maintenance.run_maintenance()
    assert stats.get("awaiting_approval_expired") == 3, stats

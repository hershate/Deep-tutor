"""iter 369 第三轮 B17 回归：chat/run 生命周期——取消记账/硬取消/入参约束/工具配额。

覆盖点：
- ThreadIn kind 白名单 + title/model 长度上界（422）
- RunIn max_steps 上下界（422，校验层挡住不进 run）
- _run_chat 硬取消路径保留 partial_usage（run.budget_used 不再被空 usage 抹零）
- _finalize_chat 收口窗口吃二次取消（commit_run_workspace 抛 CancelledError 时终态 SSE 照发）
- _cancel_run_stream 软+硬取消（跨线程 call_soon_threadsafe(task.cancel)）
- memory_save 工具配额族（60/min 限流、20000 字符、2000 条/用户——与 REST /memories 同口径）
- memory_delete / search_messages to_thread 化后功能不回退
"""

from __future__ import annotations

import asyncio
import threading
import uuid

import pytest

import app.api.memory as mem
from app.db import models
from app.db.base import SessionLocal
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMUsage
from app.tools import agent_tools as A
from app.tools.base import ToolInput


class _FakeChatProvider(AnthropicProvider):
    """AnthropicProvider 子类（过 isinstance 检查），stream_agentic 按脚本吐块。"""

    def __init__(self):  # noqa: D107
        self._model = "fake-model"
        self._client = None

    async def stream_agentic(self, messages, *, system="", tools=None, thinking=False):  # noqa: ARG002
        from app.llm.base import AgenticStreamChunk

        for c in [
            AgenticStreamChunk(text="ok"),
            AgenticStreamChunk(usage=LLMUsage(model="fake-model"), stop_reason="end_turn"),
        ]:
            yield c


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    import json

    out = []
    for block in text.split("\n\n"):
        ev = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if ev:
            out.append((ev, data))
    return out


def _setup_user_thread():
    db = SessionLocal()
    u = models.User(name="b17", username=f"b17-{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    t = models.Thread(user_id=u.id, title="t")
    db.add(t)
    db.commit()
    db.refresh(t)
    return db, u, t


# --- 入参约束（校验层） ---


def test_thread_input_validation(client, auth_headers):
    assert client.post("/api/threads", json={"kind": "evil"}, headers=auth_headers).status_code == 422
    assert client.post("/api/threads", json={"title": "x" * 257}, headers=auth_headers).status_code == 422
    assert client.post("/api/threads", json={"model": "m" * 129}, headers=auth_headers).status_code == 422
    # 白名单内的两个 kind 均合法
    assert client.post("/api/threads", json={"kind": "plan_generation"}, headers=auth_headers).status_code == 200


def test_run_max_steps_bounds(client, auth_headers):
    t = client.post("/api/threads", json={}, headers=auth_headers).json()
    for bad in (0, -1, 201):
        r = client.post(
            f"/api/threads/{t['id']}/runs",
            json={"message": "x", "max_steps": bad},
            headers={**auth_headers, "Accept": "text/event-stream"},
        )
        assert r.status_code == 422, bad


# --- 硬取消保留 partial_usage ---


def test_run_hard_cancel_keeps_partial_usage(client, auth_headers, monkeypatch):
    async def fake_loop(*a, **k):
        exc = asyncio.CancelledError()
        exc.partial_usage = LLMUsage(input_tokens=100, output_tokens=50, model="fake-model")
        raise exc

    monkeypatch.setattr(mem, "get_provider", lambda *a, **k: _FakeChatProvider())
    monkeypatch.setattr(mem, "run_agent_loop", fake_loop)
    seen: list[tuple[int, int]] = []

    def fake_cost(usage, pricing=None):  # noqa: ARG001
        seen.append((usage.input_tokens, usage.output_tokens))
        return 0.5

    monkeypatch.setattr(mem, "usage_cost", fake_cost)

    t = client.post("/api/threads", json={}, headers=auth_headers).json()
    r = client.post(
        f"/api/threads/{t['id']}/runs",
        json={"message": "hi"},
        headers={**auth_headers, "Accept": "text/event-stream"},
    )
    events = _parse_sse(r.text)
    assert events[-1][0] == "done" and events[-1][1]["status"] == "interrupted"
    db = SessionLocal()
    try:
        run = db.query(models.Run).filter_by(thread_id=t["id"]).first()
        assert run is not None and run.status.value == "interrupted"
        assert run.error == {"message": "cancelled"}
        # 此前空 usage 覆盖会把 budget_used 抹成 0——现在携带循环已累计的部分
        assert run.budget_used == 0.5
    finally:
        db.close()
    assert seen and seen[0] == (100, 50)


def test_finalize_survives_cancel_during_workspace_commit(client, auth_headers, monkeypatch):
    """收口窗口吃到二次取消：commit_run_workspace 被取消打断时终态 SSE 照发、流照清。"""

    def boom(*a, **k):
        raise asyncio.CancelledError()

    monkeypatch.setattr(mem, "get_provider", lambda *a, **k: _FakeChatProvider())
    monkeypatch.setattr(mem, "commit_run_workspace", boom)

    t = client.post("/api/threads", json={}, headers=auth_headers).json()
    r = client.post(
        f"/api/threads/{t['id']}/runs",
        json={"message": "hi"},
        headers={**auth_headers, "Accept": "text/event-stream"},
    )
    events = _parse_sse(r.text)
    # 取消被吞、收口继续：run 落 done（而非卡 running / 无终态 SSE）
    assert events[-1][0] == "done" and events[-1][1]["status"] == "done"
    db = SessionLocal()
    try:
        run = db.query(models.Run).filter_by(thread_id=t["id"]).first()
        assert run is not None and run.status.value == "done"
        assert run.workspace_committed is False  # 搬移线程被弃，标志未置
        rid = run.id
    finally:
        db.close()
    assert rid not in mem._RUN_STREAMS  # 终态后流必清（防泄漏）


def test_cancel_run_stream_soft_then_hard():
    """_cancel_run_stream：先 request_cancel（软），再跨线程 call_soon_threadsafe(task.cancel)。"""
    loop = asyncio.new_event_loop()
    hit: list = []

    async def hang():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            hit.append("cancelled")
            raise

    task = loop.create_task(hang())

    class _FakeStream:
        def __init__(self):
            self.task = task
            self._loop = loop

        def request_cancel(self):
            hit.append("soft")

    key = 999_999_999
    mem._RUN_STREAMS[key] = _FakeStream()
    try:
        timer = threading.Timer(0.1, lambda: mem._cancel_run_stream(key))
        timer.start()
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(task)
        timer.join()
        assert hit == ["soft", "cancelled"]  # 软取消先到，硬取消随后打断在途 await
    finally:
        mem._RUN_STREAMS.pop(key, None)
        loop.close()


# --- memory_save 工具配额族（与 REST /memories 同口径） ---


def _ctx_for(u, t, db):
    from app.agent.context import ExecContext

    ctx = ExecContext(session_id=str(t.id), trace_id="tr", db=db, user_id=u.id)
    ctx.thread_id = t.id
    return ctx


def test_memory_save_length_and_rate_caps(monkeypatch):
    db, u, t = _setup_user_thread()
    ctx = _ctx_for(u, t, db)
    try:
        # 20000 字符上限（与 REST MemoryIn max_length 同口径）
        out = asyncio.run(
            A.memory_save(ToolInput(tool="memory_save", session_id="1", args={"content": "x" * 20001}), ctx)
        )
        assert out.status == "error" and out.errors[0].code == "too_long"
        # 60/min 限流：60 次成功后第 61 次拒（每用例独立用户，键不与其他用例串味）
        for i in range(60):
            out = asyncio.run(
                A.memory_save(ToolInput(tool="memory_save", session_id="1", args={"content": f"m{i}"}), ctx)
            )
            assert out.status == "ok", i
        out = asyncio.run(
            A.memory_save(ToolInput(tool="memory_save", session_id="1", args={"content": "overflow"}), ctx)
        )
        assert out.status == "error" and out.errors[0].code == "rate_limited"
    finally:
        db.close()


def test_memory_save_user_quota(monkeypatch):
    db, u, t = _setup_user_thread()
    ctx = _ctx_for(u, t, db)
    monkeypatch.setattr(A, "_MAX_MEMORIES_PER_USER", 2)
    try:
        for i in range(2):
            out = asyncio.run(
                A.memory_save(ToolInput(tool="memory_save", session_id="1", args={"content": f"m{i}"}), ctx)
            )
            assert out.status == "ok", i
        out = asyncio.run(A.memory_save(ToolInput(tool="memory_save", session_id="1", args={"content": "over"}), ctx))
        assert out.status == "error" and out.errors[0].code == "quota_exceeded"
        assert db.query(models.Memory).filter_by(user_id=u.id).count() == 2
    finally:
        db.close()


def test_memory_delete_and_search_messages_to_thread():
    """to_thread 化后功能不回退：删归属校验 + 会话内关键词搜索。"""
    db, u, t = _setup_user_thread()
    ctx = _ctx_for(u, t, db)
    try:
        m = models.Memory(user_id=u.id, scope="user", content="keep")
        db.add(m)
        db.add_all(
            [
                models.Message(
                    thread_id=t.id, role=models.MessageRole.user, content=[{"type": "text", "text": "量子力学入门"}]
                ),
                models.Message(
                    thread_id=t.id,
                    role=models.MessageRole.assistant,
                    content=[{"type": "text", "text": "从波函数开始"}],
                ),
            ]
        )
        db.commit()
        db.refresh(m)

        out = asyncio.run(A.memory_delete(ToolInput(tool="memory_delete", session_id="1", args={"id": m.id}), ctx))
        assert out.status == "ok" and out.data["deleted"] == m.id
        assert db.query(models.Memory).filter_by(id=m.id).count() == 0
        out = asyncio.run(A.memory_delete(ToolInput(tool="memory_delete", session_id="1", args={"id": m.id}), ctx))
        assert out.status == "error" and out.errors[0].code == "not_found"

        out = asyncio.run(
            A.search_messages(ToolInput(tool="search_messages", session_id="1", args={"q": "波函数"}), ctx)
        )
        assert out.status == "ok" and out.data["count"] == 1
        assert "波函数" in out.data["matches"][0]["snippet"]
    finally:
        db.close()

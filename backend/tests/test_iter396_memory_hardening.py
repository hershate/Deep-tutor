"""iter 396 回归：记忆系统（threads/runs）删除与提升路径加固。

对应本轮 memory.py 的五处修复：

1. **M1 删 thread 与在途 chat run 的写并发**——``DELETE /api/threads/{id}`` 此前
   只对 running run 调 ``_cancel_run_stream``（cancel 只是调度），**不等待收敛**
   就删行；而 agent 的落库写（``_persist_message`` / ``_persist_run_step``，走
   ``asyncio.to_thread`` **不可中断**）仍可能与删除事务交错：

   - 写入落在「子行已删、thread 行未删」之间 -> 删 thread 行触发
     ``FOREIGN KEY constraint failed`` -> **500 且 thread 未删**；
   - 写入落在 thread 行删除之后 -> 写入自身 FK 失败 -> 异常冒到 SSE。

   修复：先 cancel 再等在途 run 收敛（``_wait_runs_settled``，同 sessions.py 范式），
   并对删除做**有界重试**（吸收「已在执行中的 to_thread 写」的残留窗口）。
2. **M3 提升为笔记绕过 notes 的上限与配额**——此前直接建 Note：标题未截 256、
   正文未截 20000、不检查 500 条配额。
3. **M5 chat run 异常把 ``str(exc)`` 回给客户端**——含表名/列名/SQL 片段。
4. **M6 工作区产物读取无上限**——``p.read_text()`` 全量读入内存并整份返回。
5. **M2 会话列表 N+1**——逐条 2 次查询（limit=100 即 201 次）。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event

from app.core.settings import settings
from app.db import models
from app.db.base import SessionLocal, engine


def _uid(client, auth_headers) -> int:
    return client.get("/api/auth/me", headers=auth_headers).json()["id"]


# ---------------------------------------------------------------------------
# 1. 删 thread：等在途 run 收敛 + FK 冲突重试
# ---------------------------------------------------------------------------


def test_delete_thread_with_running_run_succeeds(client, auth_headers):
    """thread 挂着 running run 时删除仍成功（run 行一并级联删除）。"""
    uid = _uid(client, auth_headers)
    t = client.post("/api/threads", json={"title": "待删"}, headers=auth_headers).json()
    db = SessionLocal()
    try:
        db.add(models.Run(thread_id=t["id"], user_id=uid, status=models.RunStatus.running))
        db.commit()
    finally:
        db.close()

    r = client.delete(f"/api/threads/{t['id']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        assert db.get(models.Thread, t["id"]) is None
    finally:
        db.close()


def test_delete_thread_retries_after_concurrent_message_insert(client, auth_headers):
    """并发写落在「子行已删、thread 行未删」之间 -> FK 失败；有界重试后应成功。

    用 SQL 事件在 ``DELETE FROM threads`` 执行前插入一条引用该 thread 的 message
    ——精确复现竞态窗口（修复前：IntegrityError -> 500 且 thread 未删）。
    """
    t = client.post("/api/threads", json={"title": "竞态"}, headers=auth_headers).json()
    tid = t["id"]
    state = {"injected": False}

    @event.listens_for(engine, "before_cursor_execute")
    def _inject(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        if state["injected"]:
            return
        if statement.strip().upper().startswith("DELETE FROM THREADS"):
            state["injected"] = True
            cursor.execute(
                "INSERT INTO messages (thread_id, role, content, tokens_in, tokens_out, model, created_at) "
                "VALUES (?, 'assistant', '[]', 0, 0, '', CURRENT_TIMESTAMP)",
                (tid,),
            )

    try:
        r = client.delete(f"/api/threads/{tid}", headers=auth_headers)
    finally:
        event.remove(engine, "before_cursor_execute", _inject)

    assert state["injected"], "未触发注入（SQL 形态变化？）——测试需更新"
    assert r.status_code == 200, f"应重试后成功，实际 {r.status_code}：{r.text}"
    db = SessionLocal()
    try:
        assert db.get(models.Thread, tid) is None
        assert db.query(models.Message).filter_by(thread_id=tid).count() == 0
    finally:
        db.close()


def test_wait_runs_settled_returns_when_no_stream():
    """无在途流时等待立即返回（不空转 10s）。"""
    from app.api.memory import _wait_runs_settled

    assert _wait_runs_settled([999_999], timeout=5.0) is True


# ---------------------------------------------------------------------------
# 2. 提升为笔记：复用 notes 的上限与配额
# ---------------------------------------------------------------------------


def _seed_approval(db, uid: int, *, name: str, body: str, filename: str) -> int:
    """造一个 awaiting_approval 的 run + artifact + approval step，返回 run_id。"""
    th = models.Thread(user_id=uid, title="t", kind="chat")
    db.add(th)
    db.commit()
    db.refresh(th)
    run = models.Run(thread_id=th.id, user_id=uid, status=models.RunStatus.awaiting_approval)
    db.add(run)
    db.commit()
    db.refresh(run)
    rel = f"iter396_{filename}"
    (Path(settings.data_dir) / rel).write_text(body, encoding="utf-8")
    art = models.RunArtifact(thread_id=th.id, run_id=run.id, name=name, path=rel, kind="draft")
    db.add(art)
    db.commit()
    db.refresh(art)
    db.add(
        models.RunStep(
            run_id=run.id,
            type="approval",
            input={"name": name, "target_kind": "note", "artifact_id": art.id},
        )
    )
    db.commit()
    return run.id


def test_approve_promote_note_respects_title_and_content_caps(client, auth_headers):
    """提升为笔记时标题截 256、正文截 20000（此前直接全文入库）。"""
    uid = _uid(client, auth_headers)
    long_title = "标" * 400
    long_body = "正文" * 30_000  # > 20000 字
    db = SessionLocal()
    try:
        run_id = _seed_approval(db, uid, name=long_title, body=long_body, filename="caps.md")
    finally:
        db.close()

    r = client.post(f"/api/runs/{run_id}/approve", json={"target_kind": "note"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        n = db.query(models.Note).filter_by(user_id=uid).first()
        assert n is not None, "应生成笔记"
        assert len(n.title) <= 256, len(n.title)
        assert len(n.content) <= 20_000, len(n.content)
    finally:
        db.close()


def test_approve_promote_note_respects_quota(client, auth_headers):
    """笔记数已达 500 时提升被拒（429）——此前完全绕过配额。"""
    uid = _uid(client, auth_headers)
    db = SessionLocal()
    try:
        db.add_all([models.Note(user_id=uid, title=f"n{i}", content="x") for i in range(500)])
        db.commit()
        run_id = _seed_approval(db, uid, name="越配额", body="内容", filename="quota.md")
    finally:
        db.close()

    r = client.post(f"/api/runs/{run_id}/approve", json={"target_kind": "note"}, headers=auth_headers)
    assert r.status_code == 429, r.text
    db = SessionLocal()
    try:
        assert db.query(models.Note).filter_by(user_id=uid).count() == 500
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3. chat run 异常不回显内部细节
# ---------------------------------------------------------------------------


def test_chat_run_error_does_not_leak_internals(client, auth_headers, monkeypatch):
    """agent 抛异常时，SSE error 的 message 不得含异常文本（表名/SQL 片段）。"""
    from app.llm.anthropic_provider import AnthropicProvider

    secret = "FOREIGN KEY constraint failed: messages.thread_id"

    class _P(AnthropicProvider):
        def __init__(self):
            self._model = "fake"
            self._client = None

        async def stream_agentic(self, messages, *, system="", tools=None, thinking=False):  # noqa: ARG002
            return
            yield  # pragma: no cover  异步生成器（空）

    async def _boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError(secret)

    monkeypatch.setattr("app.api.memory.get_provider", lambda *a, **k: _P())
    monkeypatch.setattr("app.api.memory.run_agent_loop", _boom)

    t = client.post("/api/threads", json={"title": "泄漏"}, headers=auth_headers).json()
    r = client.post(
        f"/api/threads/{t['id']}/runs",
        json={"message": "hi"},
        headers={**auth_headers, "Accept": "text/event-stream"},
    )
    assert r.status_code == 200, r.text
    assert secret not in r.text, "SSE 文本回显了内部异常细节"
    assert "internal_error" in r.text


# ---------------------------------------------------------------------------
# 4. 工作区产物读取上限
# ---------------------------------------------------------------------------


def test_read_artifact_truncates_large_file(client, auth_headers):
    """大产物读取按上限截断并置 truncated 标记（此前全量读入并整份返回）。"""
    from app.api.memory import _MAX_ARTIFACT_READ_CHARS

    uid = _uid(client, auth_headers)
    t = client.post("/api/threads", json={"title": "产物"}, headers=auth_headers).json()
    db = SessionLocal()
    try:
        run = models.Run(thread_id=t["id"], user_id=uid, status=models.RunStatus.done)
        db.add(run)
        db.commit()
        db.refresh(run)
        rel = "iter396_big_artifact.txt"
        (Path(settings.data_dir) / rel).write_text("A" * (_MAX_ARTIFACT_READ_CHARS + 5000), encoding="utf-8")
        db.add(models.RunArtifact(thread_id=t["id"], run_id=run.id, name="big.txt", path=rel, kind="draft"))
        db.commit()
        rid = run.id
    finally:
        db.close()

    r = client.get(f"/api/runs/{rid}/files/big.txt", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["truncated"] is True
    assert len(body["content"]) == _MAX_ARTIFACT_READ_CHARS


# ---------------------------------------------------------------------------
# 5. 会话列表批量查询
# ---------------------------------------------------------------------------


def test_list_threads_uses_batched_queries(client, auth_headers):
    """会话列表的查询次数与条数无关（此前逐条 2 次 -> limit=100 即 201 次）。"""
    for i in range(10):
        client.post("/api/threads", json={"title": f"t{i}"}, headers=auth_headers)

    counter = {"n": 0}

    @event.listens_for(engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        counter["n"] += 1

    try:
        r = client.get("/api/threads?limit=10", headers=auth_headers)
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 10
    # count + 页面查询 + 批量 last message + 批量 running run (+ 身份解析)
    assert counter["n"] <= 8, f"会话列表查询次数 {counter['n']} 过多（此前 2N+1）"

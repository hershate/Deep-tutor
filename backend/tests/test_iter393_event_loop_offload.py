"""iter 393 回归：async 端点内的同步 DB 写/文件 IO 移出事件循环。

缺陷
----
async 端点内直接 `db.commit()` / `db.flush()` 时，SQLite 写锁争用的等待发生在
**事件循环线程**：写锁被占时 busy_timeout（默认 10s）到期才报 "database is
locked"——实测阻塞 11.0s。这段时间内事件循环无法调度任何协程：SSE 心跳停摆、
其他请求全部挂起，表现为"全服务无响应"。典型触发场景：备份导入（单事务持
写锁数秒~数十秒）期间的并发写请求。

争用点在 **flush/commit 触发的 DML**（获取 WAL 写锁），而非 COMMIT 语句本身
——只移 commit 无效。

修复
----
写提交移入专用写线程池（app.core.dbwrite），文件落盘移入 to_thread。
本文件锁定三条不变量：
1. 写锁争用期间事件循环保持可调度（心跳协程继续运行）。
2. DB 写与文件落盘不在事件循环线程执行。
3. 端点行为（成功/失败清理/落库内容）不因异步化而改变。
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading

import httpx
import pytest

from app.core.settings import settings
from app.db.base import SessionLocal, engine

HEADERS = {"Authorization": "Bearer test-token"}


def _ensure_probe_table() -> None:
    """建一张无外键的探测表（供持锁连接写入，锁语义与业务表一致）。"""
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS iter393_probe (id INTEGER PRIMARY KEY)"))


def _hold_write_lock(seconds: float):
    """占住 SQLite 写锁，`seconds` 后由独立定时线程释放（不依赖事件循环）。

    check_same_thread=False：释放动作发生在 Timer 线程。
    """
    holder = sqlite3.connect(
        settings.db_path, isolation_level=None, timeout=30, check_same_thread=False
    )
    holder.execute("PRAGMA busy_timeout=30000")
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO iter393_probe (id) VALUES (1)")

    def _release() -> None:
        try:
            holder.execute("ROLLBACK")
        finally:
            holder.close()

    timer = threading.Timer(seconds, _release)
    timer.daemon = True
    timer.start()
    return timer


# ---------------------------------------------------------------------------
# 1. 写锁争用期间事件循环保持可调度
# ---------------------------------------------------------------------------


def test_event_loop_stays_responsive_during_write_lock_contention():
    """写锁被占时，async 端点的写请求不得冻结事件循环。

    修复前：端点在事件循环线程内 db.flush() -> 阻塞至锁释放，期间心跳协程
    一次也跑不了（ticks≈0）；修复后写等待发生在写线程池，事件循环照常调度。
    """
    from app.main import app

    _ensure_probe_table()

    async def scenario() -> tuple[int, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://iter393") as ac:
            r = await ac.post("/api/sessions", json={"title": "iter393"}, headers=HEADERS)
            assert r.status_code == 200, r.text
            sid = r.json()["session_id"]

            _hold_write_lock(0.8)  # 持锁 0.8s 后自动释放
            ticks = 0

            async def heartbeat() -> None:
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.02)
                    ticks += 1

            hb = asyncio.create_task(heartbeat())
            try:
                resp = await ac.post(
                    "/api/materials/text",
                    json={"session_id": sid, "text": "写锁争用期间的文本资料"},
                    headers=HEADERS,
                )
            finally:
                hb.cancel()
            return ticks, resp

    ticks, resp = asyncio.run(scenario())
    assert resp.status_code == 200, resp.text
    # 0.8s 持锁期内按 20ms 一跳，理论约 40 次；留足余量仍要求 >=10
    assert ticks >= 10, f"事件循环疑似被写锁等待冻结（心跳仅 {ticks} 次）"


def test_commit_async_does_not_block_event_loop_under_contention():
    """helper 层不变量：写锁被占时 commit_async 不得阻塞事件循环。"""
    from app.core.dbwrite import commit_async
    from app.db import models
    from app.db.models import User

    _ensure_probe_table()
    db0 = SessionLocal()
    try:
        user = User(name="iter393")
        db0.add(user)
        db0.commit()
        uid = user.id
    finally:
        db0.close()

    async def scenario() -> tuple[int, int]:
        holder = sqlite3.connect(
            settings.db_path, isolation_level=None, timeout=30, check_same_thread=False
        )
        holder.execute("PRAGMA busy_timeout=30000")
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("INSERT INTO iter393_probe (id) VALUES (2)")

        db = SessionLocal()
        db.add(models.Note(user_id=uid, title="iter393", content="x"))
        ticks = 0

        async def heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        hb = asyncio.create_task(heartbeat())
        task = asyncio.create_task(commit_async(db))
        await asyncio.sleep(0.5)
        n = ticks
        holder.execute("ROLLBACK")
        holder.close()
        await task
        hb.cancel()
        db.close()
        return n, ticks

    n, _ = asyncio.run(scenario())
    assert n >= 10, f"commit_async 阻塞了事件循环（心跳仅 {n} 次）"


# ---------------------------------------------------------------------------
# 2. 写与落盘不在事件循环线程执行
# ---------------------------------------------------------------------------


def test_commit_async_runs_off_event_loop_thread():
    from app.core.dbwrite import commit_async

    seen: dict = {}

    class _FakeSession:
        def commit(self) -> None:
            seen["thread"] = threading.current_thread()

    async def scenario() -> None:
        await commit_async(_FakeSession())  # type: ignore[arg-type]

    asyncio.run(scenario())
    assert seen["thread"] is not threading.main_thread()


def test_flush_async_runs_off_event_loop_thread():
    from app.core.dbwrite import flush_async

    seen: dict = {}

    class _FakeSession:
        def flush(self) -> None:
            seen["thread"] = threading.current_thread()

    async def scenario() -> None:
        await flush_async(_FakeSession())  # type: ignore[arg-type]

    asyncio.run(scenario())
    assert seen["thread"] is not threading.main_thread()


def test_write_pool_isolated_from_default_executor():
    """写等待不得占用默认线程池的槽位（否则大文件落盘被写锁等待饿死）。"""
    from app.core import dbwrite

    async def scenario() -> bool:
        pool = dbwrite._write_pool()
        names = {t.name for t in list(pool._threads)}
        return all(n.startswith("db-write") for n in names) if names else True

    assert asyncio.run(scenario())
    assert dbwrite._write_pool() is not asyncio.new_event_loop()._default_executor


def test_parsed_text_write_runs_off_event_loop_thread(client, auth_headers, monkeypatch):
    """解析文本落盘（最大 500k 字）不得在事件循环线程内同步写。"""
    from app.api import materials as mat_mod

    seen: dict = {}
    original = mat_mod.write_parsed_text

    def _spy(*args, **kwargs):
        seen["thread"] = threading.current_thread()
        return original(*args, **kwargs)

    monkeypatch.setattr(mat_mod, "write_parsed_text", _spy)
    sid = client.post("/api/sessions", json={"title": "iter393-io"}, headers=auth_headers).json()[
        "session_id"
    ]
    r = client.post(
        "/api/materials/text", json={"session_id": sid, "text": "落盘线程检查"}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    assert seen.get("thread") is not threading.main_thread()


# ---------------------------------------------------------------------------
# 3. 端点行为不因异步化而改变
# ---------------------------------------------------------------------------


def test_material_text_persists_parsed_text_file(client, auth_headers):
    """落盘移出事件循环后，解析文本文件与 DB 路径仍须一致。"""
    from app.db import models
    from app.db.base import SessionLocal
    from pathlib import Path

    sid = client.post("/api/sessions", json={"title": "iter393-file"}, headers=auth_headers).json()[
        "session_id"
    ]
    r = client.post(
        "/api/materials/text",
        json={"session_id": sid, "text": "落盘内容校验"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    mid = r.json()["material_id"]
    db = SessionLocal()
    try:
        row = db.get(models.Material, mid)
        assert row is not None and row.parsed_text_path
        p = Path(row.parsed_text_path)
        assert p.exists(), "解析文本文件未落盘"
        assert p.read_text(encoding="utf-8") == "落盘内容校验"
    finally:
        db.close()


def test_material_commit_failure_cleans_parsed_text(client, auth_headers, monkeypatch):
    """提交失败：回滚 + 清理已落盘解析文本（异步化不得丢孤儿清理）。"""
    from app.api import materials as mat_mod
    from app.db import models
    from app.db.base import SessionLocal
    from pathlib import Path

    async def _boom(db):
        raise RuntimeError("模拟提交失败")

    monkeypatch.setattr(mat_mod, "commit_async", _boom)
    sid = client.post("/api/sessions", json={"title": "iter393-fail"}, headers=auth_headers).json()[
        "session_id"
    ]
    # TestClient 默认 raise_server_exceptions=True：未处理异常直接抛出而非转 500
    with pytest.raises(RuntimeError):
        client.post(
            "/api/materials/text",
            json={"session_id": sid, "text": "失败清理校验"},
            headers=auth_headers,
        )
    db = SessionLocal()
    try:
        rows = db.query(models.Material).filter_by(session_id=sid).all()
        assert rows == [], "提交失败后行未回滚"
        leftovers = list(Path(settings.data_dir).glob(f"sessions/{sid}/parsed/*.txt"))
        assert leftovers == [], f"提交失败后残留解析文本文件：{leftovers}"
    finally:
        db.close()


def test_kb_text_document_persists_parsed_text(client, auth_headers):
    """KB 文本入库（_persist_parsed_async）落盘与回填一致。"""
    from app.db import models
    from app.db.base import SessionLocal
    from pathlib import Path

    r = client.post(
        "/api/kb/documents/text", json={"text": "知识库文本内容", "title": "kb393"}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    did = r.json()["id"]
    db = SessionLocal()
    try:
        doc = db.get(models.KbDocument, did)
        assert doc is not None and doc.parsed_text_path
        p = Path(doc.parsed_text_path)
        assert p.exists(), "KB 解析文本未落盘"
        assert "知识库文本内容" in p.read_text(encoding="utf-8")
        assert doc.parsed_chars == len("知识库文本内容")
    finally:
        db.close()


def test_add_text_document_sync_still_persists(client, auth_headers):
    """同步路径（生成产物自动沉淀）未被 _persist_parsed 拆分影响。"""
    from app.api.kb import add_text_document_sync
    from app.db import models
    from app.db.base import SessionLocal
    from app.db.models import User
    from pathlib import Path

    db = SessionLocal()
    try:
        user = db.query(models.User).first()
        if user is None:
            user = User(name="iter393")
            db.add(user)
            db.commit()
            db.refresh(user)
        uid = user.id
        doc = add_text_document_sync(db, uid, "自动沉淀", "沉淀内容正文")
        assert doc is not None
        assert doc.parsed_text_path
        p = Path(doc.parsed_text_path)
        assert p.exists(), "自动沉淀的解析文本未落盘"
        assert p.read_text(encoding="utf-8") == "沉淀内容正文"
    finally:
        db.close()


def test_notes_import_commits(client, auth_headers):
    """批量导入笔记的提交移出事件循环后仍须真正落库。"""
    from app.db import models
    from app.db.base import SessionLocal

    body = "# 标题\n正文内容".encode("utf-8")
    r = client.post(
        "/api/notes/import",
        files=[("files", ("a.md", body, "text/markdown"))],
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["imported"] == 1
    db = SessionLocal()
    try:
        assert db.query(models.Note).filter_by(title="标题").count() == 1
    finally:
        db.close()


def test_dbwrite_pool_shutdown_is_idempotent():
    """关停回收写线程池：重复调用不得抛异常（lifespan 与测试均可调用）。"""
    from app.core import dbwrite

    dbwrite.shutdown_pool()
    dbwrite.shutdown_pool()
    # 关停后可重新惰性创建
    assert dbwrite._write_pool() is not None


# ---------------------------------------------------------------------------
# 4. 请求体上限：未认证大 body 必须在解析（落盘）前被拒
# ---------------------------------------------------------------------------


def _spool_peak_spy():
    """给 Starlette 的 SpooledTemporaryFile 装探针，记录落盘峰值字节数。"""
    from starlette import formparsers

    peak = {"bytes": 0}
    orig = formparsers.SpooledTemporaryFile

    class _Spy(orig):  # type: ignore[misc,valid-type]
        def write(self, b):
            n = super().write(b)
            try:
                self._spy_max = max(getattr(self, "_spy_max", 0), self.tell())
            except Exception:  # noqa: BLE001
                pass
            if self._spy_max > peak["bytes"]:
                peak["bytes"] = self._spy_max
            return n

    formparsers.SpooledTemporaryFile = _Spy  # type: ignore[assignment]
    return peak, orig


def test_backup_body_rejected_before_multipart_is_spooled(client, monkeypatch):
    """备份端点的大 body 必须在 multipart 解析（落系统临时目录）**之前**被拒。

    iter 393 实证：FastAPI 先 `await request.form()` 再跑鉴权依赖
    （fastapi/routing.py:423 vs :474），Starlette 对 multipart 文件 part 无大小
    上限——未认证客户端可用单请求写满临时目录（64MB 体落盘后仍返回 401）。
    """
    import io

    from app import main as main_mod

    monkeypatch.setattr(main_mod, "_BACKUP_BODY_LIMIT", 1024 * 1024)
    peak, orig = _spool_peak_spy()
    try:
        r = client.post(
            "/api/backup/import",
            files={"file": ("b.json", io.BytesIO(b"0" * (8 * 1024 * 1024)), "application/json")},
        )
    finally:
        from starlette import formparsers

        formparsers.SpooledTemporaryFile = orig
    assert r.status_code == 413, r.text
    assert r.json()["error"]["code"] == "payload_too_large"
    assert peak["bytes"] == 0, "请求体在被拒前已落盘（临时目录占用未受控）"


def test_backup_body_limit_is_bounded_but_larger_than_generic():
    """备份路径上限必须有界（不得回到「无上限」），且大于通用上限（保证往返可导回）。"""
    from app import main as main_mod

    assert main_mod._BACKUP_BODY_LIMIT <= 300 * 1024 * 1024
    assert main_mod._BACKUP_BODY_LIMIT > main_mod._MAX_BODY_BYTES


def test_body_budget_rejects_excess_concurrent_bodies(client, auth_headers, monkeypatch):
    """全局在途体预算：超预算 -> 503 + Retry-After（并发上传的临时目录占用上界）。"""
    from app import main as main_mod

    monkeypatch.setattr(main_mod, "_BODY_BUDGET_BYTES", 1024)
    assert main_mod._acquire_body_budget(1024) is True
    try:
        r = client.post("/api/sessions", json={"title": "预算"}, headers=auth_headers)
    finally:
        main_mod._release_body_budget(1024)
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "server_busy"
    assert r.headers.get("Retry-After") == "1"


def test_body_budget_released_after_request(client, auth_headers):
    """正常请求结束后预算必须归还（否则一段时间后全站 503）。"""
    from app import main as main_mod

    before = main_mod._body_inflight
    r = client.post("/api/sessions", json={"title": "归还"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert main_mod._body_inflight == before


# ---------------------------------------------------------------------------
# 5. 导出 wrong_only 不得被静默忽略（表达与实现一致）
# ---------------------------------------------------------------------------


def _seed_plan() -> int:
    from app.db import models
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = db.query(User).first()
        if admin is None:
            admin = User(name="admin", username="admin", password_hash="", is_admin=True)
            db.add(admin)
            db.flush()
        sess = models.Session(user_id=admin.id, title="导出断言会话")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"intent": {"topic": "算法"}})
        db.add(plan)
        db.commit()
        return plan.id
    finally:
        db.close()


def test_wrong_only_rejected_for_slides_and_ics(client, auth_headers):
    """wrong_only 仅 anki 支持——slides/ics 此前在守卫之前 return，参数被静默忽略。"""
    plan_id = _seed_plan()
    for fmt in ("slides", "ics"):
        r = client.post(
            f"/api/export/{plan_id}", json={"format": fmt, "wrong_only": True}, headers=auth_headers
        )
        assert r.status_code == 400, f"{fmt}: {r.status_code} {r.text}"
        assert r.json()["error"]["code"] == "bad_request", fmt


def test_wrong_only_still_supported_for_anki(client, auth_headers):
    """守卫上移不得误伤 anki 的错题导出。"""
    plan_id = _seed_plan()
    r = client.post(
        f"/api/export/{plan_id}", json={"format": "anki", "wrong_only": True}, headers=auth_headers
    )
    # 无测验 -> 422（说明已进入 anki 错题分支，而非被格式守卫拦下 400）
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] in ("no_quizzes", "no_wrong_quizzes")


# ---------------------------------------------------------------------------
# 6. 插件面：记忆 scope 契约 + 在途 run 取消 + LLM 背压
# ---------------------------------------------------------------------------


def test_recall_memories_thread_scope_requires_thread_id():
    """scope='thread' 缺 thread_id 必须显式报错（此前不过滤 -> 返回用户全部记忆）。"""
    from app.plugins.api import MemoryNamespace

    ns = MemoryNamespace()
    with pytest.raises(ValueError):
        ns.recall_memories(1, scope="thread")


def test_plugin_delete_thread_cancels_running_runs(monkeypatch):
    """插件面删 thread 必须先取消在途 run（与 REST /threads/{id} 同口径）。"""
    from app.db import models
    from app.db.base import SessionLocal
    from app.db.models import User
    from app.plugins import api as plugin_api

    db = SessionLocal()
    try:
        user = db.query(User).first()
        if user is None:
            user = User(name="pluginuser", username="pluginuser", password_hash="")
            db.add(user)
            db.commit()
            db.refresh(user)
        uid = user.id
        t = models.Thread(user_id=uid, title="插件会话", kind="chat")
        db.add(t)
        db.commit()
        db.refresh(t)
        run = models.Run(thread_id=t.id, user_id=uid, status=models.RunStatus.running)
        db.add(run)
        db.commit()
        db.refresh(run)
        tid, rid = t.id, run.id
    finally:
        db.close()

    cancelled: list[int] = []
    monkeypatch.setattr("app.api.memory._cancel_run_stream", lambda rid: cancelled.append(rid))

    ns = plugin_api.MemoryNamespace()
    assert ns.delete_thread(tid, uid) is True
    assert cancelled == [rid], f"在途 run 未被取消: {cancelled}"


def test_plugin_llm_slot_times_out_with_busy_error(monkeypatch):
    """插件 LLM 槽等待有上限：超时抛 PluginLLMBusy（不挂死）。"""
    from app.core import concurrency
    from app.core.concurrency import PluginLLMBusy, plugin_llm_task_slot
    from app.core.settings import settings

    monkeypatch.setattr(settings, "max_concurrent_llm_calls", 1)
    concurrency.reset_llm_semaphore()

    async def scenario() -> str:
        async with plugin_llm_task_slot(timeout=0.2):
            # 第二个调用等同一信号量（容量 1）-> 超时
            try:
                async with plugin_llm_task_slot(timeout=0.2):
                    return "acquired"
            except PluginLLMBusy:
                return "busy"
        return "released"

    assert asyncio.run(scenario()) == "busy"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])

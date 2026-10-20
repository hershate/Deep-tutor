"""iter 392：并发/稳定性回归（F1/F2/F5/F6/F8/F9/F11 + 写争用 503 + 跨线程事件投递）。

背景（本轮实测确认的缺陷与修复）：
- F1 孤立生成流泄漏：占位在端点内完成、编排任务在响应体首次迭代时创建——客户端
  在端点执行期间断开则任务从未创建，流永久留在 _STREAMS（占全局生成槽位、会话停
  running、续接挂死）。修复：宽限期后回收（端点自愈 + lifespan 周期回收器）。
- F2 事件循环内同步 DB 写：_finalize / _finalize_chat / 孤立流回收的 DB 写在事件
  循环线程内执行，写锁争用可等 busy_timeout（10s）-> 冻结全部请求与 SSE 心跳。
  修复：持久化移 worker 线程，终态事件投递回事件循环线程。
- F5 chat 断连仅软取消：软取消打不断在途 stream_agentic，继续烧 token 并占全局
  LLM 槽。修复：finally 增加硬取消。
- F6 _RUN_STREAMS 二次取消泄漏：二次取消落在收口窗口时 _finalize_chat 函数体可能
  根本没开始执行，其内部 pop 不跑 -> 注册表残留 + run 行停 running（该 thread 永久
  409）。修复：_run_chat 自身 finally 无条件 pop + 强制收敛 run 终态。
- F8 delete_user 逐会话 15s 等待：200 会话最坏 50 分钟独占线程池槽。修复：总预算。
- F9 max_running_chat_per_thread 死配置：硬编码 1，配置零引用。修复：按配置计数。
- F11 _BG_QUIZ_INFLIGHT 泄漏：任务首次执行前被取消则协程 finally 不跑，plan_id 永
  留 -> 测验端点每次白等 180s。修复：done_callback 兜底释放。
- 写锁争用（database is locked）此前落 500（不可重试语义）-> 503 + Retry-After。
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from app.api import errors
from app.api import plans as plans_mod


# ---------------------------------------------------------------------------
# F2：finalize 持久化在 worker 线程（不在事件循环线程）
# ---------------------------------------------------------------------------


def test_finalize_persist_runs_off_event_loop_thread(client, auth_headers, monkeypatch):
    """_finalize_persist 不得在事件循环线程执行（DB 写遇写锁争用会冻结整个 loop）。"""
    from app.db import models
    from app.db.base import SessionLocal

    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    db0 = SessionLocal()
    try:
        plans_mod._set_state(db0, sid, models.OrchestratorStatus.running, "trace-off-loop")
    finally:
        db0.close()

    seen: dict = {}

    def _spy(session_id, stream, task):
        seen["thread"] = threading.current_thread()
        try:
            asyncio.get_running_loop()
            seen["loop_running"] = True
        except RuntimeError:
            seen["loop_running"] = False
        return []

    monkeypatch.setattr(plans_mod, "_finalize_persist", _spy)

    async def scenario():
        stream = plans_mod._SessionStream(sid, "trace-off-loop")
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
        await plans_mod._finalize_async(sid, stream, task)

    asyncio.run(scenario())

    assert seen, "finalize persist 未被调用"
    assert seen["loop_running"] is False, "持久化在事件循环线程内执行（写锁争用会冻结全服务）"
    assert seen["thread"] is not threading.main_thread()


def test_finalize_async_delivers_terminal_event_to_subscriber(client, auth_headers):
    """F2 收口后终态事件仍须送达订阅者（持久化异步化不得丢终态）。"""
    from app.db import models
    from app.db.base import SessionLocal

    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    db0 = SessionLocal()
    try:
        plans_mod._set_state(db0, sid, models.OrchestratorStatus.running, "trace-terminal")
    finally:
        db0.close()

    async def scenario():
        stream = plans_mod._SessionStream(sid, "trace-terminal")
        plans_mod._STREAMS[sid] = stream
        q = stream.subscribe(0)

        async def slow():
            await asyncio.sleep(100)

        task = asyncio.create_task(slow())
        stream.task = task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await plans_mod._finalize_async(sid, stream, task)
        # 终态事件已入历史（订阅队列经 mark_terminal 收尾）
        return stream, q

    stream, q = asyncio.run(scenario())
    assert stream.terminal_id is not None
    assert stream._events, "终态事件未入历史"
    ev, data = stream._events[-1][1], stream._events[-1][2]
    assert ev == "error"
    assert data["code"] == "cancelled"


def test_finalize_persist_failure_falls_back_to_error_state_async(client, auth_headers, monkeypatch):
    """持久化抛错时兜底：落 error 态 + 发终态 error（状态不得停在 running）。"""
    from app.db import models
    from app.db.base import SessionLocal

    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    db0 = SessionLocal()
    try:
        plans_mod._set_state(db0, sid, models.OrchestratorStatus.running, "trace-fail")
    finally:
        db0.close()

    def _boom(*a, **k):
        raise RuntimeError("模拟落库失败")

    monkeypatch.setattr(plans_mod, "_persist_tool_calls", _boom)

    async def scenario():
        stream = plans_mod._SessionStream(sid, "trace-fail")
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
        await plans_mod._finalize_async(sid, stream, task)
        return stream

    stream = asyncio.run(scenario())
    assert stream.terminal_id is not None

    db = SessionLocal()
    try:
        st = db.get(models.OrchestratorState, sid)
        assert st is not None
        assert st.status == models.OrchestratorStatus.error
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 跨线程事件投递（同步端点在线程池线程发终态）
# ---------------------------------------------------------------------------


def test_publish_from_worker_thread_delivered_on_main_loop():
    """非事件循环线程发布的事件须转交主循环投递（订阅队列属该循环）。"""
    from app.api import plans as pm

    async def scenario():
        loop = asyncio.get_running_loop()
        pm.set_main_loop(loop)  # 模拟 lifespan 登记主循环
        try:
            s = pm._SessionStream(1, "t")
            q = s.subscribe(0)

            def worker():
                s.publish("e", {"x": 1})

            th = threading.Thread(target=worker)
            th.start()
            item = await asyncio.wait_for(q.get(), timeout=5)
            th.join()
            assert item == (1, "e", {"x": 1})
        finally:
            pm.set_main_loop(None)

    asyncio.run(scenario())


def test_mark_terminal_keeps_event_copy_before_delivery():
    """跨线程投递窗口内 mark_terminal 仍须留终态副本（relay 兜底投递依赖它）。"""
    s = plans_mod._SessionStream(1, "t")
    eid = s.reserve_terminal("done", {"plan_id": 3})
    s.mark_terminal(eid)
    assert s.terminal_id == eid
    assert s.terminal_event == ("done", {"plan_id": 3})


# ---------------------------------------------------------------------------
# 写锁争用 -> 503 + Retry-After
# ---------------------------------------------------------------------------


def test_db_busy_handler_returns_503_with_retry_after():
    exc = OperationalError("UPDATE t SET v=1", {}, Exception("database is locked"))
    resp = asyncio.run(errors.db_busy_exception_handler(None, exc))
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After") == "1"
    body = resp.body.decode()
    assert "server_busy" in body


def test_db_busy_handler_keeps_500_for_other_operational_errors():
    exc = OperationalError("SELECT 1", {}, Exception("no such table: t"))
    resp = asyncio.run(errors.db_busy_exception_handler(None, exc))
    assert resp.status_code == 500


def test_db_busy_handler_registered_on_app():
    from app.main import app

    assert OperationalError in app.exception_handlers


# ---------------------------------------------------------------------------
# F1：孤立生成流回收
# ---------------------------------------------------------------------------


def test_orphan_stream_is_reaped_after_grace(client, auth_headers):
    """占位后任务从未启动的流：超宽限期后被回收（释放全局槽位 + 会话收敛）。"""
    from app.db import models
    from app.db.base import SessionLocal

    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    db0 = SessionLocal()
    try:
        plans_mod._set_state(db0, sid, models.OrchestratorStatus.running, "trace-orphan")
    finally:
        db0.close()

    stream = plans_mod._SessionStream(sid, "trace-orphan")
    stream.created_monotonic = time.monotonic() - (plans_mod._ORPHAN_STREAM_GRACE + 5)
    plans_mod._STREAMS[sid] = stream

    assert plans_mod._reap_orphan_streams() == 1
    assert sid not in plans_mod._STREAMS, "孤立流未被回收（仍占全局生成槽位）"

    db = SessionLocal()
    try:
        st = db.get(models.OrchestratorState, sid)
        assert st is not None
        assert st.status == models.OrchestratorStatus.interrupted
        assert st.last_error and st.last_error.get("code") == "generation_abandoned"
    finally:
        db.close()


def test_fresh_orphan_stream_not_reaped_within_grace():
    """宽限期内不误杀正在启动的流（端点返回 -> 响应体首次迭代的正常时延）。"""
    stream = plans_mod._SessionStream(999, "t")
    plans_mod._STREAMS[999] = stream
    try:
        assert plans_mod._reap_orphan_streams() == 0
        assert 999 in plans_mod._STREAMS
    finally:
        plans_mod._STREAMS.pop(999, None)


def test_running_stream_not_reaped():
    """任务仍在跑的流不是孤立流（不得被回收）。"""
    async def scenario():
        s = plans_mod._SessionStream(998, "t")
        plans_mod._STREAMS[998] = s
        s.created_monotonic = time.monotonic() - (plans_mod._ORPHAN_STREAM_GRACE + 5)

        async def slow():
            await asyncio.sleep(100)

        task = asyncio.create_task(slow())
        s.task = task
        try:
            assert plans_mod._reap_orphan_streams() == 0
            assert 998 in plans_mod._STREAMS
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            plans_mod._STREAMS.pop(998, None)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# F8：批量等待生成收敛（总预算）
# ---------------------------------------------------------------------------


def test_wait_generations_settled_uses_total_budget():
    """多会话等待共享一份总预算（此前逐会话各 15s -> 200 会话最坏 50 分钟）。"""
    sid = 424242
    stream = plans_mod._SessionStream(sid, "t")
    stream.task = None  # 无 task -> 立即视为收敛
    plans_mod._STREAMS[sid] = stream
    try:
        t0 = time.monotonic()
        assert plans_mod.wait_generations_settled([sid, sid + 1], timeout=5.0) is True
        assert time.monotonic() - t0 < 5.0
    finally:
        plans_mod._STREAMS.pop(sid, None)


def test_wait_generations_settled_times_out_on_unsettled():
    """未收敛的流：超时返回 False（调用方容忍，数据一致性不依赖本等待）。"""

    class _NeverDone:
        """占位任务：done() 恒 False（模拟在途编排任务）。

        真实场景下编排任务在事件循环中推进；此处只需 done() 判据，故用桩避免
        占用事件循环线程（wait_generation_settled 是同步 time.sleep 轮询）。
        """

        def done(self) -> bool:
            return False

    sid = 424243
    stream = plans_mod._SessionStream(sid, "t")
    stream.task = _NeverDone()
    plans_mod._STREAMS[sid] = stream
    try:
        t0 = time.monotonic()
        assert plans_mod.wait_generations_settled([sid], timeout=0.3) is False
        assert time.monotonic() - t0 < 3.0
    finally:
        plans_mod._STREAMS.pop(sid, None)


# ---------------------------------------------------------------------------
# F9：每会话 running chat run 上限按配置
# ---------------------------------------------------------------------------


def test_max_running_chat_per_thread_setting_is_enforced(client, auth_headers, monkeypatch):
    """settings.max_running_chat_per_thread 此前零引用（硬编码 1）。"""
    from app.api import memory as mem
    from app.core.settings import settings
    from app.db import models
    from app.db.base import SessionLocal

    thread_id = client.post("/api/threads", json={"title": "t"}, headers=auth_headers).json()["id"]
    db = SessionLocal()
    try:
        user_id = db.query(models.User).first().id
        db.add(
            models.Run(
                thread_id=thread_id,
                user_id=user_id,
                kind=models.RunKind.chat,
                status=models.RunStatus.running,
                trace_id="t1",
            )
        )
        db.add(
            models.Run(
                thread_id=thread_id,
                user_id=user_id,
                kind=models.RunKind.chat,
                status=models.RunStatus.running,
                trace_id="t2",
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(settings, "max_running_chat_per_thread", 2)
    monkeypatch.setattr(mem, "_MAX_RUNNING_PER_THREAD", 2)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        mem._create_chat_run_db(thread_id, user_id, "m")
    assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# 路径穿越守卫：Windows 扩展长度前缀形态（iter 392 实测误判越界）
# ---------------------------------------------------------------------------


def test_path_guard_accepts_windows_extended_prefix_form(tmp_path):
    """resolve() 偶发返回 \\\\?\\ 前缀形态时，越界判定不得误报（合法路径被拒 -> 500）。"""
    from app.core import storage

    root = tmp_path / "data"
    root.mkdir()
    prefixed = "\\\\?\\" + str(root / "sessions" / "1")
    assert storage.is_within_root(root, Path(prefixed))


def test_path_guard_still_blocks_traversal(tmp_path):
    """前缀归一化不得削弱穿越防护：root 之外的路径仍判越界。"""
    from app.core import storage

    root = tmp_path / "data"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    assert not storage.is_within_root(root, outside)
    assert not storage.is_within_root(root, Path("\\\\?\\" + str(outside)))
    with pytest.raises(ValueError):
        storage.resolve_under_root(root, "..", "outside")


def test_resolve_under_root_accepts_in_tree_path(tmp_path):
    from app.core import storage

    root = tmp_path / "data"
    root.mkdir()
    got = storage.resolve_under_root(root, "sessions", "42")
    assert got.name == "42"
    assert got.parent.name == "sessions"


# ---------------------------------------------------------------------------
# 备份导入：解析文本并发落盘（写锁持有时长）
# ---------------------------------------------------------------------------


def test_import_writes_parsed_text_files_and_paths(client, auth_headers):
    """并发落盘后：文件确实存在、内容正确、DB 的 parsed_text_path 指向该文件。"""
    import json

    from app.core import backup as backup_core
    from app.db import models
    from app.db.base import SessionLocal

    payload = {
        "format": "deep-tutor-backup",
        "version": 2,
        "sessions": [
            {
                "title": "并发落盘会话",
                "created_at": "2026-01-01T00:00:00",
                "materials": [
                    {
                        "kind": "text",
                        "source": f"m{i}",
                        "parsed_text": f"内容-{i}" * 50,
                        "parsed_chars": 250,
                        "meta": {},
                        "created_at": "2026-01-01T00:00:00",
                    }
                    for i in range(8)
                ],
                "plans": [],
                "progress": [],
                "learning_logs": [],
            }
        ],
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    r = client.post(
        "/api/backup/import",
        files={"file": ("b.json", data, "application/json")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["materials"] == 8

    db = SessionLocal()
    try:
        mats = db.query(models.Material).all()
        assert len(mats) == 8
        for m in mats:
            assert m.parsed_text_path, "parsed_text_path 未回填"
            p = Path(m.parsed_text_path)
            assert p.is_file(), f"解析文本文件缺失: {p}"
            assert p.read_text(encoding="utf-8").startswith("内容-")
    finally:
        db.close()
    assert backup_core._count_payload_rows(payload) > 0


def test_bg_quiz_inflight_released_when_task_cancelled_before_start():
    """任务首次执行前被取消时，协程 finally 不跑 -> 须由 done_callback 兜底释放。"""

    async def scenario():
        plans_mod._BG_QUIZ_INFLIGHT.clear()
        plans_mod._schedule_quiz_background(987654, 1, 1)
        assert 987654 in plans_mod._BG_QUIZ_INFLIGHT
        task = next(iter(plans_mod._BG_QUIZ_TASKS))
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)  # 让 done_callback 执行
        assert 987654 not in plans_mod._BG_QUIZ_INFLIGHT, "inflight 标志泄漏（测验端点将白等 180s）"

    asyncio.run(scenario())

"""iter 368 批次九（存储与并发稳定性）回归：

- GET /sessions 最新方案投影：MAX(id) 子查询只载最新版（多版本会话不再全量载入）
- 孤儿目录 mtime 冷却：新落盘目录当轮不删（备份恢复「先文件后提交」窗口保护）
- maintenance：checkpoint_db 可执行；VACUUM 按 freelist 门控跳过
- 材料 SSE 等 LLM 槽排队期间发注释心跳（代理空闲断连防护）
"""

from __future__ import annotations

import asyncio
import os
import time

from fastapi.testclient import TestClient

from app.db import models
from app.db.base import SessionLocal


def _mk_session_with_plans(versions: int) -> int:
    db = SessionLocal()
    try:
        u = models.User(name="u", password_hash="x")
        db.add(u)
        db.flush()
        s = models.Session(user_id=u.id, title="s")
        db.add(s)
        db.flush()
        for v in range(1, versions + 1):
            db.add(
                models.Plan(
                    session_id=s.id,
                    version=v,
                    content={"intent": {"topic": f"topic-v{v}"}},
                )
            )
        db.commit()
        return s.id
    finally:
        db.close()


def test_sessions_latest_plan_projection(client: TestClient):
    sid = _mk_session_with_plans(3)
    r = client.get("/api/sessions", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200, r.text
    items = [s for s in r.json()["items"] if s["session_id"] == sid]
    assert len(items) == 1
    assert items[0]["latest_plan"]["topic"] == "topic-v3"  # 最新版本胜出（MAX(id) 投影）


def test_orphan_dir_mtime_cooldown(tmp_path):
    from app.core import maintenance as m

    root = tmp_path / "sessions"
    # 冷目录：mtime 拨回 25h 前 -> 删
    old = root / "101"
    old.mkdir(parents=True)
    old_mtime = time.time() - 25 * 3600
    os.utime(old, (old_mtime, old_mtime))
    # 热目录：刚落盘（备份恢复窗口）-> 留
    fresh = root / "102"
    fresh.mkdir(parents=True)

    removed = m.cleanup_orphan_session_dirs(str(tmp_path))
    assert removed == 1
    assert not old.exists()
    assert fresh.exists()


def test_checkpoint_and_gated_vacuum(tmp_path):
    from app.core import maintenance as m

    db_file = tmp_path / "t.db"
    import sqlite3

    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    assert m.checkpoint_db(str(db_file)) is True

    # 空闲页占比 0% -> 门控跳过（不执行整库重写）
    assert m.vacuum_db(str(db_file)) is False
    # 强制门控为 0（任何比例都 VACUUM）-> 执行
    m._VACUUM_FREE_RATIO = 0.0
    try:
        assert m.vacuum_db(str(db_file)) is True
    finally:
        m._VACUUM_FREE_RATIO = 0.10


def test_materials_sse_keepalive_while_queued(monkeypatch):
    """槽位占满时，排队中的 SSE 生成器持续发注释心跳。"""
    from app.api import plans as plans_mod
    from app.core.concurrency import reset_llm_semaphore
    from app.core.settings import settings

    monkeypatch.setattr(settings, "max_concurrent_llm_calls", 1)
    monkeypatch.setattr(plans_mod, "_SLOT_KEEPALIVE_S", 0.05)
    reset_llm_semaphore()

    async def fake_inner(*_a, **_k):
        yield "data: done\n\n"

    monkeypatch.setattr(plans_mod, "_materials_sse", fake_inner)

    async def scenario() -> list[str]:
        from app.core.concurrency import llm_task_slot

        out: list[str] = []
        slot = llm_task_slot()
        await slot.__aenter__()  # 占住唯一槽位
        gen = plans_mod._materials_sse_gated(1, 1, {}, None, [], 1, [])
        try:
            out.append(await asyncio.wait_for(gen.__anext__(), timeout=2))  # keepalive 1
            out.append(await asyncio.wait_for(gen.__anext__(), timeout=2))  # keepalive 2
        finally:
            await slot.__aexit__(None, None, None)  # 释放占用 -> 排队方可获取槽
        async for chunk in gen:  # 获取成功 -> 转发内部事件
            out.append(chunk)
        return out

    chunks = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert chunks[0] == ": keepalive\n\n"
    assert chunks[1] == ": keepalive\n\n"
    assert chunks[2] == "data: done\n\n"
    reset_llm_semaphore()

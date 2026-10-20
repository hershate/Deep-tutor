"""iter 396 回归：会话删除端点的线程占用与文件清理日志。

对应本轮 api/sessions.py 的两处修复：

1. **S3 删除端点独占工作线程**——``DELETE /sessions/{id}`` 此前是**同步**端点，等在途
   生成收敛（最多 15s）期间独占一个 anyio 工作线程；并发删除 N 个会话即可占满线程池
   （默认 40 槽），其余同步端点（含登录）排队。改为 async 端点三段式：准备（写线程）
   → 异步等待（不占线程）→ 删除（写线程）+ 文件清理（线程池）。
2. **S2 文件清理异常被静默吞掉**——``except Exception: pass`` 让 Windows 文件占用导致
   的残留目录运维无从发现。改为记 warning（含堆栈）。
"""

from __future__ import annotations

import inspect
import logging

from app.db import models
from app.db.base import SessionLocal


def test_delete_session_endpoint_is_async():
    """端点必须是 async——否则等生成收敛期间独占 anyio 工作线程（最多 15s）。"""
    from app.api import sessions as sessions_mod

    assert inspect.iscoroutinefunction(sessions_mod.delete_session), (
        "DELETE /sessions 应为 async 端点（等待收敛不得占用工作线程）"
    )


def test_delete_session_with_running_generation(client, auth_headers):
    """会话挂着在途生成时删除仍成功（等待收敛后删行）。"""
    from app.api import plans as plans_mod

    sid = client.post("/api/sessions", json={"title": "待删"}, headers=auth_headers).json()["session_id"]
    stream = plans_mod._SessionStream(sid, "trace-x")
    plans_mod._STREAMS[sid] = stream
    try:
        r = client.delete(f"/api/sessions/{sid}", headers=auth_headers)
        assert r.status_code == 200, r.text
    finally:
        plans_mod._STREAMS.pop(sid, None)
    db = SessionLocal()
    try:
        assert db.get(models.Session, sid) is None
    finally:
        db.close()


def test_delete_session_logs_file_cleanup_failure(client, auth_headers, monkeypatch, caplog):
    """文件目录清理失败仍返回 200（DB 已删），但必须留下告警日志。"""
    import app.api.sessions as sessions_mod

    def _boom(session_id: int) -> None:
        raise OSError("文件被占用")

    monkeypatch.setattr(sessions_mod, "delete_session_files", _boom)
    sid = client.post("/api/sessions", json={"title": "清理失败"}, headers=auth_headers).json()["session_id"]

    with caplog.at_level(logging.WARNING, logger="deep_tutor.api.sessions"):
        r = client.delete(f"/api/sessions/{sid}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert any("会话目录清理失败" in rec.getMessage() for rec in caplog.records), [
        rec.getMessage() for rec in caplog.records
    ]
    db = SessionLocal()
    try:
        assert db.get(models.Session, sid) is None
    finally:
        db.close()

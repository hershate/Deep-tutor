"""iter 370（R6 裁决缺省项 F1）：备份 v2 覆盖面闭合回归。

v1 备份不含 threads/messages/runs/memories/kb_documents 及 kb 原件——恢复后这些
数据直接丢失。v2 新增三顶层键（threads 内含 runs/messages，kb 原件 base64 内联），
v1 旧备份导入兼容（无键按空处理）。
"""

from __future__ import annotations

import base64
import uuid

import pytest

from app.core.backup import (
    FORMAT,
    VERSION,
    BackupFormatError,
    build_export,
    preview_backup,
    restore_backup,
    validate_backup,
)
from app.core.storage import save_kb_upload, write_kb_parsed_text
from app.db import models
from app.db.base import SessionLocal

_RAW_PDF = b"%PDF-fake-bytes-for-backup-v2"


def _mk_user(name: str) -> int:
    db = SessionLocal()
    u = models.User(name=name, username=f"{name}-{uuid.uuid4().hex[:8]}", password_hash="x")
    db.add(u)
    db.commit()
    uid = u.id
    db.close()
    return uid


def _seed(user_id: int) -> dict:
    db = SessionLocal()
    th = models.Thread(user_id=user_id, title="对话A", kind="chat", model="m1")
    db.add(th)
    db.flush()
    run_done = models.Run(
        thread_id=th.id,
        user_id=user_id,
        kind=models.RunKind.chat,
        status=models.RunStatus.done,
        step_count=3,
        budget_used=0.25,
        trace_id="tr1",
    )
    run_live = models.Run(  # 非终态：导出须收敛为 interrupted
        thread_id=th.id,
        user_id=user_id,
        kind=models.RunKind.chat,
        status=models.RunStatus.running,
        step_count=1,
    )
    db.add_all([run_done, run_live])
    db.flush()
    db.add_all(
        [
            models.Message(thread_id=th.id, role=models.MessageRole.user, content=[{"type": "text", "text": "你好"}]),
            models.Message(
                thread_id=th.id,
                role=models.MessageRole.assistant,
                content=[{"type": "text", "text": "你好！"}],
                tokens_in=10,
                tokens_out=5,
                model="fake-model",
                run_id=run_done.id,
            ),
        ]
    )
    db.add_all(
        [
            models.Memory(user_id=user_id, scope="user", kind="fact", content="用户偏好简洁"),
            models.Memory(user_id=user_id, thread_id=th.id, scope="thread", kind="note", content="会话要点"),
        ]
    )
    doc = models.KbDocument(
        user_id=user_id,
        title="KB 文档",
        kind=models.MaterialKind.file,
        ext="pdf",
        source="manual.pdf",
        parsed_chars=4,
        meta={"k": 1},
        content_hash="h" * 64,
        status="parsed",
    )
    db.add(doc)
    db.flush()
    doc.parsed_text_path = str(write_kb_parsed_text(user_id, doc.id, "解析正文"))
    doc.original_path = str(save_kb_upload(user_id, doc.id, _RAW_PDF, "pdf"))
    out = {"thread_id": th.id, "run_done_id": run_done.id}
    db.commit()
    db.close()
    return out


def test_backup_v2_roundtrip_threads_memories_kb():
    u1 = _mk_user("b2src")
    _seed(u1)

    db = SessionLocal()
    exp = build_export(db, db.get(models.User, u1))
    db.close()

    assert exp["version"] == VERSION == 2
    t = exp["threads"][0]
    assert t["title"] == "对话A" and t["kind"] == "chat"
    assert len(t["runs"]) == 2
    assert {r["status"] for r in t["runs"]} == {"done", "interrupted"}  # running 收敛
    assert len(t["messages"]) == 2 and t["messages"][1]["run_idx"] == 0
    assert {m["scope"] for m in exp["memories"]} == {"user", "thread"}
    assert next(m for m in exp["memories"] if m["scope"] == "thread")["thread_idx"] == 0
    assert next(m for m in exp["memories"] if m["scope"] == "user")["thread_idx"] is None
    d = exp["kb_documents"][0]
    assert d["title"] == "KB 文档" and d["parsed_text"] == "解析正文"
    assert base64.b64decode(d["original_b64"]) == _RAW_PDF

    # 追加式还原到另一用户：计数 + 关联重映射 + 文件重落盘
    u2 = _mk_user("b2dst")
    db = SessionLocal()
    summary = restore_backup(db, db.get(models.User, u2), exp)
    db.commit()
    th2 = db.query(models.Thread).filter_by(user_id=u2).one()
    runs2 = db.query(models.Run).filter_by(thread_id=th2.id).all()
    msgs2 = db.query(models.Message).filter_by(thread_id=th2.id).order_by(models.Message.id).all()
    mems2 = db.query(models.Memory).filter_by(user_id=u2).all()
    doc2 = db.query(models.KbDocument).filter_by(user_id=u2).one()
    from pathlib import Path

    assert summary["threads"] == 1 and summary["messages"] == 2
    assert summary["runs"] == 2 and summary["memories"] == 2 and summary["kb_documents"] == 1
    assert {r.status.value for r in runs2} == {"done", "interrupted"}
    done2 = next(r for r in runs2 if r.status == models.RunStatus.done)
    assert msgs2[1].run_id == done2.id  # run_idx -> 新 run id
    mem_thread = next(m for m in mems2 if m.scope == "thread")
    assert mem_thread.thread_id == th2.id
    assert next(m for m in mems2 if m.scope == "user").thread_id is None
    assert Path(doc2.parsed_text_path).read_text(encoding="utf-8") == "解析正文"
    assert Path(doc2.original_path).read_bytes() == _RAW_PDF
    db.close()


def test_backup_v1_dict_still_imports():
    """v1 旧备份（无 threads/memories/kb_documents 键）往返兼容：新段计数为 0。"""
    u = _mk_user("b2v1")
    v1 = {"format": FORMAT, "version": 1, "sessions": [], "notes": [], "provider_profiles": [], "prefs": {}}
    validate_backup(v1)  # 不抛
    db = SessionLocal()
    summary = restore_backup(db, db.get(models.User, u), v1)
    db.commit()
    db.close()
    assert summary["threads"] == 0 and summary["messages"] == 0
    assert summary["runs"] == 0 and summary["memories"] == 0 and summary["kb_documents"] == 0
    assert summary["sessions"] == 0


def test_backup_future_version_rejected():
    with pytest.raises(BackupFormatError):
        validate_backup({"format": FORMAT, "version": VERSION + 1, "sessions": []})


def test_backup_v2_preview_counts():
    u = _mk_user("b2prev")
    _seed(u)
    db = SessionLocal()
    exp = build_export(db, db.get(models.User, u))
    db.close()
    p = preview_backup(exp)
    assert p["version"] == 2
    assert p["threads"] == 1 and p["messages"] == 2 and p["runs"] == 2
    assert p["memories"] == 2 and p["kb_documents"] == 1


def test_backup_kb_corrupt_original_restores_text_only():
    """原件 base64 损坏：仅还原解析文本，记 skipped 不硬失败。"""
    u = _mk_user("b2bad")
    data = {
        "format": FORMAT,
        "version": VERSION,
        "sessions": [],
        "kb_documents": [
            {
                "title": "坏原件",
                "kind": "file",
                "ext": "pdf",
                "source": "x",
                "parsed_text": "文本在",
                "parsed_chars": 3,
                "meta": {},
                "status": "parsed",
                "original_b64": "!!!not-base64!!!",
            }
        ],
    }
    db = SessionLocal()
    summary = restore_backup(db, db.get(models.User, u), data)
    db.commit()
    doc = db.query(models.KbDocument).filter_by(user_id=u).one()
    db.close()
    assert summary["kb_documents"] == 1
    assert doc.original_path is None
    from pathlib import Path

    assert Path(doc.parsed_text_path).read_text(encoding="utf-8") == "文本在"
    assert any("原件数据损坏" in s for s in summary["skipped"])

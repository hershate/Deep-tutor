"""iter 397 K8：``data/kb`` 孤儿文件清扫。

删除知识库文档的顺序是「先删行提交，再尽力清文件」（``api/kb.py``
``delete_document``）：进程在两者之间被杀、或 Windows 文件锁让 ``unlink`` 失败
（只记 WARNING），文件就永远留在磁盘上——原件可达 50MB，而 ``data/kb`` 目录此前
**没有任何清扫**（运维只扫 ``data/sessions/`` 与 ``data/runs/``），孤儿文件无上限
地长。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from app.core.maintenance import cleanup_orphan_kb_files
from app.core.settings import settings
from app.db import models
from app.db.base import SessionLocal


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _user(db) -> models.User:
    """取（或建）一个用户行——测试固件每例清空 users 表。"""
    user = db.query(models.User).first()
    if user is None:
        user = models.User(
            name="kbtest",
            username="kbtest",
            password_hash="",
            is_admin=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _backdate(path: Path, hours: int = 25) -> None:
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def test_cleanup_orphan_kb_files(client, auth_headers):
    """行还在的文件不删；行没了的文件删；冷却窗内的新文件不删。"""
    uid = "9"
    base = Path(settings.data_dir) / "kb" / uid
    originals = base / "originals"
    parsed = base / "parsed"

    live_orig = _write(originals / "live.pdf", b"%PDF-1.4 live")
    live_parsed = _write(parsed / "live.txt", b"live")
    orphan_orig = _write(originals / "orphan.pdf", b"%PDF-1.4 orphan")
    orphan_parsed = _write(parsed / "orphan.txt", b"orphan")
    fresh_orig = _write(originals / "fresh.pdf", b"%PDF-1.4 fresh")
    # 孤儿候选回拨到冷却窗外（与运维的 24h 冷却同口径）
    _backdate(orphan_orig)
    _backdate(orphan_parsed)

    db = SessionLocal()
    try:
        user = _user(db)
        doc = models.KbDocument(
            user_id=user.id,
            title="活文档",
            kind=models.MaterialKind.file,
            ext="pdf",
            source="live.pdf",
            original_path=str(live_orig),
            parsed_text_path=str(live_parsed),
            parsed_chars=4,
            content_hash="live-hash",
        )
        db.add(doc)
        db.commit()
    finally:
        db.close()

    removed = cleanup_orphan_kb_files()
    assert removed == 2, f"清理数量 = {removed}，应为 2"
    assert live_orig.exists()
    assert live_parsed.exists()
    assert not orphan_orig.exists()
    assert not orphan_parsed.exists()
    # 冷却窗内（刚上传、行尚未提交）的文件当轮不删
    assert fresh_orig.exists()


def test_cleanup_orphan_kb_files_without_a_kb_dir(tmp_path):
    """没有 kb 目录时是 no-op。"""
    assert cleanup_orphan_kb_files(str(tmp_path)) == 0


def test_referenced_file_survives_a_different_path_form(tmp_path):
    """DB 里记的路径形态与磁盘上的不一致时，仍被引用的文件**不得**被删。

    回归：DB 里的路径来自 ``storage._kb_root``（``Path.resolve()``），运维扫描用的是
    ``settings.data_dir`` 的原始形态——data_dir 若是 junction/符号链接，``resolve()``
    会给出**不同的根**（同一文件的两种字符串形态）。按绝对路径比对会把「仍被引用的
    原件」判成孤儿删掉（不可逆数据丢失）。比对键取尾部结构
    ``<uid>/<originals|parsed>/<文件名>``，与根路径形态无关。
    """
    uid = "77"
    base = tmp_path / "kb" / uid
    originals = base / "originals"
    parsed = base / "parsed"
    live_orig = _write(originals / "keep.pdf", b"%PDF-1.4 keep")
    live_parsed = _write(parsed / "keep.txt", b"keep")
    _backdate(live_orig)
    _backdate(live_parsed)

    # DB 侧的路径：根不同（junction 解析后的形态）+ Windows 扩展长度前缀
    other_root = Path("D:/real-data/kb") / uid
    db_orig = "\\\\?\\" + str(other_root / "originals" / "keep.pdf")
    db_parsed = "\\\\?\\" + str(other_root / "parsed" / "keep.txt")

    db = SessionLocal()
    try:
        user = _user(db)
        doc = models.KbDocument(
            user_id=user.id,
            title="形态不一致的活文档",
            kind=models.MaterialKind.file,
            ext="pdf",
            source="keep.pdf",
            original_path=db_orig,
            parsed_text_path=db_parsed,
            parsed_chars=4,
            content_hash="keep-hash",
        )
        db.add(doc)
        db.commit()
    finally:
        db.close()

    removed = cleanup_orphan_kb_files(str(tmp_path))
    assert removed == 0, f"仍被引用的文件被误删（removed={removed}）"
    assert live_orig.exists()
    assert live_parsed.exists()


def test_run_maintenance_reports_kb_orphans():
    """运维统计里带上清理的孤儿文件数。"""
    from app.core.maintenance import run_maintenance

    stats = run_maintenance()
    assert "orphan_kb_files_removed" in stats

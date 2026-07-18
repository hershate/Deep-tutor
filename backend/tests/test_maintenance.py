"""运维项测试（plan/09 §一/§六，iter 87）：日志轮转 handler、VACUUM、孤儿会话目录清理。"""

from __future__ import annotations

import logging
from pathlib import Path

from app.core.logging import _LOG_BACKUP_COUNT, _LOG_MAX_BYTES, _file_handler
from app.core.maintenance import cleanup_orphan_session_dirs, run_maintenance, vacuum_db


def test_file_handler_rotation(tmp_path):
    """RotatingFileHandler 参数与轮转行为：超 maxBytes -> 产生 .1 备份。"""
    handler = _file_handler(tmp_path / "logs")
    assert handler.maxBytes == _LOG_MAX_BYTES
    assert handler.backupCount == _LOG_BACKUP_COUNT
    assert handler.baseFilename.endswith("deep-tutor.log")
    handler.close()  # Windows 下不关闭会锁住文件，阻止后续 doRollover 重命名

    # 用小 maxBytes 直接构造验证轮转（stdlib 行为 + 我们的编码/路径约定）
    from logging.handlers import RotatingFileHandler

    h = RotatingFileHandler(tmp_path / "logs" / "deep-tutor.log", maxBytes=64, backupCount=2, encoding="utf-8")
    rec_logger = logging.getLogger("test_rotation")
    rec_logger.addHandler(h)
    rec_logger.setLevel(logging.INFO)
    for i in range(10):
        rec_logger.info("line %d %s", i, "x" * 40)
    h.close()
    rec_logger.removeHandler(h)
    assert (tmp_path / "logs" / "deep-tutor.log.1").exists()


def test_vacuum_db_ok_and_missing():
    from app.core.settings import settings

    vacuum_db(settings.db_path)  # 测试库存在 -> 正常完成
    vacuum_db("data/__no_such__.db")  # 不存在 -> no-op 不抛
    vacuum_db(":memory:")  # 内存库 -> no-op


def test_cleanup_orphan_session_dirs(client, auth_headers):
    from app.core.settings import settings

    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    root = Path(settings.data_dir) / "sessions"
    (root / str(sid)).mkdir(parents=True, exist_ok=True)  # 活会话目录
    (root / "999999").mkdir(parents=True)  # 孤儿目录
    (root / "not-a-sid").mkdir(parents=True)  # 非数字目录 -> 不动

    removed = cleanup_orphan_session_dirs()
    assert removed == 1
    assert (root / str(sid)).exists()
    assert not (root / "999999").exists()
    assert (root / "not-a-sid").exists()


def test_run_maintenance_returns_stats():
    stats = run_maintenance()
    assert "orphan_dirs_removed" in stats

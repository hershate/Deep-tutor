"""定期运维（plan/09 §一：周期性 VACUUM + 清孤儿文件）。

- vacuum_db：SQLite VACUUM 回收已删页、整理碎片（写锁，低峰执行）。
- cleanup_orphan_session_dirs：data/sessions/<sid>/ 中 sid 已不在 sessions 表的目录
  （历史手动删库/异常残留的孤儿）-> 删除。
- run_maintenance：二者组合，供后台周期任务与 CLI（python -m app.core.maintenance）共用。
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

log = logging.getLogger("deep_tutor.maintenance")

# 后台周期（秒）：每天一次
MAINTENANCE_INTERVAL_SECONDS = 24 * 3600


def vacuum_db(db_path: str | None = None) -> None:
    """VACUUM 数据库（plan/09 §一 周期性 VACUUM）。失败仅告警不抛出。"""
    if db_path is None:
        from app.core.settings import settings

        db_path = settings.db_path
    if db_path == ":memory:" or not Path(db_path).exists():
        return
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("VACUUM")
        log.info("VACUUM 完成: %s", db_path)
    except sqlite3.Error as e:  # 锁冲突等 -> 下轮再试
        log.warning("VACUUM 失败（下轮重试）: %s", e)


def cleanup_orphan_session_dirs(data_dir: str | None = None) -> int:
    """清理孤儿会话目录（DB 已无对应 sessions 行）。返回删除数量。"""
    from app.core.settings import settings
    from app.db import models
    from app.db.base import SessionLocal

    root = Path(data_dir or settings.data_dir) / "sessions"
    if not root.exists():
        return 0
    db = SessionLocal()
    try:
        alive = {row[0] for row in db.query(models.Session.id).all()}
    finally:
        db.close()
    removed = 0
    for child in root.iterdir():
        if not child.is_dir() or not child.name.isdigit():
            continue
        if int(child.name) not in alive:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
            log.info("清理孤儿会话目录: %s", child)
    return removed


def run_maintenance() -> dict:
    """执行一轮运维（VACUUM + 清孤儿），返回统计。"""
    removed = cleanup_orphan_session_dirs()
    vacuum_db()
    return {"orphan_dirs_removed": removed}


async def maintenance_loop(stop_event) -> None:
    """后台周期任务：每 MAINTENANCE_INTERVAL_SECONDS 执行一轮（asyncio.Event 停止）。"""
    import asyncio

    while not stop_event.is_set():
        try:
            await asyncio.to_thread(run_maintenance)
        except Exception as e:  # noqa: BLE001 - 运维失败不拖垮服务
            log.warning("运维任务失败（下轮重试）: %s", e)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=MAINTENANCE_INTERVAL_SECONDS)
        except TimeoutError:
            pass


def main() -> None:  # pragma: no cover - CLI 入口
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    stats = run_maintenance()
    log.info("运维完成: %s", stats)


if __name__ == "__main__":  # pragma: no cover
    main()

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
    from app.core.settings import settings

    if not settings.is_sqlite:
        log.info("非 SQLite 后端，跳过 VACUUM")  # err.md C10
        return
    if db_path is None:
        db_path = settings.db_path
    if db_path == ":memory:" or not Path(db_path).exists():
        return
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("VACUUM")
        log.info("VACUUM 完成: %s", db_path)
    except sqlite3.Error as e:  # 锁冲突等 -> 下轮再试
        log.warning("VACUUM 失败（下轮重试）: %s", e)
    finally:
        if conn is not None:
            conn.close()  # err.md C12：with 块不保证 connection 关闭，显式 close


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


def cleanup_orphan_run_dirs(data_dir: str | None = None) -> int:
    """清理孤儿 run 工作区目录（iter 345 记忆系统）。

    - ``data/runs/<id>/``：仍 ``running`` 的 run 保留其 scratch；其余（interrupted/error/
      已 commit 的外壳残留）-> 删。已 commit 的 run 在 commit 时已 move 走 scratch，
      此处兜底清崩溃残留。
    - ``data/workspaces/<tid>/<rid>/``：committed 区脱离 run 生命周期，**默认不清**——仅在
      run 行已删（真孤儿）或 mid-commit 崩溃（run 在但 workspace_committed=False 且非 running）
      时清，避免误删已 commit 产物。
    """
    from app.core.settings import settings
    from app.db import models
    from app.db.base import SessionLocal

    root = Path(data_dir or settings.data_dir)
    removed = 0
    db = SessionLocal()
    try:
        running_ids = {row[0] for row in db.query(models.Run.id).filter(models.Run.status == models.RunStatus.running).all()}
        # committed 区有效性：<rid, committed, status> 元组
        valid_committed: dict[str, bool] = {}  # run_id_str -> 应保留
        for r in db.query(models.Run).all():
            # iter 365：awaiting_approval 的 run workspace_committed 恒 False（_finalize_chat
            # 走 pass 不 commit；promote_artifact 仅复制不置位）——须显式保留，否则 24h 维护
            # 误删其 committed 产物，用户 approve 时 file_missing，待审批产物永久丢失。
            valid_committed[str(r.id)] = bool(r.workspace_committed) or r.status in (
                models.RunStatus.running,
                models.RunStatus.awaiting_approval,
            )
    finally:
        db.close()

    # data/runs/<id>/ —— 非 running 一律清
    runs_root = root / "runs"
    if runs_root.exists():
        for child in runs_root.iterdir():
            if not child.is_dir() or not child.name.isdigit():
                continue
            if int(child.name) not in running_ids:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
                log.info("清理孤儿 run scratch 目录: %s", child)

    # data/workspaces/<tid>/<rid>/ —— 仅清无效（run 行删 / mid-commit 半成品）
    ws_root = root / "workspaces"
    if ws_root.exists():
        for tdir in ws_root.iterdir():
            if not tdir.is_dir():
                continue
            for rdir in tdir.iterdir():
                if not rdir.is_dir() or not rdir.name.isdigit():
                    continue
                keep = valid_committed.get(rdir.name, False)
                if not keep:
                    shutil.rmtree(rdir, ignore_errors=True)
                    removed += 1
                    log.info("清理无效/孤儿 committed 工作区: %s", rdir)
    return removed


def run_maintenance() -> dict:
    """执行一轮运维（VACUUM + 清孤儿），返回统计。"""
    removed = cleanup_orphan_session_dirs()
    removed += cleanup_orphan_run_dirs()
    vacuum_db()
    return {"orphan_dirs_removed": removed}


async def maintenance_loop(stop_event) -> None:
    """后台周期任务：每 MAINTENANCE_INTERVAL_SECONDS 执行一轮（asyncio.Event 停止）。

    err.md C13：先等待一个周期再执行——启动即跑 VACUUM 会在服务冷启动
    （迁移/恢复占用写锁）时争抢资源；首轮推迟到 24h 后不影响运维目标。
    """
    import asyncio

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=MAINTENANCE_INTERVAL_SECONDS)
            break  # stop_event 已置位 -> 退出
        except TimeoutError:
            pass
        try:
            await asyncio.to_thread(run_maintenance)
        except Exception as e:  # noqa: BLE001 - 运维失败不拖垮服务
            log.warning("运维任务失败（下轮重试）: %s", e)


def main() -> None:  # pragma: no cover - CLI 入口
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    stats = run_maintenance()
    log.info("运维完成: %s", stats)


if __name__ == "__main__":  # pragma: no cover
    main()

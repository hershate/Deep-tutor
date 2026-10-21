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

# 节奏参数自 settings（iter 372 R14）：默认恒为每日一轮 / 24h 冷却（历史常量语义
# 不变），开放为运维旋钮 + 实进程验证加速驱动（tests/soak_maintenance.py）。
# 冷却窗设计依据（iter 368）：DB 重查兜不住备份恢复/导入的写序窗口（先落文件、
# 后单事务提交行——快照+重查都在提交前看，行必不存在）；mtime 冷却保证新落盘
# 目录当轮必不删。


def _interval_seconds() -> int:
    from app.core.settings import settings

    return max(1, settings.maintenance_interval_seconds)


def _grace_seconds() -> int:
    from app.core.settings import settings

    return max(0, settings.orphan_grace_seconds)


def _dir_recently_touched(path: Path) -> bool:
    """目录（或其内任一直接子项）在冷却窗内被写过 -> 视为活跃，跳过删除。"""
    import time

    cutoff = time.time() - _grace_seconds()
    try:
        if path.stat().st_mtime > cutoff:
            return True
        for child in path.iterdir():
            if child.stat().st_mtime > cutoff:
                return True
    except OSError:
        return True  # stat 失败（并发删除/权限）：宁可不删
    return False


def _rmtree_logged(path: Path) -> bool:
    """rmtree + 失败留痕（iter 368：ignore_errors 静默吞掉真实 IO 故障）。返回是否删成。"""
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        log.warning("目录删除失败（下轮重试）: %s", path)
        return False
    return True


def checkpoint_db(db_path: str | None = None) -> bool:
    """WAL 主动 checkpoint（TRUNCATE，iter 368）：把 -wal 并回主库并截断至 0。

    存在长读事务时 TRUNCATE 会失败（返回 busy）-> False，下轮重试。周期执行防
    「auto-checkpoint 被长读者挡住 -> -wal 无限增长」；比 VACUUM 轻得多（不重写全库）。
    """
    from app.core.settings import settings

    if not settings.is_sqlite:
        return False
    if db_path is None:
        db_path = settings.db_path
    if db_path == ":memory:" or not Path(db_path).exists():
        return False
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(f"PRAGMA busy_timeout={int(settings.db_busy_timeout_ms)}")
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        ok = bool(row) and row[0] == 0  # (busy, log, checkpointed)
        if ok:
            log.info("WAL checkpoint(TRUNCATE) 完成: %s", db_path)
        else:
            log.info("WAL checkpoint 跳过（存在活跃读者，下轮重试）: %s", db_path)
        return ok
    except sqlite3.Error as e:
        log.warning("WAL checkpoint 失败（下轮重试）: %s", e)
        return False
    finally:
        if conn is not None:
            conn.close()


# VACUUM 触发门槛：空闲页占比 ≥10% 才值得整库重写（写锁 + 全库 IO）
_VACUUM_FREE_RATIO = 0.10


def vacuum_db(db_path: str | None = None) -> bool:
    """VACUUM 数据库（plan/09 §一 周期性 VACUUM）。失败仅告警不抛出。

    iter 368：freelist 门控——空闲页占比不足 10% 时跳过（VACUUM 持写锁重写全库，
    大库低收益场景纯负担）；返回是否实际执行。
    """
    from app.core.settings import settings

    if not settings.is_sqlite:
        log.info("非 SQLite 后端，跳过 VACUUM")  # err.md C10
        return False
    if db_path is None:
        db_path = settings.db_path
    if db_path == ":memory:" or not Path(db_path).exists():
        return False
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path)
        # iter 366：设 busy_timeout——否则持续写入下 VACUUM 取不到独占锁即立即
        # "database is locked"，永不执行 -> DB 文件只增不减（删行不回收）。设后等待写入
        # 间隙完成；仍取不到则下轮 24h 重试（best-effort，不阻塞服务）。
        conn.execute(f"PRAGMA busy_timeout={int(settings.db_busy_timeout_ms)}")
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
        total = conn.execute("PRAGMA page_count").fetchone()[0]
        if total and (free / total) < _VACUUM_FREE_RATIO:
            log.info("VACUUM 跳过（空闲页 %s/%s 不足 %.0f%%）", free, total, _VACUUM_FREE_RATIO * 100)
            return False
        conn.execute("VACUUM")
        log.info("VACUUM 完成: %s", db_path)
        return True
    except sqlite3.Error as e:  # 锁冲突等 -> 下轮再试
        log.warning("VACUUM 失败（下轮重试）: %s", e)
        return False
    finally:
        if conn is not None:
            conn.close()  # err.md C12：with 块不保证 connection 关闭，显式 close


def cleanup_orphan_session_dirs(data_dir: str | None = None) -> int:
    """清理孤儿会话目录（DB 已无对应 sessions 行）。返回删除数量。

    iter 367：rmtree 前对候选目录**逐个即时重查** DB——快照查询与删除之间存在
    新建会话窗口（POST /sessions 落盘新目录但快照未含），不重查会误删活跃会话
    的全部文件（解析文本/单元/导出，DB 行完好但内容全失）。
    """
    from app.core.settings import settings
    from app.db import models
    from app.db.base import SessionLocal

    root = Path(data_dir or settings.data_dir) / "sessions"
    if not root.exists():
        return 0
    db = SessionLocal()
    removed = 0
    try:
        alive = {row[0] for row in db.query(models.Session.id).all()}
        for child in root.iterdir():
            if not child.is_dir() or not child.name.isdigit():
                continue
            sid = int(child.name)
            if sid in alive:
                continue
            # 快照后新建的会话（窗口竞态）-> 即时重查兜底
            if db.query(models.Session.id).filter(models.Session.id == sid).first() is not None:
                continue
            # iter 368：mtime 冷却——备份恢复/导入「先落文件后提交行」的窗口内不删
            if _dir_recently_touched(child):
                continue
            if _rmtree_logged(child):
                removed += 1
                log.info("清理孤儿会话目录: %s", child)
    finally:
        db.close()
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
        # iter 367：列投影取代全 ORM 行加载——Run 行随历史线性增长，全量
        # .all() 把 content 等大列一并载入只为取三个标量
        valid_committed: dict[str, bool] = {}  # run_id_str -> 应保留
        rows = db.query(models.Run.id, models.Run.workspace_committed, models.Run.status).all()
        for rid, committed, status in rows:
            # iter 365：awaiting_approval 的 run workspace_committed 恒 False（_finalize_chat
            # 走 pass 不 commit；promote_artifact 仅复制不置位）——须显式保留，否则 24h 维护
            # 误删其 committed 产物，用户 approve 时 file_missing，待审批产物永久丢失。
            valid_committed[str(rid)] = bool(committed) or status in (
                models.RunStatus.running,
                models.RunStatus.awaiting_approval,
            )
    finally:
        db.close()

    # data/runs/<id>/ —— 非 running 一律清（快照后新建的 run 即时重查兜底，同上）
    runs_root = root / "runs"
    if runs_root.exists():
        db = SessionLocal()
        try:
            for child in runs_root.iterdir():
                if not child.is_dir() or not child.name.isdigit():
                    continue
                rid = int(child.name)
                if rid in running_ids:
                    continue
                cur = (
                    db.query(models.Run.status)
                    .filter(models.Run.id == rid)
                    .scalar()
                )
                if cur == models.RunStatus.running:
                    continue  # 快照后新建/状态回升的 run，保留 scratch
                if _dir_recently_touched(child):
                    continue
                if _rmtree_logged(child):
                    removed += 1
                    log.info("清理孤儿 run scratch 目录: %s", child)
        finally:
            db.close()

    # data/workspaces/<tid>/<rid>/ —— 仅清无效（run 行删 / mid-commit 半成品）；
    # 删除前即时重查该 run 的有效性（快照后 approve/commit 置位的窗口兜底）
    ws_root = root / "workspaces"
    if ws_root.exists():
        db = SessionLocal()
        try:
            for tdir in ws_root.iterdir():
                if not tdir.is_dir():
                    continue
                for rdir in tdir.iterdir():
                    if not rdir.is_dir() or not rdir.name.isdigit():
                        continue
                    if valid_committed.get(rdir.name, False):
                        continue
                    cur = (
                        db.query(models.Run.workspace_committed, models.Run.status)
                        .filter(models.Run.id == int(rdir.name))
                        .one_or_none()
                    )
                    if cur is not None and (
                        cur[0] or cur[1] in (models.RunStatus.running, models.RunStatus.awaiting_approval)
                    ):
                        continue  # 快照后已 approve/commit，保留
                    if _dir_recently_touched(rdir):
                        continue
                    if _rmtree_logged(rdir):
                        removed += 1
                        log.info("清理无效/孤儿 committed 工作区: %s", rdir)
        finally:
            db.close()
    return removed


def prune_api_usage() -> int:
    """api_usage 保留策略（iter 370，R6 裁决缺省项 F4）。

    每 LLM 调用一行、无保留策略时长期运行无限增长（日预算聚合扫描/备份体积/
    VACUUM 时长线性劣化）。每日运维轮删超期行（created_at 为 naive UTC，与写入端
    func.now() 同口径）。settings.api_usage_retention_days=0 禁用（保留全部历史）。
    """
    from datetime import datetime, timedelta, timezone

    from app.core.settings import settings

    days = settings.api_usage_retention_days
    if days <= 0:
        return 0
    # 列为 naive UTC（写入端 func.now()/CURRENT_TIMESTAMP）——转 naive 再比较
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)
    from app.db.base import SessionLocal
    from app.db.models import ApiUsage

    db = SessionLocal()
    try:
        removed = (
            db.query(ApiUsage)
            .filter(ApiUsage.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()
        if removed:
            log.info("api_usage 保留裁剪：删除 %d 行（保留 %d 天）", removed, days)
        return removed
    except Exception:  # noqa: BLE001 - 裁剪失败不拖垮运维轮
        db.rollback()
        log.warning("api_usage 保留裁剪失败（下轮重试）", exc_info=True)
        return 0
    finally:
        db.close()


def prune_quiz_attempts(batch: int = 2000) -> int:
    """作答记录保留裁剪（iter 395）：**每题**保留最新 N 条（N=settings.quiz_attempt_keep_per_quiz）。

    quiz_attempts 是全库增速最快的表（每次提交一行，原先永不清理）——长期运行
    无限增长，并连带拖慢错题本/复习页/统计的窗口函数查询（实测 5 万条作答时
    wrong-queue 约 93ms、stats 约 120ms，随历史线性劣化）。

    口径：按 (created_at desc, id desc) 取每题最新 N 条，其余删除。**最新一条始终
    保留**（N>=1）-> 错题本判定（每题最新作答 correct IS False）逐字不变；受影响的
    只有「总作答次数 / 正确率」统计（GET /plans/{id}/quiz/stats）与备份体积。

    分批删除（每批一个事务）——单条大 DELETE 会长时间持 SQLite 写锁，把并发写
    请求全打成 503。0 = 禁用（保留全部历史）。
    """
    from app.core.settings import settings

    keep = int(settings.quiz_attempt_keep_per_quiz)
    if keep <= 0:
        return 0
    from sqlalchemy import text

    from app.db.base import SessionLocal
    from app.db.models import QuizAttempt

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT id FROM ("
                "  SELECT id, ROW_NUMBER() OVER ("
                "    PARTITION BY quiz_id ORDER BY created_at DESC, id DESC"
                "  ) AS rn FROM quiz_attempts"
                ") WHERE rn > :keep"
            ),
            {"keep": keep},
        ).all()
        ids = [int(r[0]) for r in rows]
        if not ids:
            return 0
        removed = 0
        for i in range(0, len(ids), max(1, batch)):
            chunk = ids[i : i + max(1, batch)]
            db.query(QuizAttempt).filter(QuizAttempt.id.in_(chunk)).delete(
                synchronize_session=False
            )
            db.commit()
            removed += len(chunk)
        log.info("作答记录保留裁剪：删除 %d 行（每题保留最新 %d 条）", removed, keep)
        return removed
    except Exception:  # noqa: BLE001 - 裁剪失败不拖垮运维轮
        db.rollback()
        log.warning("作答记录保留裁剪失败（下轮重试）", exc_info=True)
        return 0
    finally:
        db.close()


def expire_stale_approvals() -> int:
    """收敛超时的**待审批提议**（iter 397 A2/A17）。返回收敛数量。

    ``promote_artifact`` 提议后 run 停在 ``awaiting_approval``——此前**没有任何出口**：
    ``cancel_run`` 只受理仍在流注册表内的 run（finalize 已摘除 -> 恒 409
    ``not_running``），也没有过期机制 -> 提议永久占位（run 行不落终态、产物目录
    永不清理、用户也无法放弃）。现：超时（``settings.awaiting_approval_ttl_seconds``，
    默认 24h）未处理的提议收敛为 ``cancelled``——``RunStatus.cancelled`` 此前在运行时
    从不写入，此处是它的首个真实写入点。

    判据取**提议产生时刻**（最新 approval 步骤的 created_at）而非 run 起始时刻：
    长 run 的起始时刻远早于提议产生时刻，用它会把刚提出的提议误判为超时。
    无 approval 步骤（数据异常）时回退 run.started_at。0 = 禁用（永不自动收敛）。
    """
    from datetime import datetime, timedelta, timezone

    from app.core.settings import settings

    ttl = int(settings.awaiting_approval_ttl_seconds)
    if ttl <= 0:
        return 0
    from app.db import models
    from app.db.base import SessionLocal

    # 列为 naive UTC（server_default=func.now()）——转 naive 再比较
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=ttl)).replace(tzinfo=None)
    db = SessionLocal()
    expired = 0
    try:
        candidates = (
            db.query(models.Run)
            .filter(
                models.Run.status == models.RunStatus.awaiting_approval,
                models.Run.started_at < cutoff,
            )
            .all()
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for r in candidates:
            step = (
                db.query(models.RunStep)
                .filter_by(run_id=r.id, type="approval")
                .order_by(models.RunStep.id.desc())
                .first()
            )
            ts = step.created_at if (step is not None and step.created_at is not None) else r.started_at
            if ts is None or ts >= cutoff:
                continue
            r.status = models.RunStatus.cancelled
            r.ended_at = now
            expired += 1
        if expired:
            db.commit()
            log.info("待审批提议超时收敛：%d 个 run -> cancelled", expired)
        return expired
    except Exception:  # noqa: BLE001 - 收敛失败不拖垮运维轮
        db.rollback()
        log.warning("待审批提议超时收敛失败（下轮重试）", exc_info=True)
        return 0
    finally:
        db.close()


def cleanup_orphan_kb_files(data_dir: str | None = None) -> int:
    """清理孤儿知识库文件（DB 已无对应行的原件/解析文本）。返回删除数量。

    删除文档的顺序是「先删行提交，再尽力清文件」（api/kb.py delete_document）：
    进程在两者之间被杀、或 Windows 文件锁让 unlink 失败（只记 WARNING），文件就
    永远留在磁盘上——原件可达 50MB，而 data/kb 目录此前**没有任何清扫**（运维只扫
    data/sessions/ 与 data/runs/），孤儿文件无上限地长。

    判据
    ----
    1. **引用判据**：文件的「尾部结构键」不在任何 kb_documents 行的
       original_path / parsed_text_path 里。用尾部键而非绝对路径比对——DB 里的
       路径来自 ``storage._kb_root``（``Path.resolve()``，可能带 ``\\\\?\\`` 前缀
       或 junction 解析后的形态），而扫描出来的是 ``settings.data_dir`` 下的原始
       形态，两者**字符串形态不一致**（同一文件两种写法）——按绝对路径比对会把
       **仍被引用的文件误判为孤儿删掉**（不可逆数据丢失）。尾部键
       ``<user_id>/<originals|parsed>/<文件名>`` 与根路径形态无关。
    2. **冷却窗**：mtime 在冷却窗内的文件当轮不删——KB 上传是「先落盘后建行」，
       50MB 写入窗口内文件没有行；备份还原同理（先落文件后单事务提交行，
       快照+重查都在提交前看，行必不存在）。
    """
    import time

    from app.core.settings import settings
    from app.core.storage import strip_ext_prefix
    from app.db import models
    from app.db.base import SessionLocal

    root = Path(data_dir or settings.data_dir) / "kb"
    if not root.is_dir():
        return 0

    def _tail_key(p: Path | str) -> str | None:
        """取路径尾部结构键 ``<user_id>/<originals|parsed>/<文件名>``。

        形态与根路径无关（junction / ``\\\\?\\`` 前缀 / 相对 vs 绝对都不影响）。
        不是 kb 目录布局内的路径 -> None（不参与比对）。
        """
        parts = Path(strip_ext_prefix(str(p))).parts
        if len(parts) < 3:
            return None
        sub = parts[-2]
        if sub not in ("originals", "parsed"):
            return None
        return f"{parts[-3]}/{sub}/{parts[-1]}"

    def _alive_keys(db) -> set[str]:
        """当前被 kb_documents 行引用的尾部键集合。"""
        alive: set[str] = set()
        for row in db.query(
            models.KbDocument.original_path, models.KbDocument.parsed_text_path
        ).all():
            for p in row:
                if not p:
                    continue
                key = _tail_key(p)
                if key is not None:
                    alive.add(key)
        return alive

    db = SessionLocal()
    removed = 0
    try:
        alive = _alive_keys(db)
        cutoff = time.time() - _grace_seconds()
        # 第一遍：只收集候选，不删除。
        candidates: list[Path] = []
        for user_dir in root.iterdir():
            if not user_dir.is_dir():
                continue
            for sub in ("originals", "parsed"):
                d = user_dir / sub
                if not d.is_dir():
                    continue
                for f in d.iterdir():
                    if not f.is_file():
                        continue
                    try:
                        if f.stat().st_mtime > cutoff:
                            continue  # 冷却窗内：可能正在上传
                    except OSError:
                        continue  # stat 失败（并发删除/权限）：宁可不删
                    key = _tail_key(f)
                    if key is None or key in alive:
                        continue
                    candidates.append(f)
        if not candidates:
            return 0
        # 第二遍：删除前重查引用集（iter 367 同款——快照与删除之间存在
        # 「文件已落盘、行未提交」的窗口，重查把误删窗口从「整轮扫描」
        # 缩到「一次查询往返」）。
        alive = _alive_keys(db)
        for f in candidates:
            key = _tail_key(f)
            if key is None or key in alive:
                continue
            try:
                f.unlink(missing_ok=True)
            except OSError:
                log.warning("孤儿知识库文件删除失败（下轮重试）: %s", f)
                continue
            removed += 1
            log.info("清理孤儿知识库文件: %s", f)
    finally:
        db.close()
    return removed


def run_maintenance() -> dict:
    """执行一轮运维（审批超时收敛 + checkpoint + 清孤儿 + 用量裁剪 + 作答裁剪 + 门控 VACUUM），返回统计。"""
    # 收敛排在清孤儿之前：超时提议的产物随后可被孤儿清理（状态已非 awaiting_approval）
    expired = expire_stale_approvals()
    removed = cleanup_orphan_session_dirs()
    removed += cleanup_orphan_run_dirs()
    removed_files = cleanup_orphan_kb_files()
    pruned = prune_api_usage()
    pruned_attempts = prune_quiz_attempts()
    checkpoint_db()
    vacuum_db()
    return {
        "orphan_dirs_removed": removed,
        "orphan_kb_files_removed": removed_files,
        "api_usage_pruned": pruned,
        "quiz_attempts_pruned": pruned_attempts,
        "awaiting_approval_expired": expired,
    }


async def maintenance_loop(stop_event) -> None:
    """后台周期任务：每 settings.maintenance_interval_seconds 执行一轮（Event 停止）。

    err.md C13：先等待一个周期再执行——启动即跑 VACUUM 会在服务冷启动
    （迁移/恢复占用写锁）时争抢资源；首轮推迟一个周期不影响运维目标。
    """
    import asyncio

    interval = _interval_seconds()
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
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

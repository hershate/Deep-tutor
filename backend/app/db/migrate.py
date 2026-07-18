"""部署启动迁移：alembic upgrade head 的健壮包装（scripts/start.* 入口 / 手动执行）。

三类库：
1. 全新库（无业务表）——upgrade head 建全部表并写 alembic_version；
2. 已有 alembic_version——upgrade head 增量应用；
3. 无 alembic_version 的旧库（历史 init_db create_all 所建）——直接 upgrade 会因
   表已存在而失败，故先按 schema 特征探测所属代际 stamp 到匹配版本，再 upgrade。

用法：python -m app.db.migrate（cwd 任意；alembic.ini 按包路径定位）
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

log = logging.getLogger(__name__)

_BACKEND_DIR = Path(__file__).resolve().parents[2]

# 代际探测：按迁移链从新到旧，首个命中的特征即该库 stamp 目标。
# (revision, 表名, 列名)；列名为 None 表示表存在即命中。
_REVISION_PROBES: list[tuple[str, str, str | None]] = [
    ("d8f3b1a92c67", "materials", "content_hash"),
    ("c7e2a9f41d35", "share_links", None),
    ("a1c4f7e20b58", "llm_provider_profiles", "user_id"),
    ("169af26b09e7", "users", "username"),
    ("3deb4164602b", "llm_provider_profiles", None),
    ("69bb0fe9ca9f", "study_units", "done_at"),
    ("baa9cf610047", "knowledge_nodes", "sid"),
]
_BASE_REVISION = "beba50db3cc1"


def _progress_has_plan_node_unique(insp) -> bool:
    """progress 表存在覆盖 (plan_id, node_id) 的唯一约束/索引（f2a8c1d94e07 代特征）。

    SQLite 不持久化表级约束名，故按「唯一索引的列集合」判定（autoindex 亦可命中）。
    """
    if "progress" not in insp.get_table_names():
        return False
    target = {"plan_id", "node_id"}
    for uc in insp.get_unique_constraints("progress"):
        if set(uc.get("column_names") or []) == target:
            return True
    for idx in insp.get_indexes("progress"):
        if idx.get("unique") and set(idx.get("column_names") or []) == target:
            return True
    return False


# 附加特征探测（无法以「表/列存在」表达的新代际），按迁移链从新到旧：
# (revision, detector(insp) -> bool)。先于 _REVISION_PROBES 检查（列探针会命中更老代际）。
_EXTRA_PROBES = [
    ("f2a8c1d94e07", _progress_has_plan_node_unique),
]


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    # script_location 相对 cwd 解析，显式钉到包路径，保证任意 cwd 可跑
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def detect_stamp(db_url: str) -> str | None:
    """返回旧库应 stamp 的版本；无需 stamp（新库/已有版本表）返回 None。"""
    insp = inspect(create_engine(db_url))
    tables = set(insp.get_table_names())
    if "alembic_version" in tables or "sessions" not in tables:
        return None  # 版本表在 -> alembic 自理；无业务表 -> 视为新库
    for revision, detector in _EXTRA_PROBES:
        if detector(insp):
            return revision
    for revision, table, column in _REVISION_PROBES:
        if table not in tables:
            continue
        if column is None:
            return revision
        cols = {c["name"] for c in insp.get_columns(table)}
        if column in cols:
            return revision
    return _BASE_REVISION


def _ensure_sqlite_parent(db_url: str) -> None:
    """确保 SQLite 文件父目录存在（全新机器无 data/ 时 sqlite 无法建库文件）。"""
    prefix = "sqlite:///"
    if db_url.startswith(prefix):
        path = db_url[len(prefix) :]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)


def run_migrations(db_url: str | None = None) -> None:
    """对目标库执行 stamp（如需要）+ upgrade head。"""
    if db_url is None:
        from app.core.settings import settings

        db_url = settings.sqlite_url
    _ensure_sqlite_parent(db_url)
    stamp = detect_stamp(db_url)
    cfg = _alembic_config(db_url)
    if stamp:
        log.warning(
            "检测到无 alembic_version 的旧库，按 schema 特征 stamp 到 %s 后增量迁移", stamp
        )
        command.stamp(cfg, stamp)
    command.upgrade(cfg, "head")
    log.info("数据库迁移完成（head）")


def main() -> None:  # pragma: no cover - CLI 入口
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run_migrations()


if __name__ == "__main__":  # pragma: no cover
    main()

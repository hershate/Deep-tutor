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
# 每项 (revision, detector(insp) -> bool)；探测器须与链序严格一致（新在前）。
def _has_table(table: str):
    def _det(insp) -> bool:
        return table in insp.get_table_names()

    return _det


def _has_column(table: str, column: str):
    def _det(insp) -> bool:
        if table not in insp.get_table_names():
            return False
        return column in {c["name"] for c in insp.get_columns(table)}

    return _det


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


_REVISION_PROBES = [
    ("f6a1c3d58e42", _has_table("notes")),
    ("e5f7a3c82d19", _has_column("kb_documents", "status")),
    ("d9e4b2c17f05", _has_table("credential_profiles")),
    ("b7e3f1a92c48", _has_table("kb_chat_records")),
    ("5226d813e363", _has_table("kb_documents")),
    ("c4d91e7b2a05", _has_column("orchestrator_state", "last_error")),
    ("b4e19f0c7a25", _has_table("user_prefs")),
    ("e5b21c8f90a3", _has_table("node_notes")),
    ("a3b91c2e4f56", _has_column("users", "tokens_valid_after")),
    ("f2a8c1d94e07", _progress_has_plan_node_unique),
    ("d8f3b1a92c67", _has_column("materials", "content_hash")),
    ("c7e2a9f41d35", _has_table("share_links")),
    ("a1c4f7e20b58", _has_column("llm_provider_profiles", "user_id")),
    ("169af26b09e7", _has_column("users", "username")),
    ("3deb4164602b", _has_table("llm_provider_profiles")),
    ("69bb0fe9ca9f", _has_column("study_units", "done_at")),
    ("baa9cf610047", _has_column("knowledge_nodes", "sid")),
]
_BASE_REVISION = "beba50db3cc1"


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    # script_location 相对 cwd 解析，显式钉到包路径，保证任意 cwd 可跑
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    # 本函数总是经应用进程（lifespan / python -m app.db.migrate）触达 env.py：
    # 阻止 env.py fileConfig（默认 disable_existing_loggers=True 会禁用 app/uvicorn
    # 既有 logger 并替换 root handlers——服务仍健康但日志全灭，看似启动即死，iter 90
    # 实证）。alembic 自身日志改为传播到 root，由应用/CLI 既有日志配置承接。
    # 直跑 alembic CLI 不经本函数，env.py 保持模板原行为。
    cfg.attributes["configure_logging"] = False
    return cfg


def detect_stamp(db_url: str) -> str | None:
    """返回旧库应 stamp 的版本；无需 stamp（新库/已有版本表）返回 None。"""
    insp = inspect(create_engine(db_url))
    tables = set(insp.get_table_names())
    if "alembic_version" in tables or "sessions" not in tables:
        return None  # 版本表在 -> alembic 自理；无业务表 -> 视为新库
    for revision, detector in _REVISION_PROBES:
        if detector(insp):
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

        db_url = settings.sqlalchemy_url  # err.md C10：DATABASE_URL 优先（此前硬编码 sqlite_url）
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

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


def escape_config_interpolation(value: str) -> str:
    """把裸 `%` 转成 `%%`，使该值能安全地交给 configparser（alembic 的 ini 载体）。

    alembic 的 `Config.set_main_option` 最终落到 `ConfigParser.set`，而
    `configparser.BasicInterpolation` 把 `%` 当插值标记：值里出现**不属于
    `%(name)s` 形态**的裸 `%` 就直接抛 `ValueError: invalid interpolation syntax`。

    谁会被咬：数据库 URL 里的百分号编码。SQLAlchemy 2.1 起 `str(engine.url)`
    对 Windows 路径做百分号编码（`sqlite:///F%3A%5C...`）——把这样一条 URL
    交给 alembic 就会炸在设置选项这一步，迁移根本跑不起来（iter 423 实测：
    `test_create_all_schema_matches_alembic_head` 红，CI 装新依赖即红）。

    转义后 configparser 读回时会把 `%%` 还原成 `%`，交给 SQLAlchemy 的仍是
    原样的 URL（`make_url` 能正确还原百分号编码的路径）。

    反过来说，URL 里的 `%(...)s` 形态并非本仓的用法（数据库 URL 不做
    ini 插值），故整体转义是安全的。
    """
    return value.replace("%", "%%")

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


def _has_index(table: str, index: str):
    def _det(insp) -> bool:
        if table not in insp.get_table_names():
            return False
        return index in {i["name"] for i in insp.get_indexes(table)}

    return _det


def _provider_pricing_removed(insp) -> bool:
    """c5d7e9a13b28 代（iter 356 删每卡计价列）特征：study_units 有 node_sids
    （≥f0a2c5d839b1 基线）且 llm_provider_profiles 已无 price_input_per_mtok 列。

    删列迁移无"新增"特征，故用「较新基线 + 该列已不存在」组合识别——既能把最新
    create_all 库直接 stamp 到对应版本（upgrade 无操作），又不误命中「price 列仍在的
    d8a2f5c91b34 代库」（那种库列仍在 -> 不命中 -> 落到下一探针 stamp d8a2f5c91b34，
    upgrade 再跑 c5d7e9a13b28 删列）。
    """
    if not _has_column("study_units", "node_sids")(insp):
        return False
    return not _has_column("llm_provider_profiles", "price_input_per_mtok")(insp)


def _api_usage_composite_indexed(insp) -> bool:
    """e8c4a1f63b02（iter 369 head，api_usage(user_id, created_at) 复合索引）特征：
    c5d7e9a13b28 代基线 + 该索引已建（当前模型 create_all 的库）。无索引的旧库
    不命中 -> 落到下一探针 stamp c5d7e9a13b28，upgrade 经 if_not_exists 补建。

    此前误判「加索引迁移无需探针」：当前模型 create_all 的新库会被 stamp 到
    c5d7e9a13b28（落后一个版本），虽因迁移幂等而无实害，但版本表失真——后续若
    有非幂等迁移挂在其上，旧库升级路径即被污染。
    """
    if not _provider_pricing_removed(insp):
        return False
    return _has_index("api_usage", "ix_api_usage_user_created")(insp)


_REVISION_PROBES = [
    ("b9d2e4f71a35", _has_index("quizzes", "ix_quizzes_plan_id")),  # iter 371 R9 热查询索引（head）
    ("e8c4a1f63b02", _api_usage_composite_indexed),  # iter 369 api_usage 复合索引
    ("c5d7e9a13b28", _provider_pricing_removed),  # iter 356 删每卡计价列
    # iter 356：price 列仍在 = 旧库已到 d8a2f5c91b34 代；stamp 后 upgrade 跑 c5d7e9a13b28 删列
    ("d8a2f5c91b34", _has_column("llm_provider_profiles", "price_input_per_mtok")),
    ("f0a2c5d839b1", _has_column("study_units", "node_sids")),  # iter 348 材料知识点驱动
    ("e1b7c3d94a08", _has_table("threads")),  # iter 345 记忆系统
    ("c0f2a4b18d33", _has_column("llm_provider_profiles", "kind")),  # iter 313
    ("a7f3e5c91d02", _has_index("sessions", "ix_sessions_user_id")),
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
    cfg.set_main_option("sqlalchemy.url", escape_config_interpolation(db_url))
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

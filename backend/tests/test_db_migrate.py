"""部署启动迁移（app.db.migrate）测试：新库/版本库/无版本旧库三条路径。"""

from __future__ import annotations

from alembic import command
from sqlalchemy import create_engine, inspect, text

from app.db import models  # noqa: F401  触发模型注册
from app.db.base import Base
from app.db.migrate import _alembic_config, detect_stamp, run_migrations

HEAD = "d8f3b1a92c67"


def _url(tmp_path, name: str = "m.db") -> str:
    return f"sqlite:///{tmp_path / name}"


def _tables(url: str) -> set[str]:
    return set(inspect(create_engine(url)).get_table_names())


def _version(url: str) -> str:
    with create_engine(url).connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _drop_version_table(url: str) -> None:
    with create_engine(url).begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))


def test_fresh_db_upgrades_to_head(tmp_path):
    url = _url(tmp_path)
    run_migrations(url)
    tables = _tables(url)
    assert {"sessions", "users", "llm_provider_profiles", "alembic_version"} <= tables
    assert _version(url) == HEAD


def test_fresh_db_in_nonexistent_dir(tmp_path):
    """全新机器无 data/ 目录：迁移自动建父目录（干净检出冒烟暴露的 bug）。"""
    url = f"sqlite:///{tmp_path / 'sub' / 'dir' / 'm.db'}"
    run_migrations(url)
    assert _version(url) == HEAD


def test_run_migrations_idempotent(tmp_path):
    url = _url(tmp_path)
    run_migrations(url)
    run_migrations(url)  # 第二次：版本表在 head，无操作不报错
    assert _version(url) == HEAD


def test_detect_stamp_none_for_fresh_and_versioned(tmp_path):
    assert detect_stamp(_url(tmp_path, "fresh.db")) is None
    url = _url(tmp_path, "v.db")
    run_migrations(url)
    assert detect_stamp(url) is None  # 已有 alembic_version -> alembic 自理


def test_stamp_then_upgrade_for_intermediate_schema(tmp_path):
    """无版本表的中间代旧库：探测 stamp 到 169af26b09e7，再增量到 head。"""
    url = _url(tmp_path)
    command.upgrade(_alembic_config(url), "169af26b09e7")
    _drop_version_table(url)  # 模拟 init_db create_all 时代：有表无版本

    assert detect_stamp(url) == "169af26b09e7"
    run_migrations(url)

    assert _version(url) == HEAD
    cols = {c["name"] for c in inspect(create_engine(url)).get_columns("llm_provider_profiles")}
    assert "user_id" in cols  # 最后一迁移的增量确实补上


def test_stamp_base_for_initial_schema(tmp_path):
    url = _url(tmp_path)
    command.upgrade(_alembic_config(url), "beba50db3cc1")
    _drop_version_table(url)

    assert detect_stamp(url) == "beba50db3cc1"
    run_migrations(url)
    assert _version(url) == HEAD
    assert "llm_provider_profiles" in _tables(url)


def test_stamp_head_for_current_create_all_db(tmp_path):
    """当前模型的 create_all 库（=head schema 但无版本表）：stamp head 后 upgrade 无操作。"""
    url = _url(tmp_path)
    Base.metadata.create_all(bind=create_engine(url))

    assert detect_stamp(url) == HEAD
    run_migrations(url)  # 不抛 “表已存在”
    assert _version(url) == HEAD


def test_stamp_provider_profiles_generation(tmp_path):
    """3deb4164602b 代：provider 表存在但 users 无 username。"""
    url = _url(tmp_path)
    command.upgrade(_alembic_config(url), "3deb4164602b")
    _drop_version_table(url)

    assert detect_stamp(url) == "3deb4164602b"
    run_migrations(url)
    assert _version(url) == HEAD
    user_cols = {c["name"] for c in inspect(create_engine(url)).get_columns("users")}
    assert "username" in user_cols

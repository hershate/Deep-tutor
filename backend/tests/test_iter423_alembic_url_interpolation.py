"""iter 423：alembic 的 ini 载体（configparser）吃不下百分号编码的数据库 URL。

现象：SQLAlchemy 2.1 起 `str(engine.url)` 对 Windows 路径做百分号编码
（`sqlite:///F%3A%5C...`），而 alembic 的 `set_main_option` 最终落到
`ConfigParser.set`——`BasicInterpolation` 把裸 `%` 当插值标记，直接抛
`ValueError: invalid interpolation syntax`，迁移一步都跑不动
（`test_create_all_schema_matches_alembic_head` 当场红；CI 装到新依赖即红）。

修法：`app.db.migrate.escape_config_interpolation` 把 `%` 转成 `%%`，
configparser 读回时还原成 `%`；`alembic/env.py` 的 CLI 直跑路径同样走它。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from app.db.migrate import _alembic_config, escape_config_interpolation

_ENCODED_WIN_URL = "sqlite:///F%3A%5CTEMP%5Cdeeptutor%5Cprobe.db"


def test_encoded_windows_url_is_accepted():
    """百分号编码的 URL 不再把 set_main_option 炸掉。"""
    cfg = _alembic_config(_ENCODED_WIN_URL)
    assert cfg.get_main_option("sqlalchemy.url") == _ENCODED_WIN_URL


def test_escape_helper_round_trips():
    """转义 → 读回：configparser 的 `%%` 还原成 `%`。"""
    assert escape_config_interpolation("a%b") == "a%%b"
    cfg = _alembic_config("postgresql://u:p%40ss@host:5432/db")
    assert cfg.get_main_option("sqlalchemy.url") == "postgresql://u:p%40ss@host:5432/db"


def test_encoded_url_still_points_at_the_same_file():
    """读回的 URL 交给 SQLAlchemy，指向的还是同一个文件（转义没改语义）。"""
    target = Path(r"F:\TEMP\deeptutor\probe_roundtrip.db")
    encoded = str(create_engine(f"sqlite:///{target}").url)
    assert "%3A" in encoded, "前提：SQLAlchemy 2.1 确实做了百分号编码"

    cfg = _alembic_config(encoded)
    back = cfg.get_main_option("sqlalchemy.url")
    assert make_url(back).database == str(target)


def test_upgrade_head_runs_with_an_encoded_url(tmp_path):
    """真跑一次迁移：百分号编码的 URL 必须能建出库来（这正是原故障）。"""
    from alembic import command

    db = tmp_path / "alembic_encoded.db"
    encoded = str(create_engine(f"sqlite:///{db}").url)
    command.upgrade(_alembic_config(encoded), "head")

    from sqlalchemy import inspect

    insp = inspect(create_engine(encoded))
    tables = set(insp.get_table_names())
    assert "alembic_version" in tables
    assert "users" in tables, "迁移没建出业务表"


def test_env_py_escapes_the_url_too():
    """形状守卫：alembic/env.py 的 CLI 直跑路径也得转义（否则直跑 alembic 就红）。"""
    env_src = (Path(__file__).resolve().parents[1] / "alembic" / "env.py").read_text(encoding="utf-8")
    assert "escape_config_interpolation" in env_src

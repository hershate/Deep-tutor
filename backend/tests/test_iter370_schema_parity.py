"""iter 370（R6 遗留⑤闭环）：init_db create_all ↔ alembic head schema 平行度守护。

R6 审计曾把「init_db 对新库走 create_all、不跑 alembic 链」列为漂移风险；第 4 轮
agent 实证当前零漂移。本测试把该对账固化进套件：任何一侧加列/索引/约束而忘了另一
侧（模型改了没写迁移，或迁移写了没改模型）都在 CI 暴露，而非等到某台旧库升级踩坑。
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect

from app.db.base import Base
from app.db.migrate import _alembic_config


def _schema_snapshot(engine_) -> dict:
    insp = inspect(engine_)
    out: dict[str, dict] = {}
    for table in sorted(insp.get_table_names()):
        if table == "alembic_version":
            continue  # 迁移簿记表（仅 alembic 侧存在），非业务 schema
        # 口径：type + nullable + pk/fk/unique/index（结构面）。server_default 的有无
        # 不纳入——两侧 ORM 插入路径都走 Python 侧默认值，运行时等价（实证仅历史迁移
        # 措辞差异，如 users.is_admin 迁移带 '0' 而模型是 Python default）
        cols = {c["name"]: (str(c["type"]), c["nullable"]) for c in insp.get_columns(table)}
        pks = tuple(sorted(insp.get_pk_constraint(table).get("constrained_columns") or []))
        fks = sorted(
            (fk["referred_table"], fk["constrained_columns"][0], fk["referred_columns"][0])
            for fk in insp.get_foreign_keys(table)
        )
        uniques = sorted(
            tuple(sorted(u["column_names"])) for u in insp.get_unique_constraints(table) if u.get("column_names")
        )
        indexes = sorted(
            (i["name"], tuple(sorted(i["column_names"]))) for i in insp.get_indexes(table) if i.get("name")
        )
        out[table] = {"columns": cols, "pk": pks, "fks": fks, "uniques": uniques, "indexes": indexes}
    return out


def test_create_all_schema_matches_alembic_head(tmp_path):
    from alembic import command

    # create_all 侧（init_db 对新库的路径）
    e_create = create_engine(f"sqlite:///{tmp_path / 'create_all.db'}")
    Base.metadata.create_all(bind=e_create)

    # alembic head 侧（旧库升级的终点）
    alembic_path = tmp_path / "alembic.db"
    e_alembic = create_engine(f"sqlite:///{alembic_path}")
    command.upgrade(_alembic_config(str(e_alembic.url)), "head")

    got_create = _schema_snapshot(e_create)
    got_alembic = _schema_snapshot(e_alembic)
    e_create.dispose()
    e_alembic.dispose()

    only_create = set(got_create) - set(got_alembic)
    only_alembic = set(got_alembic) - set(got_create)
    assert not only_create, f"仅 create_all 有的表（模型有/迁移缺）：{sorted(only_create)}"
    assert not only_alembic, f"仅 alembic 有的表（迁移有/模型缺）：{sorted(only_alembic)}"

    drift: list[str] = []
    for table in got_create:
        a, b = got_create[table], got_alembic[table]
        for facet in ("columns", "pk", "fks", "uniques", "indexes"):
            if a[facet] != b[facet]:
                drift.append(f"{table}.{facet}: create_all={a[facet]!r} != alembic={b[facet]!r}")
    assert not drift, "init_db create_all 与 alembic head 漂移：\n" + "\n".join(drift)

"""Alembic 环境脚本。

从 app.core.settings 读取 SQLite URL，从 app.db.base.Base 读取 metadata。
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# 确保把 backend/ 加入 sys.path（alembic.ini 已 prepend_sys_path = .）
from app.core.settings import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db import models  # noqa: E402,F401  触发模型注册

config = context.config
# 已被调用方显式设置（如 app.db.migrate 指定目标库）时尊重之；否则回退 settings（CLI 直跑）
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url)  # err.md C10

# fileConfig 默认 disable_existing_loggers=True：在应用进程内执行迁移时
# （lifespan -> app.db.migrate.run_migrations -> command.upgrade）会禁用
# deep_tutor/uvicorn/app.* 全部既有 logger 并替换 root handlers——进程实际健康
# 但此后所有日志（含 "Application startup complete"）静默消失，表现为「后端
# 启动即死」（iter 90 实证）。app.db.migrate 因此置 attributes["configure_logging"]
# =False 跳过之；直跑 alembic CLI 无该属性 -> 保持模板原行为。
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# err.md C10：render_as_batch 是 SQLite ALTER 兼容手段，其他后端不需要
_BATCH = settings.sqlalchemy_url.startswith("sqlite")


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_BATCH,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_BATCH,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

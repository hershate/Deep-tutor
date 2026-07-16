"""SQLAlchemy 引擎、会话工厂与建表辅助。

SQLite 配置（plan/09-audit.md §五 存储策略）：WAL 模式、synchronous=NORMAL。
大内容入文件系统，DB 存路径 + 元数据。
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.settings import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""


engine = create_engine(
    settings.sqlite_url,
    connect_args={"check_same_thread": False},  # FastAPI 多线程访问 SQLite
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _record) -> None:  # pragma: no cover
    """开启 WAL 与 synchronous=NORMAL，降低锁竞争（plan/09 §五）。"""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """开发期直接建表；生产用 Alembic 迁移（见 backend/alembic）。"""
    from app.db import models  # noqa: F401  触发模型注册

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[SessionLocal]:  # type: ignore[type-arg]
    """FastAPI 依赖：每请求一个 DB 会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

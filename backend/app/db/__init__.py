"""数据层：SQLAlchemy 2.0 声明式基类、引擎、会话工厂与初始化。"""

from app.db.base import Base, SessionLocal, engine, get_db, init_db
from app.db import models  # noqa: F401  确保模型注册到 Base.metadata

__all__ = ["Base", "SessionLocal", "engine", "get_db", "init_db", "models"]

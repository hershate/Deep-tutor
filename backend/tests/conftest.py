"""测试公共配置：设置隔离环境（须在 app 导入前设 env）+ 每测试清 DB/数据目录。"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TEST_DATA = os.path.join(tempfile.gettempdir(), "dt_api_data")
os.environ.setdefault("AUTH_TOKEN", "test-token")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "dt_api_test.db"))
os.environ.setdefault("DATA_DIR", _TEST_DATA)
os.environ.setdefault("SKILLS_DIR", os.path.join(tempfile.gettempdir(), "dt_api_skills"))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolate():
    """每测试前重置 DB 表 + 清空数据目录 + 清空 SSE 流缓冲，保证隔离。"""
    from app.api import plans as plans_mod  # noqa: F401
    from app.db import models  # noqa: F401  触发模型注册
    from app.db.base import Base, engine

    plans_mod._STREAMS.clear()  # 内存事件缓冲跨测试隔离
    from app.skills.loader import reset_skill_registry

    reset_skill_registry()  # Skill 注册表缓存跨测试隔离
    from app.tools.verify_url import reset_verify_cache

    reset_verify_cache()  # verify_url 结果缓存跨测试隔离（P5 缓存优化）
    from app.core.ratelimit import reset_rate_limiter

    reset_rate_limiter()  # 速率限制计数跨测试隔离（plan/09 §十八）
    Base.metadata.drop_all(bind=engine)
    # alembic_version 非 ORM 表，drop_all 不会清：残留旧版本号会让启动迁移对
    # create_all 已建的新表再执行 CREATE -> "table already exists"（iter 171 实证）
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    Base.metadata.create_all(bind=engine)
    p = Path(_TEST_DATA)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture
def client():
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-token"}

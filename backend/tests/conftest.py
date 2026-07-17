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
    """每测试前重置 DB 表 + 清空数据目录，保证隔离。"""
    from app.db import models  # noqa: F401  触发模型注册
    from app.db.base import Base, engine

    Base.metadata.drop_all(bind=engine)
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

"""测试公共配置：设置隔离环境（须在 app 导入前设 env）+ 每测试清 DB/数据目录。"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# 允许从任意 cwd 运行（pytest 只把 tests/ 自身加入 sys.path，app 包需显式定位）。
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.scratchroot import root  # noqa: E402

# 测试产物落在项目的可丢弃根目录下，不写系统卷（与 Go 侧 scratchroot 同规则）。
_TEST_ROOT = os.path.join(root(), "v1")
_TEST_DATA = os.path.join(_TEST_ROOT, "dt_api_data")
_TEST_PLUGINS = os.path.join(_TEST_ROOT, "dt_api_plugins")
os.makedirs(_TEST_ROOT, exist_ok=True)
# 强制赋值而非 setdefault：测试固件硬编码 Bearer test-token（见 auth_headers），
# 外部环境（如 CI 的 AUTH_TOKEN=ci-token）若经 setdefault 渗入会导致全量 401。
os.environ["AUTH_TOKEN"] = "test-token"
os.environ.setdefault("DB_PATH", os.path.join(_TEST_ROOT, "dt_api_test.db"))
os.environ.setdefault("DATA_DIR", _TEST_DATA)
os.environ.setdefault("SKILLS_DIR", os.path.join(_TEST_ROOT, "dt_api_skills"))
os.environ.setdefault("PLUGINS_DIR", _TEST_PLUGINS)

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolate():
    """每测试前重置 DB 表 + 清空数据目录 + 清空 SSE 流缓冲，保证隔离。"""
    from app.api import plans as plans_mod  # noqa: F401
    from app.db import models  # noqa: F401  触发模型注册
    from app.db.base import Base, engine

    plans_mod._STREAMS.clear()  # 内存事件缓冲跨测试隔离
    from app.api import memory as _memory_mod

    _memory_mod._RUN_STREAMS.clear()  # iter 363：chat run SSE 流跨测试隔离
    _memory_mod._CHAT_RUN_TASKS.clear()  # chat run 任务强引用跨测试隔离
    from app.skills.loader import reset_skill_registry

    reset_skill_registry()  # Skill 注册表缓存跨测试隔离
    from app.tools.verify_url import reset_verify_cache

    reset_verify_cache()  # verify_url 结果缓存跨测试隔离（P5 缓存优化）
    from app.core.ratelimit import reset_rate_limiter

    reset_rate_limiter()  # 速率限制计数跨测试隔离（plan/09 §十八）
    from app.plugins.loader import reset_plugins

    reset_plugins()  # 插件台账/工具表/事件订阅跨测试隔离（API.md「后端插件 API」）
    from app.llm.anthropic_provider import reset_client_cache

    reset_client_cache()  # LLM client 缓存跨测试隔离（iter 243：缓存绑定事件循环）
    from app.core.concurrency import reset_llm_semaphore

    reset_llm_semaphore()  # 全局 LLM 信号量跨测试隔离（iter 366：信号量按事件循环缓存）
    from app import main as main_mod

    main_mod._body_inflight = 0  # iter 393：全局在途请求体预算（跨测试归零）
    Base.metadata.drop_all(bind=engine)
    # alembic_version 非 ORM 表，drop_all 不会清：残留旧版本号会让启动迁移对
    # create_all 已建的新表再执行 CREATE -> "table already exists"（iter 176 实证）
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    Base.metadata.create_all(bind=engine)
    p = Path(_TEST_DATA)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True, exist_ok=True)
    pp = Path(_TEST_PLUGINS)
    if pp.exists():
        shutil.rmtree(pp, ignore_errors=True)
    pp.mkdir(parents=True, exist_ok=True)
    yield


@pytest.fixture(autouse=True)
def _no_network_search(monkeypatch):
    """iter 346（F1 回归修复）：测试环境隔离网络。

    search_resources 的 _search_web 默认返回 []（模拟检索成功但无候选）->
    走空候选路径（不触发 _screen_resources 的 LLM 筛选）。否则生成流程测试会因真联网
    （F1 后 bing+无key 回退 bing_web）检索到候选而多调一次 LLM，超出 CannedProvider mock
    响应序列（IndexError）。返回 [] 而非 None：search_unit_resources 直接消费 _search_web
    返回值，None 会致 TypeError。专门测 search 的用例自行 monkeypatch _search_web 覆盖此默认
    （同 monkeypatch 实例，后设生效）。
    """
    from app.tools import search_resources as sr_mod

    async def _noop_search(*args, **kwargs):
        return []

    monkeypatch.setattr(sr_mod, "_search_web", _noop_search)
    yield


@pytest.fixture
def client():
    from app.main import app
    from fastapi.testclient import TestClient

    # iter 394：client 地址设为回环（真实场景：浏览器与后端同机）——开放模式的
    # /setup 端点仅回环可调用（iter 394）。TestClient 默认 "testclient" 非回环，
    # 会让开放模式用例误判为「远程调用」。
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        yield c


@pytest.fixture
def remote_client():
    """非回环客户端（模拟局域网访问）——iter 394：开放模式 setup 端点仅回环可调用。"""
    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app, client=("10.0.0.5", 50000)) as c:
        yield c


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-token"}

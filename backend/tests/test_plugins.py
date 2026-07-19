"""后端插件系统测试（API.md「后端插件 API」）。

覆盖：装载（单文件/包插件/_ 前缀停用/确定性排序）、隔离（导入失败/register 异常/
缺入口/非法 id/重复 id/min_api 不兼容均记 failed 且不阻塞其余插件）、
路由挂载（默认认证/公开可选）、事件总线（订阅/退订/异常隔离/自定义事件）、
工具注册（汇入 default_registry）、状态端点（/api/plugins 管理员）、
lifespan 集成（启动加载 + startup/shutdown 事件）。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

# --- 测试插件源码 ---

PING_PLUGIN = '''
from fastapi import APIRouter

PLUGIN_META = {"id": "ping", "name": "Ping", "version": "0.1.0"}

def register(api):
    router = APIRouter()

    @router.get("/ping")
    def ping():
        return {"pong": True, "plugin": api.id, "api_version": api.version}

    api.routes.mount(router)
'''

PUBLIC_PLUGIN = '''
from fastapi import APIRouter

PLUGIN_META = {"id": "webhook"}

def register(api):
    router = APIRouter()

    @router.post("/hook")
    def hook():
        return {"ok": True}

    api.routes.mount(router, auth=False)
'''

EVENT_PLUGIN = '''
import json

PLUGIN_META = {"id": "ev"}

def register(api):
    def record(payload):
        (api.data_dir / "got.json").write_text(json.dumps(payload), encoding="utf-8")

    def boom(payload):
        raise RuntimeError("监听器故意炸掉")

    api.events.on("ev.custom", boom)      # 先订阅会炸的：验证隔离不影响后订阅者
    api.events.on("ev.custom", record)
'''

TOOL_PLUGIN = '''
PLUGIN_META = {"id": "toolsy"}

def register(api):
    async def echo(inp, ctx):
        return {"echo": inp.args}

    api.tools.register_python("toolsy.echo", echo)
'''

BOOT_PLUGIN = '''
PLUGIN_META = {"id": "boot"}

def register(api):
    api.events.on("startup", lambda payload: (api.data_dir / "started.txt").write_text("1", encoding="utf-8"))
    api.events.on("shutdown", lambda payload: (api.data_dir / "stopped.txt").write_text("1", encoding="utf-8"))
'''


# --- 辅助 ---


def _write(directory: Path, name: str, source: str) -> Path:
    p = directory / name
    p.write_text(source, encoding="utf-8")
    return p


def _fresh_app():
    from app.main import create_app

    return create_app()


def _load(app, directory: Path):
    from app.plugins.loader import load_plugins

    return load_plugins(app, directory)


def _status_map():
    from app.plugins.loader import get_plugin_statuses

    return {s.id: s for s in get_plugin_statuses()}


# --- 装载与隔离 ---


def test_load_single_file_plugin(tmp_path):
    statuses = _load(_fresh_app(), tmp_path)
    assert statuses == []  # 空目录 -> 空台账
    _write(tmp_path, "ping.py", PING_PLUGIN)
    statuses = _load(_fresh_app(), tmp_path)
    assert len(statuses) == 1
    s = statuses[0]
    assert s.id == "ping" and s.state == "loaded" and s.version == "0.1.0"
    assert s.routes == ["/api/plugins/ping"]


def test_load_package_plugin_with_relative_import(tmp_path):
    pkg = tmp_path / "pkgp"
    pkg.mkdir()
    _write(pkg, "helper.py", "VALUE = 42\n")
    _write(
        pkg,
        "plugin.py",
        'from . import helper\n\nPLUGIN_META = {"id": "pkgp"}\n\n'
        "def register(api):\n"
        '    assert helper.VALUE == 42\n',
    )
    statuses = _load(_fresh_app(), tmp_path)
    assert len(statuses) == 1
    assert statuses[0].id == "pkgp" and statuses[0].state == "loaded"


def test_underscore_prefixed_skipped(tmp_path):
    _write(tmp_path, "_disabled.py", PING_PLUGIN)
    pkg = tmp_path / "_off"
    pkg.mkdir()
    _write(pkg, "plugin.py", PING_PLUGIN)
    assert _load(_fresh_app(), tmp_path) == []


def test_broken_import_isolated(tmp_path):
    _write(tmp_path, "aaa_broken.py", "def nope(:\n")  # 语法错误，排序在前
    _write(tmp_path, "ping.py", PING_PLUGIN)
    statuses = _status_map_after(_fresh_app(), tmp_path)
    assert statuses["aaa_broken"].state == "failed"
    assert statuses["aaa_broken"].error.startswith("import:")
    assert statuses["ping"].state == "loaded"  # 坏插件不阻塞好插件


def _status_map_after(app, directory: Path):
    from app.plugins.loader import load_plugins

    load_plugins(app, directory)
    return _status_map()


def test_register_raises_isolated(tmp_path):
    _write(tmp_path, "bad.py", 'PLUGIN_META = {"id": "bad"}\n\ndef register(api):\n    raise RuntimeError("boom")\n')
    _write(tmp_path, "ping.py", PING_PLUGIN)
    statuses = _status_map_after(_fresh_app(), tmp_path)
    assert statuses["bad"].state == "failed"
    assert statuses["bad"].error.startswith("register: boom")
    assert statuses["ping"].state == "loaded"


def test_missing_register_failed(tmp_path):
    _write(tmp_path, "noreg.py", 'PLUGIN_META = {"id": "noreg"}\n')
    statuses = _status_map_after(_fresh_app(), tmp_path)
    assert statuses["noreg"].state == "failed"
    assert "register" in statuses["noreg"].error


def test_invalid_id_failed(tmp_path):
    _write(tmp_path, "upper.py", 'PLUGIN_META = {"id": "UpperCase"}\n\ndef register(api):\n    pass\n')
    statuses = _status_map_after(_fresh_app(), tmp_path)
    assert statuses["UpperCase"].state == "failed"
    assert "非法" in statuses["UpperCase"].error


def test_duplicate_id_failed(tmp_path):
    _write(tmp_path, "a.py", 'PLUGIN_META = {"id": "dup"}\n\ndef register(api):\n    pass\n')
    _write(tmp_path, "b.py", 'PLUGIN_META = {"id": "dup"}\n\ndef register(api):\n    pass\n')
    statuses = _load(_fresh_app(), tmp_path)  # 重复 id 在台账中是两条记录，不能用 map
    dups = [s for s in statuses if s.id == "dup"]
    assert len(dups) == 2
    assert sorted(s.state for s in dups) == ["failed", "loaded"]  # 先加载者成功，后者记重复失败
    assert "重复" in next(s for s in dups if s.state == "failed").error


def test_min_api_compatibility(tmp_path):
    _write(tmp_path, "toonew.py", 'PLUGIN_META = {"id": "toonew", "min_api": "99.0"}\n\ndef register(api):\n    pass\n')
    _write(tmp_path, "okver.py", 'PLUGIN_META = {"id": "okver", "min_api": "1.0"}\n\ndef register(api):\n    pass\n')
    statuses = _status_map_after(_fresh_app(), tmp_path)
    assert statuses["toonew"].state == "failed"
    assert "不兼容" in statuses["toonew"].error
    assert statuses["okver"].state == "loaded"


# --- 路由挂载 ---


def test_route_mount_requires_auth_by_default(tmp_path, auth_headers):
    _write(tmp_path, "ping.py", PING_PLUGIN)
    app = _fresh_app()
    _load(app, tmp_path)
    client = TestClient(app)
    r = client.get("/api/plugins/ping/ping", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"pong": True, "plugin": "ping", "api_version": "1.0.0"}
    # 默认认证：无凭证 -> 401（与宿主端点语义一致）
    assert client.get("/api/plugins/ping/ping").status_code == 401


def test_route_public_opt_out(tmp_path):
    _write(tmp_path, "webhook.py", PUBLIC_PLUGIN)
    app = _fresh_app()
    _load(app, tmp_path)
    client = TestClient(app)
    r = client.post("/api/plugins/webhook/hook")
    assert r.status_code == 200 and r.json() == {"ok": True}


# --- 事件总线 ---


def test_events_subscribe_emit_and_isolation(tmp_path):
    _write(tmp_path, "ev.py", EVENT_PLUGIN)
    app = _fresh_app()
    statuses = _load(app, tmp_path)
    assert statuses[0].state == "loaded"
    assert statuses[0].events == ["ev.custom", "ev.custom"]
    from app.plugins import bus

    bus.emit("ev.custom", {"x": 1})
    from app.core.settings import settings

    data_file = Path(settings.data_dir) / "plugins" / "ev" / "got.json"
    assert data_file.exists()  # 先订阅的炸监听器被隔离，后订阅者仍收到
    assert json.loads(data_file.read_text(encoding="utf-8")) == {"x": 1}


def test_events_unsubscribe():
    from app.plugins import bus

    calls = []
    off = bus.on("t.x", lambda p: calls.append(p))
    bus.emit("t.x", {"a": 1})
    off()
    bus.emit("t.x", {"a": 2})
    assert calls == [{"a": 1}]


def test_events_unknown_event_subscription_is_noop():
    from app.plugins import bus

    calls = []
    bus.on("future.event", lambda p: calls.append(p))  # 订阅不存在的事件不报错
    bus.emit("other.event", {})
    assert calls == []


# --- 工具注册 ---


def test_plugin_tools_join_default_registry(tmp_path):
    _write(tmp_path, "toolsy.py", TOOL_PLUGIN)
    _load(_fresh_app(), tmp_path)
    from app.tools import default_registry

    reg = default_registry()
    assert reg.has("toolsy.echo")
    tool = reg.get("toolsy.echo")
    assert tool.llm is False and tool.func is not None


# --- 状态端点与 lifespan 集成 ---


def test_lifespan_loads_plugins_and_fires_lifecycle_events(auth_headers):
    from app.core.security import hash_password
    from app.core.settings import settings
    from app.db.base import SessionLocal
    from app.db.models import User

    # require_admin 需要管理员：与 test_api_users 同一模式，直接落库一个管理员
    # （env token 认证解析为首个管理员）
    db = SessionLocal()
    try:
        db.add(User(name="root", username="root", password_hash=hash_password("pw1234"), is_admin=True))
        db.commit()
    finally:
        db.close()

    pdir = Path(settings.plugins_dir)
    _write(pdir, "boot.py", BOOT_PLUGIN)
    data_dir = Path(settings.data_dir) / "plugins" / "boot"

    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/plugins", headers=auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["api_version"] == "1.0.0"
        rows = {p["id"]: p for p in body["plugins"]}
        assert rows["boot"]["state"] == "loaded"
        assert "startup" in body["subscribed_events"]
        assert (data_dir / "started.txt").exists()  # startup 事件已在启动时广播
    assert (data_dir / "stopped.txt").exists()  # 退出 lifespan 广播 shutdown


def test_status_endpoint_requires_admin(client):
    # 无任何凭证 -> 401（require_admin 先经 get_current_user 认证）
    assert client.get("/api/plugins").status_code == 401

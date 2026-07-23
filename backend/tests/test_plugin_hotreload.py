"""后端插件热管理测试（API.md「后端插件 API」§热管理）。

覆盖 loader 层 unload/reload/disable/enable 全生命周期 + 端点
POST /api/plugins/{id}/{reload,disable,enable}（管理员）+ 禁用状态跨重启持久化。
公开路由（auth=False）免去认证复杂度，直接以 HTTP 状态码断言路由挂载/卸载。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

# 公开路由插件（auth=False）——测试免认证直接命中，断言挂载/卸载
PUBLIC_PING = '''
from fastapi import APIRouter

PLUGIN_META = {"id": "pp", "name": "PP", "version": "0.1.0"}

def register(api):
    router = APIRouter()

    @router.get("/ping")
    def ping():
        return {"pong": True}

    api.routes.mount(router, auth=False)
'''


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


# --- unload ---


def test_unload_removes_route_and_status(tmp_path):
    _write(tmp_path, "pp.py", PUBLIC_PING)
    app = _fresh_app()
    _load(app, tmp_path)
    client = TestClient(app)
    assert client.get("/api/plugins/pp/ping").status_code == 200

    from app.plugins import loader

    assert loader.unload_plugin("pp") is True
    assert client.get("/api/plugins/pp/ping").status_code == 404  # 路由已按身份摘除
    assert "pp" not in _status_map()  # 状态台账移除
    assert "pp" not in loader._plugin_apis  # noqa: SLF001  API 实例释放
    # 卸载不存在的插件 -> False（幂等无副作用）
    assert loader.unload_plugin("nope") is False


# --- reload ---


def test_reload_reregisters(tmp_path):
    _write(tmp_path, "pp.py", PUBLIC_PING)
    app = _fresh_app()
    _load(app, tmp_path)
    client = TestClient(app)

    from app.plugins import loader

    st = loader.reload_plugin(app, "pp")
    assert st is not None and st.state == "loaded"
    assert client.get("/api/plugins/pp/ping").status_code == 200  # 重新挂载
    assert "pp" in loader._plugin_apis  # noqa: SLF001
    # 重载不存在的插件 -> None
    assert loader.reload_plugin(app, "nope") is None


def test_reload_picks_up_file_changes(tmp_path):
    """reload 重新导入文件——改了源码后 reload 生效（热重启核心价值）。"""
    _write(tmp_path, "pp.py", PUBLIC_PING)
    app = _fresh_app()
    _load(app, tmp_path)
    client = TestClient(app)
    assert client.get("/api/plugins/pp/ping").json() == {"pong": True}

    # 改源码：换一个端点
    _write(tmp_path, "pp.py", '''
from fastapi import APIRouter

PLUGIN_META = {"id": "pp", "name": "PP"}

def register(api):
    router = APIRouter()

    @router.get("/v2")
    def v2():
        return {"reloaded": True}

    api.routes.mount(router, auth=False)
''')
    from app.plugins import loader

    st = loader.reload_plugin(app, "pp")
    assert st is not None and st.state == "loaded"
    assert client.get("/api/plugins/pp/ping").status_code == 404  # 旧端点已摘除
    assert client.get("/api/plugins/pp/v2").json() == {"reloaded": True}  # 新端点生效


# --- disable ---


def test_disable_unloads_and_persists(tmp_path):
    _write(tmp_path, "pp.py", PUBLIC_PING)
    app = _fresh_app()
    _load(app, tmp_path)
    client = TestClient(app)
    assert client.get("/api/plugins/pp/ping").status_code == 200

    from app.plugins import loader

    assert loader.disable_plugin("pp") is True
    assert client.get("/api/plugins/pp/ping").status_code == 404  # 已卸载
    assert _status_map()["pp"].state == "disabled"  # 台账记 disabled（可观测、可启用）
    assert "pp" in loader._disabled_ids  # noqa: SLF001
    # 持久化到磁盘（重启后仍禁用）
    from app.core.settings import settings

    state_file = Path(settings.data_dir) / "plugin_state.json"
    assert state_file.is_file()
    assert "pp" in json.loads(state_file.read_text(encoding="utf-8"))["disabled"]


def test_disable_unknown_returns_false(tmp_path):
    """对不存在的插件 disable -> False（端点据此 404，与 reload/enable 一致）。"""
    from app.plugins import loader

    assert loader.disable_plugin("ghost") is False
    assert "ghost" not in _status_map()
    assert "ghost" not in loader._disabled_ids  # noqa: SLF001


def test_disable_skipped_plugin_is_idempotent():
    """启动时跳过的禁用插件（文件存在于 plugins_dir）再次 disable -> 幂等记 disabled。"""
    from app.core.settings import settings
    from app.plugins import loader

    pdir = Path(settings.plugins_dir)
    pdir.mkdir(parents=True, exist_ok=True)
    _write(pdir, "pp.py", PUBLIC_PING)
    # 模拟启动跳过：手动置入禁用集 + 不加载（文件在 plugins_dir 可被 _find_plugin_file 定位）
    loader._disabled_ids.add("pp")  # noqa: SLF001
    assert loader.disable_plugin("pp") is True
    assert _status_map()["pp"].state == "disabled"


# --- enable ---


def test_enable_reloads_after_disable(tmp_path):
    _write(tmp_path, "pp.py", PUBLIC_PING)
    app = _fresh_app()
    _load(app, tmp_path)
    client = TestClient(app)

    from app.plugins import loader

    loader.disable_plugin("pp")
    assert client.get("/api/plugins/pp/ping").status_code == 404

    st = loader.enable_plugin(app, "pp")
    assert st is not None and st.state == "loaded"
    assert client.get("/api/plugins/pp/ping").status_code == 200  # 重新挂载
    assert "pp" not in loader._disabled_ids  # noqa: SLF001


def test_enable_unknown_returns_none(tmp_path):
    from app.plugins import loader

    app = _fresh_app()
    # 文件不存在 -> None（不禁用集合已清，但无文件可加载）
    loader.disable_plugin("nope")
    assert loader.enable_plugin(app, "nope") is None


# --- 禁用状态跨重启持久化 ---


def test_disabled_skipped_on_restart(tmp_path):
    """禁用状态持久化：模拟重启（清内存状态，保留磁盘禁用文件）后 load_plugins 跳过该插件。"""
    _write(tmp_path, "pp.py", PUBLIC_PING)
    app = _fresh_app()
    _load(app, tmp_path)

    from app.plugins import loader

    loader.disable_plugin("pp")

    # 模拟重启：reset_plugins 清空内存 _disabled_ids/statuses/_plugin_apis（但不动磁盘文件）
    loader.reset_plugins()
    assert "pp" not in loader._disabled_ids  # noqa: SLF001  内存已清

    app2 = _fresh_app()
    _load(app2, tmp_path)  # load_plugins 从磁盘重新读禁用状态
    assert _status_map()["pp"].state == "disabled"  # 启动时跳过加载，记 disabled
    client = TestClient(app2)
    assert client.get("/api/plugins/pp/ping").status_code == 404  # 未挂载

    # 启用后恢复
    st = loader.enable_plugin(app2, "pp")
    assert st is not None and st.state == "loaded"
    assert TestClient(app2).get("/api/plugins/pp/ping").status_code == 200


# --- 端点（管理员）端到端 ---


def test_hot_management_endpoints(client, auth_headers, tmp_path):
    """POST /api/plugins/{id}/reload|disable|enable（管理员）端到端。"""
    from app.core.security import hash_password
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="root", username="root", password_hash=hash_password("pw1234"), is_admin=True))
        db.commit()
    finally:
        db.close()

    _write(tmp_path, "pp.py", PUBLIC_PING)
    from app.main import app
    from app.plugins.loader import load_plugins

    load_plugins(app, tmp_path)  # client fixture 的 lifespan 启动时目录空；这里手动加载

    def states():
        r = client.get("/api/plugins", headers=auth_headers)
        assert r.status_code == 200, r.text
        return {p["id"]: p["state"] for p in r.json()["plugins"]}

    assert states().get("pp") == "loaded"
    assert client.get("/api/plugins/pp/ping").status_code == 200

    # disable
    r = client.post("/api/plugins/pp/disable", headers=auth_headers)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert states()["pp"] == "disabled"
    assert client.get("/api/plugins/pp/ping").status_code == 404

    # enable
    r = client.post("/api/plugins/pp/enable", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["plugin"]["state"] == "loaded"
    assert states()["pp"] == "loaded"
    assert client.get("/api/plugins/pp/ping").status_code == 200

    # reload
    r = client.post("/api/plugins/pp/reload", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["plugin"]["state"] == "loaded"
    assert client.get("/api/plugins/pp/ping").status_code == 200

    # 不存在的插件 -> 404
    assert client.post("/api/plugins/nope/reload", headers=auth_headers).status_code == 404
    assert client.post("/api/plugins/nope/disable", headers=auth_headers).status_code == 404
    assert client.post("/api/plugins/nope/enable", headers=auth_headers).status_code == 404


def test_hot_endpoints_require_admin(client, auth_headers, tmp_path):
    """非管理员访问热管理端点 -> 403（require_admin）。"""
    from app.db.base import SessionLocal
    from app.db.models import User

    # 落库一个普通用户；auth_headers 的 test-token 解析为首个用户（非管理员）
    db = SessionLocal()
    try:
        db.add(User(name="u", username="u", password_hash="", is_admin=False))
        db.commit()
    finally:
        db.close()

    _write(tmp_path, "pp.py", PUBLIC_PING)
    from app.main import app
    from app.plugins.loader import load_plugins

    load_plugins(app, tmp_path)
    assert client.post("/api/plugins/pp/reload", headers=auth_headers).status_code == 403
    assert client.post("/api/plugins/pp/disable", headers=auth_headers).status_code == 403

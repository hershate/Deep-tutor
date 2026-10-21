"""iter 390 R33：授权矩阵增量复验——R18 之后新增/变更端点的行为级角色×端点证据。

静态矩阵（tests/auth_matrix.py 复跑，158 路由）确认 R23-R32 改动端点的授权级别未漂移：
插件热管理三端点（R27 async 化）仍 ADMIN、pricing/notes PUT 仍 USER 且按 user.id 隔离、
SSE 流仍 require_auth。行为级证据盘点后仅两处缺口（其余面既有测试已钉死）：

- POST /api/plugins/{id}/enable 非管理员 403——test_plugin_hotreload 只断言了
  reload/disable 两端点（enable 同为 require_admin 但缺行为级钉死）；
- GET /api/plugins（状态列表）非管理员 403——前端有 403 分支（Plugins.tsx
  backendForbidden），后端无测试。

顺带补未认证 401 面（列表 + 三热管理端点）。既有覆盖留档（不重复）：
- reload/disable 非管理员 403：test_plugin_hotreload.py test_hot_endpoints_require_admin
- SSE 流未认证 401：test_api_events.py test_event_stream_requires_auth
- pricing 跨用户隔离：test_api_pricing.py test_pricing_cross_user_isolation
- pricing 409 乐观并发：test_iter379_pricing_concurrency.py
- 自由笔记跨用户 404：test_api_free_notes.py test_isolation_404
- 知识节点笔记跨用户 404：test_api_notes.py test_note_user_isolation
- SSE 心跳重验断流：test_iter381_events_reauth.py
- api.tools.call 未知工具 404 信封：test_plugin_tools_call.py
"""

from pathlib import Path

PUBLIC_PING = '''
from fastapi import APIRouter

PLUGIN_META = {"id": "amx390", "name": "AMX", "version": "0.1.0"}

def register(api):
    router = APIRouter()

    @router.get("/ping390")
    def ping():
        return {"pong": True}

    api.routes.mount(router, auth=False)
'''


def _setup(tmp_path: Path) -> int:
    """落库一个非管理员用户 + 装载一个插件供热管理端点定位。返回该用户 id。

    iter 397（E6）：**「非管理员」身份不再用 `.env` token 表达**——.env
    AUTH_TOKEN 是运维主凭据，库中无管理员时它会映射到管理员
    （core/security.ensure_admin_for_env_token），用它充当普通用户的前提已
    不成立。改用该普通用户的 JWT（断言一字未改）。

    插件 id/路由路径须全仓唯一：reset_plugins 按设计不摘已挂载路由（docstring
    明示「已挂载的路由随 app 实例存在」——conftest 隔离语义），若与他测试的
    同 id/同路径插件冲突，本测试结束后残留的路由会让该测试的「disable 后 404」
    断言假红（iter 390 实证：复用 test_plugin_hotreload 的 pp/ping 时必炸）。"""
    from app.db.base import SessionLocal
    from app.db.models import User
    from app.core.security import hash_password

    db = SessionLocal()
    try:
        u = User(name="plain", username="plain", password_hash=hash_password("pw123456"), is_admin=False)
        db.add(u)
        db.commit()
        db.refresh(u)
        uid = u.id
    finally:
        db.close()

    (tmp_path / "amx390.py").write_text(PUBLIC_PING, encoding="utf-8")
    from app.main import app
    from app.plugins.loader import load_plugins

    load_plugins(app, tmp_path)
    return uid


def _plain_headers(user_id: int) -> dict:
    """普通用户的 JWT（非管理员身份）。"""
    from app.core.security import create_token

    return {"Authorization": f"Bearer {create_token(user_id, 'plain')}"}


def test_plugins_list_requires_admin(client, tmp_path):
    """GET /api/plugins（状态列表）非管理员 -> 403（Plugins.tsx 既有 403 分支的后端钉死）。"""
    uid = _setup(tmp_path)
    assert client.get("/api/plugins", headers=_plain_headers(uid)).status_code == 403


def test_plugins_enable_requires_admin(client, tmp_path):
    """POST /api/plugins/{id}/enable 非管理员 -> 403（reload/disable 已有钉死，本端点补齐）。"""
    uid = _setup(tmp_path)
    assert client.post("/api/plugins/amx390/enable", headers=_plain_headers(uid)).status_code == 403


def test_plugin_admin_endpoints_unauthenticated_401(client, tmp_path):
    """未认证（无 Authorization）-> 401（列表 + 三热管理端点；不落用户避免依赖用户次序）。"""
    _setup(tmp_path)
    assert client.get("/api/plugins").status_code == 401
    for op in ("reload", "disable", "enable"):
        assert client.post(f"/api/plugins/amx390/{op}").status_code == 401, op

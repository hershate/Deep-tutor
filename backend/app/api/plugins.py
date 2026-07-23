"""后端插件状态端点 + 热管理（API.md「后端插件 API」§可观测性 + §热管理）。

GET /api/plugins（管理员）：加载状态台账（loaded/failed/disabled + 错误原因 + 各插件
注册的路由/事件/工具），便于部署者确认插件装载结果、排查坏插件。

POST /api/plugins/{id}/reload（管理员）：热重载一个已加载的插件（unload + 重新导入 + register）。
POST /api/plugins/{id}/disable（管理员）：禁用一个插件（unload + 持久化禁用状态，重启后仍禁用）。
POST /api/plugins/{id}/enable（管理员）：启用一个被禁用的插件（重新加载）。
"""

from fastapi import APIRouter, Depends
from fastapi import Request

from app.api.deps import require_admin
from app.api.errors import ApiError
from app.db.models import User
from app.plugins import bus
from app.plugins.api import API_VERSION
from app.plugins import loader
from app.plugins.loader import get_plugin_statuses

router = APIRouter(prefix="/plugins", tags=["plugins"])


def _serialize_status(s) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "version": s.version,
        "state": s.state,
        "error": s.error,
        "file": s.file,
        "routes": s.routes,
        "events": s.events,
        "tools": s.tools,
        "parsers": s.parsers,
        "exporters": s.exporters,
        "providers": s.providers,
    }


@router.get("")
def list_plugins(user: User = Depends(require_admin)) -> dict:  # noqa: ARG001
    return {
        "api_version": API_VERSION,
        "plugins": [_serialize_status(s) for s in get_plugin_statuses()],
        "subscribed_events": bus.event_names(),
    }


@router.post("/{plugin_id}/reload")
def reload_plugin(plugin_id: str, request: Request, user: User = Depends(require_admin)) -> dict:  # noqa: ARG001
    """热重载一个已加载的插件（unload + 重新导入 + register）。"""
    app = request.app
    status = loader.reload_plugin(app, plugin_id)
    if status is None:
        raise ApiError("not_found", f"插件 {plugin_id} 不存在", status=404)
    return {"ok": True, "plugin": _serialize_status(status)}


@router.post("/{plugin_id}/disable")
def disable_plugin(plugin_id: str, user: User = Depends(require_admin)) -> dict:  # noqa: ARG001
    """禁用一个插件（unload + 持久化禁用状态）。"""
    ok = loader.disable_plugin(plugin_id)
    if not ok:
        raise ApiError("not_found", f"插件 {plugin_id} 不存在", status=404)
    return {"ok": True}


@router.post("/{plugin_id}/enable")
def enable_plugin(plugin_id: str, request: Request, user: User = Depends(require_admin)) -> dict:  # noqa: ARG001
    """启用一个被禁用的插件（重新加载）。"""
    app = request.app
    status = loader.enable_plugin(app, plugin_id)
    if status is None:
        raise ApiError("not_found", f"插件 {plugin_id} 文件不存在", status=404)
    return {"ok": True, "plugin": _serialize_status(status)}

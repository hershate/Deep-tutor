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
        "webhooks": s.webhooks,  # v1.15.0：注册的 Webhook URL（iter 363 补——可观测性残缺）
        "middleware": s.middleware,  # v1.17.0：注册了中间件的插件 id（高敏感扩展点）
    }


@router.get("")
def list_plugins(user: User = Depends(require_admin)) -> dict:  # noqa: ARG001
    return {
        "api_version": API_VERSION,
        "plugins": [_serialize_status(s) for s in get_plugin_statuses()],
        "subscribed_events": bus.event_names(),
    }


def _gate_plugin_admin(user: User) -> None:
    """iter 371：热管理写端点频控——reload/enable 是重操作（卸载+import+register+回滚登记），
    失控客户端/脚本循环调用会持续占用解释器与文件 IO；admin-only 已限面，再限速率。"""
    from app.core.ratelimit import limiter

    if not limiter.check(f"plugin_admin:{user.id}", 20):
        raise ApiError("rate_limited", "插件热管理操作过于频繁，请稍后再试", status=429)


@router.post("/{plugin_id}/reload")
async def reload_plugin(plugin_id: str, request: Request, user: User = Depends(require_admin)) -> dict:  # noqa: ARG001
    """热重载一个已加载的插件（unload + 重新导入 + register）。

    iter 384 R27：async def——register/回滚在事件循环线程执行（与启动路径 lifespan 内
    load_plugins 同语义）。此前 sync def 跑 anyio 线程池，api.scheduler.every/once 在
    register 内 get_running_loop 必抛 -> 调度器插件永远无法热重载（启动成功、热路径
    failed 的行为不一致）。"""
    _gate_plugin_admin(user)
    app = request.app
    status = loader.reload_plugin(app, plugin_id)
    if status is None:
        raise ApiError("not_found", f"插件 {plugin_id} 不存在", status=404)
    return {"ok": True, "plugin": _serialize_status(status)}


@router.post("/{plugin_id}/disable")
async def disable_plugin(plugin_id: str, user: User = Depends(require_admin)) -> dict:  # noqa: ARG001
    """禁用一个插件（unload + 持久化禁用状态）。

    iter 384 R27：async def——unload 的回滚（含调度任务 cancel）在事件循环线程执行，
    task.cancel() 不再跨线程（asyncio Task 非线程安全，见 scheduler._cancel_task）。"""
    _gate_plugin_admin(user)
    ok = loader.disable_plugin(plugin_id)
    if not ok:
        raise ApiError("not_found", f"插件 {plugin_id} 不存在", status=404)
    return {"ok": True}


@router.post("/{plugin_id}/enable")
async def enable_plugin(plugin_id: str, request: Request, user: User = Depends(require_admin)) -> dict:  # noqa: ARG001
    """启用一个被禁用的插件（重新加载）。语义对齐 reload（iter 384 R27：async def）。"""
    _gate_plugin_admin(user)
    app = request.app
    status = loader.enable_plugin(app, plugin_id)
    if status is None:
        raise ApiError("not_found", f"插件 {plugin_id} 文件不存在", status=404)
    return {"ok": True, "plugin": _serialize_status(status)}

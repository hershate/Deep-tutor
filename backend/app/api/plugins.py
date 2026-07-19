"""后端插件状态端点（API.md「后端插件 API」§可观测性）。

GET /api/plugins（管理员）：加载状态台账（loaded/failed + 错误原因 + 各插件
注册的路由/事件/工具），便于部署者确认插件装载结果、排查坏插件。
"""

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.db.models import User
from app.plugins import bus
from app.plugins.api import API_VERSION
from app.plugins.loader import get_plugin_statuses

router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.get("")
def list_plugins(user: User = Depends(require_admin)) -> dict:  # noqa: ARG001
    return {
        "api_version": API_VERSION,
        "plugins": [
            {
                "id": s.id,
                "name": s.name,
                "version": s.version,
                "state": s.state,
                "error": s.error,
                "file": s.file,
                "routes": s.routes,
                "events": s.events,
                "tools": s.tools,
            }
            for s in get_plugin_statuses()
        ],
        "subscribed_events": bus.event_names(),
    }

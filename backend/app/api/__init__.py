"""API 路由层（plan/03 REST/SSE）。

health/sessions/materials/plans/export/usage/settings/progress 已接入。
"""

from fastapi import APIRouter

from app.api import deps, exports, health, materials, plans, progress, sessions, settings, usage

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(sessions.router)
api_router.include_router(materials.router)
api_router.include_router(plans.router)
api_router.include_router(exports.router)
api_router.include_router(usage.router)
api_router.include_router(settings.router)
api_router.include_router(progress.router)


__all__ = ["api_router", "deps", "exports", "health", "materials", "plans", "progress", "sessions", "settings", "usage"]

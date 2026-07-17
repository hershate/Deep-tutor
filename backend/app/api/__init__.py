"""API 路由层（plan/03 REST/SSE）。

health/sessions/materials 已接入；plans(generate SSE)/export 等于 step 6 后续迭代落地。
"""

from fastapi import APIRouter

from app.api import deps, health, materials, sessions

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(sessions.router)
api_router.include_router(materials.router)


__all__ = ["api_router", "deps", "health", "materials", "sessions"]

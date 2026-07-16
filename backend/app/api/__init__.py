"""API 路由层（plan/03 REST/SSE）。

骨架阶段仅 health；materials/sessions/plans/export 端点于 step 6 落地。
"""

from fastapi import APIRouter

from app.api import deps, health

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)


__all__ = ["api_router", "deps", "health"]

"""健康检查（docker-compose healthcheck 用，plan/09 §十九）。"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

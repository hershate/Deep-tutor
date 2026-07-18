"""健康检查（部署探活/冒烟用，plan/09 §十九）。"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

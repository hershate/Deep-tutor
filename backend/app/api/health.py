"""健康检查（部署探活/冒烟用，plan/09 §十四：DB 连通、skills 加载、磁盘空间）。

恒返回 200，组件状态在 body 内（探活脚本只看 200；运维看 status/components）。
不回显路径等敏感信息（R35）。
"""

from __future__ import annotations

import shutil

from fastapi import APIRouter
from sqlalchemy import text

from app.core.settings import settings
from app.db.base import engine
from app.skills.loader import get_skill_registry

router = APIRouter(tags=["health"])

# 磁盘余量低于该值视为 degraded（生成/导出/上传都吃磁盘）
_DISK_WARN_FREE_MB = 100


@router.get("/health")
async def health() -> dict:
    components: dict[str, object] = {}

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        components["db"] = "ok"
    except Exception:  # noqa: BLE001  健康检查不暴露异常细节
        components["db"] = "error"

    try:
        get_skill_registry()
        components["skills"] = "ok"
    except Exception:  # noqa: BLE001
        components["skills"] = "error"

    # err.md A25：未认证端点不回显内部细节（磁盘余量数值/组件计数），仅给状态档位
    try:
        free_mb = int(shutil.disk_usage(settings.data_dir).free / (1024 * 1024))
        components["disk"] = "low" if free_mb < _DISK_WARN_FREE_MB else "ok"
    except OSError:
        components["disk"] = "error"

    degraded = "error" in components.values() or components["disk"] == "low"
    return {"status": "degraded" if degraded else "ok", "components": components}

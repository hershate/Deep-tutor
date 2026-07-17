"""导出 API（plan/03 §一 export）。

POST /api/export/{plan_id}  导出方案为 md（pdf/slides 在 P1/P5）

从 DB 加载方案（大方案从文件读），调 export_plan 工具渲染 Markdown 并写文件，返回 file_url。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent.context import ExecContext
from app.api.deps import get_db, require_auth
from app.api.errors import ApiError
from app.core.settings import settings
from app.db import models
from app.tools.base import ToolInput
from app.tools.export_plan import export_plan

router = APIRouter(prefix="/export", tags=["export"], dependencies=[Depends(require_auth)])


class ExportIn(BaseModel):
    format: str = "md"


@router.post("/{plan_id}")
async def export_plan_endpoint(plan_id: int, body: ExportIn, db: Session = Depends(get_db)) -> dict:
    plan = db.get(models.Plan, plan_id)
    if plan is None:
        raise ApiError("not_found", f"方案 {plan_id} 不存在")
    content = plan.content
    if not content and plan.content_path:
        try:
            content = json.loads(Path(plan.content_path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            content = {}
    if not content:
        raise ApiError("empty_content", "方案内容为空，无法导出")

    inp = ToolInput(
        tool="export_plan",
        session_id=str(plan.session_id),
        args={"plan_id": str(plan_id), "format": body.format, "plan": content},
    )
    ctx = ExecContext(
        session_id=str(plan.session_id),
        trace_id=f"export-{plan_id}",
        data_dir=settings.data_dir,
    )
    out = await export_plan(inp, ctx)
    if out.status == "error" and out.errors:
        raise ApiError(out.errors[0].code, out.errors[0].message)
    result = {"file_url": out.data["file_url"], "format": out.data.get("format", "md")}
    if out.status == "repaired" and out.errors:
        result["warning"] = out.errors[0].message
    return result

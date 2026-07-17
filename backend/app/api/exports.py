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

from app.agent import run_tool
from app.agent.context import ExecContext
from app.api.deps import get_db, require_auth
from app.api.errors import ApiError
from app.core.settings import settings
from app.db import models
from app.llm import get_provider
from app.tools import default_registry
from app.tools.base import ToolInput
from app.tools.build_plan_slides import SlideSpec, default_spec_from_plan, render_slides_html
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

    if body.format == "slides":
        return await _export_slides(plan_id, plan.session_id, content)
    return await _export_md(plan_id, plan.session_id, content, body.format)


async def _export_md(plan_id: int, session_id: int, content: dict, fmt: str) -> dict:
    """md 导出（export_plan 工具）。非 md 格式（如 pdf）由 export_plan 降级为 md。"""
    inp = ToolInput(
        tool="export_plan",
        session_id=str(session_id),
        args={"plan_id": str(plan_id), "format": fmt, "plan": content},
    )
    ctx = ExecContext(session_id=str(session_id), trace_id=f"export-{plan_id}", data_dir=settings.data_dir)
    out = await export_plan(inp, ctx)
    if out.status == "error" and out.errors:
        raise ApiError(out.errors[0].code, out.errors[0].message)
    result = {"file_url": out.data["file_url"], "format": out.data.get("format", "md")}
    if out.status == "repaired" and out.errors:
        result["warning"] = out.errors[0].message
    return result


async def _export_slides(plan_id: int, session_id: int, content: dict) -> dict:
    """slides 导出：run_tool 生成 SlideSpec（LLM，降级默认）+ 渲染自包含 HTML。

    无 ANTHROPIC_API_KEY 或 LLM 失败 -> default_spec_from_plan 纯 Python 默认 deck（稳定兜底）。
    返回 html 内容（前端 Blob 新窗口打开，plan/03 §93；无需额外文件服务/鉴权）。
    """
    template_path = Path(settings.skills_dir) / "ppt-slide-builder" / "templates" / "base.html"
    try:
        base_template = template_path.read_text(encoding="utf-8")
    except OSError as exc:  # noqa: BLE001  模板缺失（vendored，正常不应发生）
        raise ApiError("internal_error", f"幻灯片模板缺失: {exc}") from exc

    try:
        provider = get_provider()
    except Exception:  # noqa: BLE001  无 key -> 降级默认 deck
        provider = None

    ctx = ExecContext(
        session_id=str(session_id),
        trace_id=f"export-{plan_id}",
        registry=default_registry(),
        llm_provider=provider,
        data_dir=settings.data_dir,
    )
    inp = ToolInput(
        tool="build_plan_slides",
        session_id=str(session_id),
        args={"plan_id": str(plan_id), "plan": content},
    )
    spec_out = await run_tool("build_plan_slides", inp, ctx)
    # ok 用 LLM 规约；repaired/error 降级 default_spec_from_plan(plan)
    if spec_out.status == "ok":
        spec = SlideSpec.model_validate(spec_out.data)
    else:
        spec = default_spec_from_plan(content)

    html = render_slides_html(spec, base_template)
    result: dict = {"file_url": "", "format": "html", "slide_count": len(spec.slides), "html": html}
    if spec_out.status != "ok":
        result["warning"] = "幻灯片规约生成降级，使用默认结构"
    return result

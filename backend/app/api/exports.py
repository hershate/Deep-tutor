"""导出 API（plan/03 §一 export）。

POST /api/export/{plan_id}  导出方案为 md（pdf/slides 在 P1/P5）
  - format=anki & wrong_only=true（iter 134）：仅导出错题（最近作答判错）为独立 .apkg 牌组。

从 DB 加载方案（大方案从文件读），调 export_plan 工具渲染 Markdown 并写文件，返回 file_url。
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agent import BudgetTracker, run_tool
from app.agent.context import ExecContext
from app.api.deps import check_daily_budget, get_current_user, get_db, get_owned_plan, require_auth
from app.api.errors import ApiError
from app.core.ratelimit import limiter
from app.core.settings import settings
from app.db import models
from app.db.base import SessionLocal
from app.db.models import User
from app.llm import get_provider
from app.tools import default_registry
from app.tools.base import ToolInput
from app.tools.build_anki import build_apkg
from app.tools.build_ics import build_ics
from app.tools.build_plan_slides import SlideSpec, default_spec_from_plan, render_slides_html
from app.tools.export_pdf import build_pdf, pdf_available
from app.tools.export_plan import export_plan, render_markdown

log = logging.getLogger("deep_tutor.api.exports")

router = APIRouter(prefix="/export", tags=["export"], dependencies=[Depends(require_auth)])

# 导出速率限制（plan/09 §十八）
_EXPORT_RATE_PER_MIN = 12


class ExportIn(BaseModel):
    # err.md A17：未知格式此前静默走 md 分支（客户端拿到与请求不符的产物）
    format: Literal["md", "pdf", "slides", "ics", "anki"] = "md"
    wrong_only: bool = False  # iter 134：仅导出错题（最近作答判错的题），仅 anki 格式支持


@router.post("/{plan_id}")
async def export_plan_endpoint(
    plan_id: int,
    body: ExportIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    plan = get_owned_plan(db, plan_id, user)  # P4 隔离：他人方案 404
    # 导出速率限制（plan/09 §十八 /export 每分钟限 N）
    if not limiter.check(f"export:{user.id}", _EXPORT_RATE_PER_MIN):
        raise ApiError("rate_limited", f"导出限每分钟 {_EXPORT_RATE_PER_MIN} 次，请稍后再试")
    content = plan.content
    if not content and plan.content_path:
        try:
            content = json.loads(Path(plan.content_path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            content = {}
    if not content:
        raise ApiError("empty_content", "方案内容为空，无法导出")

    if body.format == "slides":
        return await _export_slides(plan_id, plan.session_id, content, user.id)
    if body.format == "ics":
        return await _export_ics(plan_id, content)
    if body.format == "anki":
        return await _export_anki(plan_id, content, db=db, plan=plan, wrong_only=body.wrong_only)
    if body.wrong_only:
        # iter 134：错题导出仅 anki（错题判定依赖 DB 作答记录，md/pdf 等走 content 快照无此口径）
        raise ApiError("bad_request", "错题导出仅支持 anki 格式")
    if body.format == "pdf":
        return await _export_pdf(plan_id, plan.session_id, content)
    return await _export_md(plan_id, plan.session_id, content, body.format)


async def _export_pdf(plan_id: int, session_id: int, content: dict) -> dict:
    """PDF 导出（plan/03 §一）：md -> HTML -> weasyprint。

    R9 降级：weasyprint 不可用（未装/缺 Pango 原生库）-> 降级 md + warning
    （plan/02 §11「不支持格式 -> 降级为 md」），与 OCR/whisper 同一模式。
    weasyprint 为同步 CPU 密集调用，to_thread 防阻塞事件循环。
    """
    if not pdf_available():
        result = await _export_md(plan_id, session_id, content, "md")
        result["warning"] = (
            "PDF 导出依赖 weasyprint（另需系统 Pango 库）不可用，已降级为 Markdown；"
            "安装：pip install weasyprint（Windows 另需 GTK 运行时）"
        )
        return result
    import asyncio

    md = render_markdown(content)
    pdf = await asyncio.to_thread(build_pdf, content, md)
    return {
        "file_url": "",
        "format": "pdf",
        "filename": f"deep-tutor-plan-{plan_id}.pdf",
        "chars": len(md),
        "pdf_b64": base64.b64encode(pdf).decode("ascii"),
    }


async def _export_ics(plan_id: int, content: dict) -> dict:
    """ics 日历导出（P5）：schedule -> RFC 5545 全天事件。内容随响应返回，前端 Blob 下载。"""
    import asyncio

    try:
        # err.md A18：同步 CPU 工作 to_thread，防阻塞事件循环（同 _export_pdf）
        ics_text, events, skipped = await asyncio.to_thread(build_ics, plan_id, content)
    except ValueError as exc:
        raise ApiError("no_schedule", str(exc)) from exc
    result: dict = {
        "file_url": "",
        "format": "ics",
        "filename": f"deep-tutor-plan-{plan_id}.ics",
        "event_count": events,
        "ics": ics_text,
    }
    if skipped:
        result["warning"] = f"{skipped} 条日程日期非法已跳过"
    return result


async def _export_anki(
    plan_id: int,
    content: dict,
    db: Session | None = None,
    plan: models.Plan | None = None,
    wrong_only: bool = False,
) -> dict:
    """Anki 卡片包导出（P5）：quizzes -> .apkg（base64 随响应返回，前端 Blob 下载）。

    wrong_only（iter 134）：只收最近作答判错的题（DB quizzes 表 + QuizAttempt 判定，
    与 GET /plans/{id}/quiz/wrong 同口径）；无错题 -> no_wrong_quizzes。
    """
    import asyncio

    quizzes: list[dict] | None = None
    if wrong_only:
        from app.api.plans import _wrong_quiz_rows

        assert db is not None and plan is not None  # wrong_only 路径由端点保证
        rows = _wrong_quiz_rows(db, plan)
        quizzes = [dict(r.payload or {}) for r in rows]
        if not quizzes:
            raise ApiError("no_wrong_quizzes", "当前没有错题——全部答对或尚未作答，无需导出")

    try:
        # err.md A18：同步 CPU 工作 to_thread，防阻塞事件循环（同 _export_pdf）
        apkg, cards = await asyncio.to_thread(build_apkg, plan_id, content, quizzes, wrong_only)
    except ValueError as exc:
        raise ApiError("no_quizzes", str(exc)) from exc
    return {
        "file_url": "",
        "format": "apkg",
        "filename": f"deep-tutor-plan-{plan_id}{'-wrong' if wrong_only else ''}.apkg",
        "card_count": cards,
        "apkg_b64": base64.b64encode(apkg).decode("ascii"),
    }


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
    result = {
        "file_url": out.data["file_url"],
        "format": out.data.get("format", "md"),
        # UX（iter 73）：md 正文随响应返回，前端 Blob 直接下载（不再只显示服务器路径）
        "content": out.data.get("content", ""),
        "filename": out.data.get("filename", f"{plan_id}.md"),
    }
    if out.status == "repaired" and out.errors:
        result["warning"] = out.errors[0].message
    return result


async def _export_slides(plan_id: int, session_id: int, content: dict, user_id: int) -> dict:
    """slides 导出：run_tool 生成 SlideSpec（LLM，降级默认）+ 渲染自包含 HTML。

    无 ANTHROPIC_API_KEY 或 LLM 失败 -> default_spec_from_plan 纯 Python 默认 deck（稳定兜底）。
    返回 html 内容（前端 Blob 新窗口打开，plan/03 §93；无需额外文件服务/鉴权）。
    user_id：发起用户（P4 iter 58）：供应商档案按该用户解析。
    """
    template_path = Path(settings.skills_dir) / "ppt-slide-builder" / "templates" / "base.html"
    try:
        base_template = template_path.read_text(encoding="utf-8")
    except OSError as exc:  # noqa: BLE001  模板缺失（vendored，正常不应发生）
        # err.md A10：不回显服务器文件系统路径（R35），路径仅进服务端日志
        log.error("幻灯片模板缺失: %s", exc)
        raise ApiError("internal_error", "幻灯片模板缺失") from exc

    check_daily_budget(None, user_id)  # err.md A6：slides LLM 调用纳入日预算守卫

    try:
        provider = get_provider(user_id)
    except Exception:  # noqa: BLE001  无 key -> 降级默认 deck
        provider = None

    ctx = ExecContext(
        session_id=str(session_id),
        trace_id=f"export-{plan_id}",
        registry=default_registry(),
        llm_provider=provider,
        data_dir=settings.data_dir,
        # err.md A6：会话级预算守卫（此前该路径 LLM 调用无预算约束）
        budget=BudgetTracker(limit=settings.budget_per_session_usd),
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

    # err.md A6：slides LLM 用量计入 api_usage（日预算统计口径完整）
    if spec_out.metadata.tokens_in or spec_out.metadata.tokens_out:
        from app.agent.cost import record_metadata_usage

        db = SessionLocal()
        try:
            record_metadata_usage(db, spec_out.metadata, user_id)
        finally:
            db.close()

    html = render_slides_html(spec, base_template)
    result: dict = {"file_url": "", "format": "html", "slide_count": len(spec.slides), "html": html}
    if spec_out.status != "ok":
        result["warning"] = "幻灯片规约生成降级，使用默认结构"
    return result

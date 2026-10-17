"""导出 API（plan/03 §一 export）。

POST /api/export/{plan_id}  导出方案为 md（pdf/slides 在 P1/P5）
  - format=anki & wrong_only=true（iter 134）：仅导出错题（最近作答判错）为独立 .apkg 牌组。
  - 插件格式（iter 307，后端插件 API v1.4.0 扩展点）：api.exporters 注册的 format 经
    exporters.find_exporter 委托插件渲染器；内置优先（override=True 例外，见 API.md）。

GET /api/export/formats  导出格式发现：内置格式 + 插件注册格式（label/plugin_id/override）。

从 DB 加载方案（大方案从文件读），调 export_plan 工具渲染 Markdown 并写文件，返回 file_url。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path

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
from app.plugins import exporters
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
    # iter 307：Literal -> str——插件可经 api.exporters 注册新格式（Literal 会在
    # pydantic 层 422 拦截插件格式）；未知格式改由端点显式 unsupported_format 拒绝
    format: str = "md"
    wrong_only: bool = False  # iter 134：仅导出错题（最近作答判错的题），仅 anki 格式支持


@router.get("/formats")
def list_export_formats() -> dict:
    """导出格式发现（v1.4.0 扩展点）：内置格式 + 插件注册格式。

    外部客户端/替代前端据此动态列出可选导出格式（契约同 GET /materials/formats）。
    """
    return {
        "builtin": sorted(exporters.BUILTIN_FORMATS),
        "plugin": [
            {
                "format": e.format,
                "label": e.label,
                "plugin_id": e.plugin_id,
                "override": e.override,
            }
            for e in exporters.all_entries()
        ],
    }


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

    # iter 307：format 规范化 + 显式校验（内置或插件注册），未知格式 400 而非静默降级 md
    try:
        fmt = exporters.normalize_format(body.format)
    except ValueError:
        fmt = ""
    entry = exporters.find_exporter(fmt)
    if fmt not in exporters.BUILTIN_FORMATS and entry is None:
        raise ApiError(
            "unsupported_format",
            f"不支持的导出格式: {body.format!r}"
            f"（内置: {', '.join(sorted(exporters.BUILTIN_FORMATS))}；"
            "插件扩展格式见 GET /api/export/formats）",
        )

    content = plan.content
    if not content and plan.content_path:
        # 溢出方案 >512KB（上界不封顶）——同步读+json.loads 移 to_thread，防阻塞事件循环
        def _load_overflow() -> dict:
            return json.loads(Path(plan.content_path).read_text(encoding="utf-8"))

        try:
            content = await asyncio.to_thread(_load_overflow)
        except Exception:  # noqa: BLE001
            content = {}
    if not content:
        raise ApiError("empty_content", "方案内容为空，无法导出")

    # 插件导出器优先（find_exporter 对内置格式默认 None，override=True 才接管内置）
    if entry is not None:
        if body.wrong_only and fmt != "anki":
            # wrong_only 是内置 anki 专属口径（依赖 DB 作答记录）；普通插件格式无此语义，
            # 显式拒绝而非静默忽略（同 err.md A17 反静默降级原则）
            raise ApiError("bad_request", "错题导出仅支持 anki 格式")
        return await _export_plugin(entry, plan_id, content, user.id, wrong_only=body.wrong_only)

    if fmt == "slides":
        return await _export_slides(plan_id, plan.session_id, content, user.id)
    if fmt == "ics":
        return await _export_ics(plan_id, content)
    if fmt == "anki":
        return await _export_anki(plan_id, content, db=db, plan=plan, wrong_only=body.wrong_only)
    if body.wrong_only:
        # iter 134：错题导出仅 anki（错题判定依赖 DB 作答记录，md/pdf 等走 content 快照无此口径）
        raise ApiError("bad_request", "错题导出仅支持 anki 格式")
    if fmt == "pdf":
        return await _export_pdf(plan_id, plan.session_id, content)
    return await _export_md(plan_id, plan.session_id, content, fmt)


async def _export_plugin(
    entry: exporters.ExporterEntry,
    plan_id: int,
    content: dict,
    user_id: int,
    *,
    wrong_only: bool = False,
) -> dict:
    """插件导出格式分发（v1.4.0 扩展点）。

    renderer 契约见 api.exporters.register docstring：async 直接 await；sync 经
    to_thread（与内置同步导出同口径，不阻塞事件循环）。插件抛异常 -> export_failed
    （不静默降级为 md——私有格式降级无意义且产物与请求不符，同 err.md A17 原则）。
    返回契约校验：必含 filename（仅取 basename 防路径注入）且含 content(str) 或
    data(bytes，上限 MAX_BYTES) 其一；其余键并入响应（保留键由宿主控制）。
    """
    handler = entry.handler
    ctx = {"plan_id": plan_id, "user_id": user_id, "wrong_only": wrong_only}
    try:
        if asyncio.iscoroutinefunction(handler):
            result = await handler(content, ctx)
        else:
            result = await asyncio.to_thread(handler, content, ctx)
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001  插件渲染失败 -> 明确 export_failed
        raise ApiError(
            "export_failed",
            f"插件导出器 {entry.plugin_id} 处理 {entry.format} 失败: {exc}",
        ) from exc

    try:
        if not isinstance(result, dict):
            raise TypeError(f"返回 {type(result).__name__}，须为 dict")
        filename = result.get("filename")
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("返回须含非空 filename（str）")
        text = result.get("content")
        blob = result.get("data")
        if text is None and blob is None:
            raise ValueError("返回须含 content(str) 或 data(bytes) 其一")
        if text is not None and not isinstance(text, str):
            raise TypeError("content 须为 str")
        if blob is not None:
            if not isinstance(blob, (bytes, bytearray)):
                raise TypeError("data 须为 bytes")
            if len(blob) > exporters.MAX_BYTES:
                raise ValueError(
                    f"data 大小 {len(blob) / 1024 / 1024:.0f}MB 超上限"
                    f" {exporters.MAX_BYTES / 1024 / 1024:.0f}MB"
                )
        resp: dict = {"file_url": "", "format": entry.format, "filename": Path(filename).name}
        if text is not None:
            resp["content"] = text
        if blob is not None:
            resp["bytes_b64"] = base64.b64encode(bytes(blob)).decode("ascii")
        # 其余键（如 mime/自定义元数据）并入响应；保留键由宿主控制（防覆盖 format 等不变量）
        for key, value in result.items():
            if key not in resp and key != "data":
                resp[key] = value
    except (TypeError, ValueError) as exc:
        raise ApiError(
            "export_failed",
            f"插件导出器 {entry.plugin_id} 返回不合规: {exc}（契约见 API.md 导出格式扩展点）",
        ) from exc
    return resp


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
    md = await asyncio.to_thread(render_markdown, content)
    try:
        # 病态内容下 weasyprint 渲染可挂起——wait_for 释放请求（线程不可杀，
        # 但有 12 次/分限流兜底，线程泄漏有界）；超时降级 md 而非 500
        pdf = await asyncio.wait_for(asyncio.to_thread(build_pdf, content, md), timeout=120.0)
    except TimeoutError:
        result = await _export_md(plan_id, session_id, content, "md")
        result["warning"] = "PDF 渲染超时（120s），已降级为 Markdown"
        return result
    return {
        "file_url": "",
        "format": "pdf",
        "filename": f"deep-tutor-plan-{plan_id}.pdf",
        "chars": len(md),
        "pdf_b64": base64.b64encode(pdf).decode("ascii"),
    }


async def _export_ics(plan_id: int, content: dict) -> dict:
    """ics 日历导出（P5）：schedule -> RFC 5545 全天事件。内容随响应返回，前端 Blob 下载。"""
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

    try:
        provider = get_provider(user_id)
    except Exception:  # noqa: BLE001  无 key -> 降级默认 deck
        provider = None

    # err.md A6：slides LLM 调用纳入日预算守卫。iter 247：移到 provider 解析之后——
    # 无 key 用户走纯 Python 默认 deck（零成本），预算耗尽可能性不该拦截免费兜底
    # （md/pdf/ics/anki 等零成本格式本就无此拦截，口径对齐）
    if provider is not None:
        check_daily_budget(None, user_id)

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
    from app.core.concurrency import llm_task_slot

    # iter 368：补全局 LLM 背压（与生成/材料/测验同口径——此前 slides 导出可绕开
    # 信号量，高负载叠加触发供应商限流）
    async with llm_task_slot():
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

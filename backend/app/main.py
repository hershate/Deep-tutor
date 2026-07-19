"""FastAPI 应用入口（plan/01 §二、plan/09 §十八 CORS/异常）。

职责：CORS、统一异常处理（不回显敏感信息 R35）、路由注册、生命周期（启动校验 + 建表）。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from app.api import api_router
from app.core.logging import setup_logging
from app.core.settings import settings
from app.db.base import init_db
from app.skills.loader import get_skill_registry

# 请求体大小上限（防超大 JSON/multipart 体 OOM）：上传限额 + multipart 开销余量。
# Content-Length 预检在路由解析前拒，避免 Starlette 全量读取 body 入内存。
_MAX_BODY_BYTES = settings.max_upload_bytes + 5 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    log = setup_logging(settings.log_level)
    # 启动校验（iter 89 零配置开箱即用：无硬必填，缺失认证配置 -> 开放本机模式，
    # 首次设置引导用户在前端完成；R27 收敛为功能级 key 调用前校验）
    missing = settings.validate_required()
    if missing:  # pragma: no cover - 当前无硬必填，保留钩子供未来收紧
        log.error("启动校验失败，缺失必填配置: %s", missing)
        raise SystemExit(1)
    soft_missing = settings.validate_feature_keys()
    if soft_missing:
        log.warning(
            "功能级 key 缺失（生成/搜索将不可用直到配置，可在前端 设置->供应商档案 添加）: %s",
            soft_missing,
        )
    # err.md E5：弱/默认 AUTH_TOKEN 告警（.env.example 的 change-me-please 忘改即弱认证）
    if settings.auth_token.strip().lower() in {
        "change-me-please",
        "changeme",
        "change-me",
        "test",
        "token",
        "123456",
        "password",
    }:
        log.warning(
            "AUTH_TOKEN 为弱默认值（%r），任何猜到该值的人可调用全部 API——请改为随机长串 "
            "（python -c \"import secrets; print(secrets.token_hex(32))\"）",
            settings.auth_token,
        )
    settings.ensure_dirs()
    # err.md C2：启动即执行 alembic 迁移（旧库增量加列/建表），不再仅依赖
    # scripts/start.* 的前置迁移；随后 init_db 兜底保证模型定义的表齐全
    # （create_all 对已存在表为 no-op；测试夹具 drop_all 后亦靠它重建）。
    from app.db.migrate import run_migrations

    run_migrations()
    init_db()
    # err.md A2：进程崩溃/重启后 orchestrator_state 残留 running 会让该用户
    # 永久 409（配额检查把 stale running 计入并发）。启动时批量收敛为 interrupted，
    # 会话可通过 /generate 重新生成（_terminal_event 回放 error 终态）。
    from sqlalchemy import update

    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        stale = db.execute(
            update(models.OrchestratorState)
            .where(models.OrchestratorState.status == models.OrchestratorStatus.running)
            .values(status=models.OrchestratorStatus.interrupted)
        )
        if stale.rowcount:
            db.commit()
            log.warning("启动恢复：%d 个 stale running 会话已收敛为 interrupted", stale.rowcount)
        # P4 多用户（iter 57）：启动收敛管理员账号（旧单用户数据归管理员）
        from app.core.security import ensure_admin_user

        ensure_admin_user(db)
    finally:
        db.close()
    if not settings.admin_password and not settings.auth_token_configured:
        log.info("未配置认证（ADMIN_PASSWORD/AUTH_TOKEN 均空）：以开放本机模式运行——"
                 "免登录直接使用，局域网内任何人可访问；可在前端设置页设置访问密码")
    elif not settings.admin_password:
        log.warning("ADMIN_PASSWORD 未配置：JWT 登录不可用，仅 AUTH_TOKEN 回退（P4 多用户）")
    # err.md C17：CORS * 兜底告警（配合凭证/自定义头时是危险配置）
    if "*" in settings.cors_origin_list:
        log.warning(
            "CORS_ORIGINS 含通配符 *：任何来源网页均可携带 X-Auth-Token 调用 API，"
            "公网部署请务必收敛为具体来源（逗号分隔）"
        )
    # 多模态依赖检测（P3，R9）：缺失仅告警——对应入口按 dependency_missing 报错 + 提示安装
    from app.tools.parse_material import ocr_available, whisper_available

    if not ocr_available():
        log.warning("图片 OCR 不可用：未检测到 tesseract/pytesseract（安装后上传图片方可 OCR）")
    if not whisper_available():
        log.warning("音视频转写不可用：未安装 faster-whisper（pip install faster-whisper）")
    skills = get_skill_registry()  # plan/05 §三.2 启动扫描 skills/ -> 注册表
    # 后端插件（API.md「后端插件 API」）：PLUGINS_DIR 目录自动发现 + 隔离加载，
    # 坏插件记 failed 不阻塞启动；随后广播 startup 事件（监听器异常各自隔离）
    from app.plugins import bus as plugin_bus
    from app.plugins.loader import load_plugins

    plugin_statuses = load_plugins(app)
    plugin_failed = sum(1 for s in plugin_statuses if s.state == "failed")
    log.info(
        "Deep-tutor 后端启动完成 (model=%s, skills=%d, plugins=%d/%d failed=%d)",
        settings.anthropic_model,
        len(skills),
        len(plugin_statuses) - plugin_failed,
        len(plugin_statuses),
        plugin_failed,
    )
    await plugin_bus.emit_async("startup", {})
    # 定期运维（plan/09 §一：VACUUM + 清孤儿文件，每 24h 后台执行）
    import asyncio

    from app.core.maintenance import maintenance_loop

    stop_event = asyncio.Event()
    maintenance_task = asyncio.create_task(maintenance_loop(stop_event))
    yield
    await plugin_bus.emit_async("shutdown", {})
    stop_event.set()
    maintenance_task.cancel()
    try:
        await maintenance_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    log.info("Deep-tutor 后端关闭")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Deep-tutor",
        version="0.1.0",
        description="AI 驱动的学习方案生成 Web 应用",
        lifespan=lifespan,
    )

    # CORS（plan/09 §十八：仅允许 CORS_ORIGINS；允许凭证；指定允许头）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "X-Auth-Token"],  # err.md A21：X-Auth-Token 认证方式对浏览器跨域可用
    )

    # 请求体大小限制（Content-Length 预检，防超大 body OOM；路由解析前拒）
    @app.middleware("http")
    async def _limit_request_body(request, call_next):
        cl = request.headers.get("content-length")
        if cl:
            try:
                if int(cl) > _MAX_BODY_BYTES:
                    return JSONResponse(
                        {"error": {"code": "payload_too_large", "message": "请求体超上限", "details": {}}},
                        status_code=413,
                    )
            except ValueError:
                pass
            return await call_next(request)
        # err.md A5/C18：chunked 传输无 Content-Length，预检被绕过 -> 流式限量读取，
        # 超限即拒；未超限则把已读 body 重新包装给下游
        if "chunked" in request.headers.get("transfer-encoding", "").lower():
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > _MAX_BODY_BYTES:
                    return JSONResponse(
                        {"error": {"code": "payload_too_large", "message": "请求体超上限", "details": {}}},
                        status_code=413,
                    )

            async def _receive():
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            from starlette.requests import Request as _StarletteRequest

            request = _StarletteRequest(request.scope, _receive)
        return await call_next(request)

    app.include_router(api_router)

    # 统一错误处理（plan/03 错误格式、R35 不回显敏感信息）
    from fastapi import HTTPException
    from fastapi.exceptions import RequestValidationError

    from app.api import errors

    app.add_exception_handler(errors.ApiError, errors.api_error_handler)
    app.add_exception_handler(HTTPException, errors.http_exception_handler)
    # err.md A4：Starlette 层 404/405 与请求校验 422 同样走统一错误格式
    app.add_exception_handler(StarletteHTTPException, errors.starlette_http_exception_handler)
    app.add_exception_handler(RequestValidationError, errors.request_validation_handler)
    app.add_exception_handler(Exception, errors.unhandled_exception_handler)

    # 免 nginx 本地直起（iter 67）：FRONTEND_DIST 指向构建产物时由后端托管 SPA。
    # 挂在 /api 路由之后：/api/* 恒进 API 路由，未命中的前端路由回退 index.html。
    if settings.frontend_dist:
        dist = Path(settings.frontend_dist)
        if (dist / "index.html").is_file():
            app.mount("/", _SPAStaticFiles(directory=str(dist), html=True), name="spa")
        else:
            logging.getLogger(__name__).warning(
                "FRONTEND_DIST=%s 下无 index.html，SPA 托管未启用", dist
            )

    return app


class _SPAStaticFiles(StaticFiles):
    """SPA 静态托管：未命中的非 /api 路径回退 index.html（前端路由），/api 404 不吞。"""

    async def get_response(self, path: str, scope: Scope) -> Response:
        # 用 scope["path"]（恒为 / 前缀的 URL 路径）判定：StaticFiles 传入的 path
        # 会被转为 OS 分隔符（Windows 下 api\xxx），不可移植。
        norm = scope.get("path", "").lstrip("/")
        if norm == "api" or norm.startswith("api/"):
            return JSONResponse(
                {"error": {"code": "not_found", "message": "接口不存在", "details": {}}},
                status_code=404,
            )
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


app = create_app()

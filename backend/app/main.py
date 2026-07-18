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
    # 启动校验必填项（R27）
    missing = settings.validate_required()
    if missing:
        log.error("启动校验失败，缺失必填配置: %s", missing)
        raise SystemExit(1)
    soft_missing = settings.validate_feature_keys()
    if soft_missing:
        log.warning(
            "功能级 key 缺失（生成/搜索将不可用直到配置）: %s", soft_missing
        )
    settings.ensure_dirs()
    init_db()
    # P4 多用户（iter 57）：启动收敛管理员账号（旧单用户数据归管理员）
    from app.core.security import ensure_admin_user
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        ensure_admin_user(db)
    finally:
        db.close()
    if not settings.admin_password:
        log.warning("ADMIN_PASSWORD 未配置：JWT 登录不可用，仅 AUTH_TOKEN 回退（P4 多用户）")
    # 多模态依赖检测（P3，R9）：缺失仅告警——对应入口按 dependency_missing 报错 + 提示安装
    from app.tools.parse_material import ocr_available, whisper_available

    if not ocr_available():
        log.warning("图片 OCR 不可用：未检测到 tesseract/pytesseract（安装后上传图片方可 OCR）")
    if not whisper_available():
        log.warning("音视频转写不可用：未安装 faster-whisper（pip install faster-whisper）")
    skills = get_skill_registry()  # plan/05 §三.2 启动扫描 skills/ -> 注册表
    log.info("Deep-tutor 后端启动完成 (model=%s, skills=%d)", settings.anthropic_model, len(skills))
    yield
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
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
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

    app.include_router(api_router)

    # 统一错误处理（plan/03 错误格式、R35 不回显敏感信息）
    from app.api import errors
    from fastapi import HTTPException

    app.add_exception_handler(errors.ApiError, errors.api_error_handler)
    app.add_exception_handler(HTTPException, errors.http_exception_handler)
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

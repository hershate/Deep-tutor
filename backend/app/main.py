"""FastAPI 应用入口（plan/01 §二、plan/09 §十八 CORS/异常）。

职责：CORS、统一异常处理（不回显敏感信息 R35）、路由注册、生命周期（启动校验 + 建表）。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import api_router
from app.core.logging import setup_logging
from app.core.settings import settings
from app.db.base import init_db


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
    log.info("Deep-tutor 后端启动完成 (model=%s)", settings.anthropic_model)
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

    app.include_router(api_router)

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception):  # noqa: ARG001
        # 统一错误格式；不回显敏感信息（路径/key 等，R35）
        logging.getLogger("deep_tutor").exception("未处理异常: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "内部错误"}},
        )

    return app


app = create_app()

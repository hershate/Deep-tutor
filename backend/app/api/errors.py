"""统一错误响应（plan/03 §错误响应统一格式、plan/09 §十六 错误码、R35 不回显敏感信息）。

所有错误返回 {"error": {"code", "message", "details"}}。
ApiError 用于业务错误；HTTPException/未捕获异常统一转换为此格式。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        self.status = status or CODE_STATUS.get(code, 400)
        super().__init__(message)


# 错误码 -> HTTP 状态（plan/09 §十六）。扩展性：新增码在此登记。
CODE_STATUS: dict[str, int] = {
    "unauthorized": 401,
    "not_found": 404,
    "conflict": 409,
    "budget_exceeded": 402,
    "unsupported_format": 400,
    "empty_content": 422,
    "payload_too_large": 413,
    "upstream_timeout": 504,
    "no_search_key": 503,
    "ssrf_blocked": 400,
    "validation_error": 422,
    "missing_upstream_field": 400,
    "no_schedule": 422,
    "no_quizzes": 422,
    "invalid_scope": 422,
    "quota_exceeded": 429,
    "rate_limited": 429,
    "server_busy": 503,
    "internal_error": 500,
}


def _error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:  # noqa: ARG001
    return JSONResponse(status_code=exc.status, content=_error_body(exc.code, exc.message, exc.details))


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:  # noqa: ARG001
    code = _status_to_code(exc.status_code)
    msg = str(exc.detail) if exc.detail is not None else code
    return JSONResponse(status_code=exc.status_code, content=_error_body(code, msg))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
    # 不回显敏感信息（路径/key 等，R35）
    import logging

    logging.getLogger("deep_tutor").exception("未处理异常: %s", exc)
    return JSONResponse(
        status_code=500,
        content=_error_body("internal_error", "内部错误"),
    )


def _status_to_code(status: int) -> str:
    for code, st in CODE_STATUS.items():
        if st == status:
            return code
    return "internal_error" if status >= 500 else "validation_error"

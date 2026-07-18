"""统一错误响应（plan/03 §错误响应统一格式、plan/09 §十六 错误码、R35 不回显敏感信息）。

所有错误返回 {"error": {"code", "message", "details"}}。
ApiError 用于业务错误；HTTPException/未捕获异常统一转换为此格式。
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


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
    "forbidden": 403,
    "not_found": 404,
    "method_not_allowed": 405,
    "conflict": 409,
    "budget_exceeded": 402,
    "bad_request": 400,
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
    "bad_gateway": 502,
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


async def starlette_http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:  # noqa: ARG001
    """err.md A4：路由未命中 404/405 等 Starlette 层异常同样走统一错误格式。"""
    code = _status_to_code(exc.status_code)
    msg = str(exc.detail) if exc.detail else code
    return JSONResponse(status_code=exc.status_code, content=_error_body(code, msg))


async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:  # noqa: ARG001
    """err.md A4：请求体/参数校验失败（422）走统一错误格式，details 截断防巨型回显。"""
    try:
        errs = jsonable_encoder(exc.errors())[:20]
    except Exception:  # noqa: BLE001
        errs = []
    return JSONResponse(
        status_code=422,
        content=_error_body("validation_error", "请求参数校验失败", {"errors": errs}),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
    # 不回显敏感信息（路径/key 等，R35）
    import logging

    logging.getLogger("deep_tutor").exception("未处理异常: %s", exc)
    return JSONResponse(
        status_code=500,
        content=_error_body("internal_error", "内部错误"),
    )


# err.md A20：HTTP 状态 -> 通用错误码的独立映射（此前复用 CODE_STATUS 取首个匹配，
# 任意 400 被打成 "unsupported_format" 等不相干业务码，误导前端分支处理）
_STATUS_TO_CODE: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    402: "budget_exceeded",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "server_busy",
    504: "upstream_timeout",
}


def _status_to_code(status: int) -> str:
    return _STATUS_TO_CODE.get(status, "internal_error" if status >= 500 else "bad_request")

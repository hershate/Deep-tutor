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


class UnknownToolError(ApiError, ValueError):
    """api.tools.call 未注册工具（iter 385 R28）。

    双继承：``except ValueError`` 的既有插件与 api.tools.call 契约（API.md「未知工具 ->
    ValueError」）保持兼容；未捕获时经宿主统一错误信封以 404 呈现——此前裸 ValueError
    逃出插件路由成 500，插件热管理窗口期（unload 后在途路由请求 / reload 重注册前）
    的在途调用被错误地恶化为服务器错误。定义在本叶子模块：plugins.api 顶层导入会
    构成 app.api.__init__ 循环，门面在抛出点函数内局部导入。"""

    def __init__(self, message: str) -> None:
        ApiError.__init__(self, "not_found", message, status=404)
        ValueError.__init__(self, message)


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
    "search_unavailable": 503,  # iter 222：免 Key 网页引擎全面失败（区别于 no_search_key 的显式 bing 无 key）
    "no_llm_provider": 503,  # iter 212：知识库问答/生成需 LLM 但用户未配置任何供应商
    "ssrf_blocked": 400,
    "validation_error": 422,
    "missing_upstream_field": 400,
    "no_schedule": 422,
    "no_quizzes": 422,
    "no_wrong_quizzes": 422,  # iter 134 错题导出无错题（与 no_quizzes 同族：无内容可导出）
    "invalid_scope": 422,
    "quota_exceeded": 429,
    "rate_limited": 429,
    "server_busy": 503,
    "bad_gateway": 502,
    "internal_error": 500,
    # iter 397（K9）：解析管线的三个错误码此前**未登记** -> ApiError 落到默认 400。
    # 此处显式登记为 **400**，即保持既有 HTTP 语义**完全不变**（note/11：v1 契约冻结，
    # 只改勘误不改语义——把 dependency_missing 升为 503 / parse_failed、degraded 升为
    # 422 属契约变更，需先改 API.md 与前端分支，另行裁决；tests/test_api.py 的
    # test_upload_image_png_passes_validation 即钉住 dependency_missing -> 400）。
    "degraded": 400,  # 解析降级（marker_single 失败 / URL 抓取异常）
    "parse_failed": 400,  # 解析失败（格式损坏/加密/内容为空）
    "dependency_missing": 400,  # 依赖缺失（tesseract / pypdf / faster-whisper 未安装）
}


def raise_parse_failure(out: object, *, default_code: str = "parse_failed") -> None:
    """解析类工具输出为失败时抛 ApiError（iter 397 K16）。

    守卫此前写成 ``out.status == "error" and out.errors``：status 为 error 而
    errors 为空（``ToolOutput.errors`` 默认空列表）时**继续往下走** ->
    ``out.data.get("text", "")`` 得空串 -> **用空文本覆盖既有解析文本**并把文档
    标成 ``parsed``（``parsed_chars=0``）——与端点承诺「解析失败行保留状态不变
    （用户可换档重试），不删文档」正相反：失败时反而把好数据清了。

    本助手：status 非 error -> 放行（"ok"/"repaired" 均非失败）；error -> 抛
    ApiError（errors 为空时用 default_code，保证响应总有可识别错误码）。
    """
    if getattr(out, "status", "ok") != "error":
        return
    errors = getattr(out, "errors", None) or []
    if errors:
        raise ApiError(errors[0].code, errors[0].message)
    raise ApiError(default_code, "解析失败")


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


# iter 392：SQLite 写锁争用（database is locked / database is busy）——SQLite 单写者，
# 写-写争用超 busy_timeout 即抛此错。此前落到通用 500（不可重试语义），前端无从
# 提示用户稍后重试；实为瞬时资源争用，应回 503 + Retry-After。
_DB_BUSY_MARKERS = (
    "database is locked",
    "database is busy",
    "database table is locked",
    "sqlite_busy",
)


def is_db_busy_error(exc: BaseException) -> bool:
    """判定异常是否为数据库写锁争用（busy/locked）。

    SQLAlchemy 将 DBAPI 异常包在 ``.orig`` 里；此处两处都查。
    """
    for candidate in (getattr(exc, "orig", None), exc):
        if candidate is None:
            continue
        msg = str(candidate).lower()
        if any(marker in msg for marker in _DB_BUSY_MARKERS):
            return True
    return False


async def db_busy_exception_handler(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
    """数据库写锁争用 -> 503 + Retry-After（可重试）；非争用错误仍走 500。"""
    import logging

    if not is_db_busy_error(exc):
        logging.getLogger("deep_tutor").exception("未处理异常: %s", exc)
        return JSONResponse(
            status_code=500,
            content=_error_body("internal_error", "内部错误"),
        )
    logging.getLogger("deep_tutor").warning("数据库写锁争用，返回 503：%s", exc)
    resp = JSONResponse(
        status_code=503,
        content=_error_body("server_busy", "数据库繁忙（写入争用），请稍后重试"),
    )
    resp.headers["Retry-After"] = "1"
    return resp


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

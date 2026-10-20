"""FastAPI 应用入口（plan/01 §二、plan/09 §十八 CORS/异常）。

职责：CORS、统一异常处理（不回显敏感信息 R35）、路由注册、生命周期（启动校验 + 建表）。
"""

from __future__ import annotations

import asyncio
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
from app.core.dbwrite import commit_async, shutdown_pool
from app.core.logging import setup_logging
from app.core.settings import settings
from app.db.base import init_db
from app.skills.loader import get_skill_registry

# 请求体大小上限（防超大 JSON/multipart 体 OOM）：上传限额 + multipart 开销余量。
# Content-Length 预检在路由解析前拒，避免 Starlette 全量读取 body 入内存。
_MAX_BODY_BYTES = settings.max_upload_bytes + 5 * 1024 * 1024

# iter 393：备份导入/预检的 body 上限——此前这两个路径被**完全豁免**通用上限。
# 豁免的理由（v2 备份含 kb 原件可超通用上限 ~55MB，不豁免则「导出后导不回」）成立，
# 但「无上限」本身是洞：Starlette 对 multipart **文件 part 无大小限制**
# （max_part_size 只管非文件字段），文件写入 SpooledTemporaryFile（>1MB 落系统临时
# 目录）；且 FastAPI 先 `body = await request.form()` 解析 body、再 solve_dependencies
# 跑鉴权（fastapi/routing.py:423 vs :474）——未认证客户端可用单请求把任意大的体
# 写进临时目录。iter 393 实证：64MB 体落盘后仍返回 401。
# 故改为按路径给更高但**有界**的阈值：256MB（备份文件上限）+ multipart 开销余量。
_BACKUP_BODY_LIMIT = 256 * 1024 * 1024 + 5 * 1024 * 1024

# iter 393：全局在途请求体预算——单请求有界仍挡不住并发叠加（N 个并发上传 =
# N × 单请求上限的临时磁盘占用，且认证前即可触发）。超预算 -> 503 + Retry-After，
# 保证系统临时目录的请求体占用有硬上界。
_BODY_BUDGET_BYTES = 1024 * 1024 * 1024  # 1GB
_body_inflight = 0

# iter 391：孤立生成流回收器周期（秒）
_ORPHAN_REAPER_INTERVAL = 15.0


def _acquire_body_budget(nbytes: int) -> bool:
    """占用全局在途请求体预算；超预算返回 False（调用方回 503 + Retry-After）。

    iter 393：单请求体上限只约束「一个请求」，并发上传仍可叠加出 N × 上限的
    系统临时目录占用（Starlette 把 multipart 文件落 SpooledTemporaryFile）。
    此处给聚合占用设硬上界，超出即拒（背压语义，可重试）。
    """
    global _body_inflight
    if nbytes <= 0:
        return True
    if _body_inflight + nbytes > _BODY_BUDGET_BYTES:
        return False
    _body_inflight += nbytes
    return True


def _release_body_budget(nbytes: int) -> None:
    global _body_inflight
    if nbytes <= 0:
        return
    _body_inflight = max(0, _body_inflight - nbytes)


def _body_budget_response() -> JSONResponse:
    resp = JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "server_busy",
                "message": "并发上传总量超上限，请稍后重试",
                "details": {},
            }
        },
    )
    resp.headers["Retry-After"] = "1"
    return resp


async def _orphan_stream_reaper(stop_event) -> None:
    """周期回收孤立的生成流（iter 391）。

    孤立 = 占位成功但编排任务从未启动：客户端在端点执行期间断开 -> 响应体
    生成器永不被迭代 -> 任务从未创建 -> 该流永久留在 _STREAMS（terminal_id
    is None），既占全局生成槽位（8 条即全服生成 503），又让会话停在 running 态
    （后续 /generate 只能续接到一条永不产事件的死流）。

    iter 392：状态收敛含 DB 写，移 to_thread——写锁争用时可等 busy_timeout（10s），
    留在事件循环会冻结全部请求与 SSE 心跳。终态事件发布经 _SessionStream 的
    跨线程投递路径转交事件循环（订阅队列属该循环）。
    """
    from app.api.plans import _reap_orphan_streams

    reaper_log = logging.getLogger("deep_tutor.orphan_reaper")

    try:
        while True:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_ORPHAN_REAPER_INTERVAL)
                return  # stop_event 已置位 -> 正常退出
            except asyncio.TimeoutError:
                pass
            try:
                await asyncio.to_thread(_reap_orphan_streams)
            except Exception:  # noqa: BLE001  回收失败不影响下一轮
                reaper_log.warning("孤立生成流回收异常", exc_info=True)
    except asyncio.CancelledError:
        raise


async def _private_provider_base_urls(pairs: list[tuple[str, str]]) -> list[str]:
    """筛出 base_url 指向私网/环回/元数据的供应商档案（iter 394 启动告警用）。

    pairs：[(档案名, base_url)]。DNS 解析放线程（不占事件循环，iter 393 口径）。
    返回可直接写入日志的描述列表。
    """
    from app.core.urlguard import check_outbound_url, display_url

    hits: list[str] = []
    for name, url in pairs:
        ok, reason = await asyncio.to_thread(check_outbound_url, url)
        if not ok:
            hits.append(f"{name} -> {display_url(url)}（{reason}）")
    return hits


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
            "功能级 key 缺失（生成将不可用直到配置，可在前端 设置->供应商档案 添加）: %s",
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
    # iter 368：JWT_SECRET 弱值告警（显式 env 配置才可能弱——空值走 .jwt_secret
    # 持久化随机串，进程级随机兜底，均安全）。过短或常见默认值可被离线伪造全部 JWT
    if settings.jwt_secret and (
        settings.jwt_secret.strip().lower()
        in {"secret", "jwt-secret", "change-me", "changeme", "change-me-please", "test", "123456"}
        or len(settings.jwt_secret) < 32
    ):
        log.warning(
            "JWT_SECRET 为弱值（过短或常见默认串），可被离线暴力破解后伪造任意用户 JWT——"
            "请改为随机长串（python -c \"import secrets; print(secrets.token_hex(32))\"）"
        )
    settings.ensure_dirs()
    # iter 392：登记主事件循环——同步端点在线程池线程发布 SSE 终态事件时，
    # 须经本循环投递（订阅队列属该循环，跨线程 set_result 不安全）。
    from app.api.plans import set_main_loop

    set_main_loop(asyncio.get_running_loop())
    # err.md C2：启动即执行 alembic 迁移（旧库增量加列/建表），不再仅依赖
    # scripts/start.* 的前置迁移；随后 init_db 兜底保证模型定义的表齐全
    # （create_all 对已存在表为 no-op；测试夹具 drop_all 后亦靠它重建）。
    from app.db.migrate import run_migrations

    run_migrations()
    init_db()
    # iter 394：历史明文个人访问令牌升级为 sha256 摘要存储（明文入库 -> 库/备份
    # 泄露即令牌泄露）。启动期一次性收敛（幂等）；写库走线程池，保持「DB 写不在
    # 事件循环线程」的一致口径。失败不阻断启动（旧明文行仍可认证）。
    from app.core.credentials import upgrade_plaintext_tokens

    try:
        _upgraded = await asyncio.to_thread(upgrade_plaintext_tokens)
        if _upgraded:
            log.info("已将 %d 条历史明文访问令牌升级为摘要存储", _upgraded)
    except Exception:  # noqa: BLE001  升级失败不影响启动
        log.warning("历史明文访问令牌升级失败（不影响启动）", exc_info=True)
    # err.md A2：进程崩溃/重启后 orchestrator_state 残留 running 会让该用户
    # 永久 409（配额检查把 stale running 计入并发）。启动时批量收敛为 interrupted，
    # 会话可通过 /generate 重新生成（_terminal_event 回放 error 终态）。
    from sqlalchemy import update

    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    _active_provider_urls: list[tuple[str, str]] = []
    try:
        stale = db.execute(
            update(models.OrchestratorState)
            .where(models.OrchestratorState.status == models.OrchestratorStatus.running)
            .values(status=models.OrchestratorStatus.interrupted)
        )
        if stale.rowcount:
            await commit_async(db)
            log.warning("启动恢复：%d 个 stale running 会话已收敛为 interrupted", stale.rowcount)
        # iter 345 记忆系统：收敛 stale running runs（崩溃/重启残留 -> interrupted；
        # 弃用 resume 改"新 run"续——历史在 messages，连续性不丢；半截 tool_use 重放非幂等）
        stale_runs = db.execute(
            update(models.Run)
            .where(models.Run.status == models.RunStatus.running)
            .values(status=models.RunStatus.interrupted)
        )
        if stale_runs.rowcount:
            await commit_async(db)
            log.warning("启动恢复：%d 个 stale running run 已收敛为 interrupted", stale_runs.rowcount)
        # P4 多用户（iter 57）：启动收敛管理员账号（旧单用户数据归管理员）
        from app.core.security import ensure_admin_user

        ensure_admin_user(db)
        # iter 394：存量 active 供应商档案的 base_url 若指向私网/环回/元数据，
        # 其调用会被 llm.get_provider 拒绝（RuntimeError）。规则引入前建档的
        # 用户不会在「写档案」时被拦，只在真正生成时才发现——启动期点名告警。
        _active_provider_urls = [
            (p.name, p.base_url)
            for p in db.query(models.LlmProviderProfile)
            .filter(models.LlmProviderProfile.is_active.is_(True))
            .filter(models.LlmProviderProfile.base_url != "")
            .all()
        ]
    finally:
        db.close()
    if _active_provider_urls and not settings.allow_private_llm_base_url:
        _hits = await _private_provider_base_urls(_active_provider_urls)
        if _hits:
            log.warning(
                "%d 个 active 供应商档案的 base_url 指向私网/环回/元数据，LLM 调用将被拒绝：%s"
                "（若确为内网模型服务，请设 ALLOW_PRIVATE_LLM_BASE_URL=1）",
                len(_hits),
                "；".join(_hits),
            )
    if not settings.admin_password and not settings.auth_token_configured:
        log.info("未配置认证（ADMIN_PASSWORD/AUTH_TOKEN 均空）：以开放本机模式运行——"
                 "免登录直接使用，局域网内任何人可访问；可在前端设置页设置访问密码。"
                 "（iter 394：首次设置向导 /api/setup、/api/setup/dismiss 仅接受本机回环请求）")
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
    from app.core.maintenance import maintenance_loop

    stop_event = asyncio.Event()
    maintenance_task = asyncio.create_task(maintenance_loop(stop_event))
    # iter 391：孤立生成流周期回收器（15s）——占位成功但编排任务从未启动的流
    # （客户端在端点执行期间断开，响应体生成器永不被迭代）会永久占用全局生成
    # 槽位并让会话停在 running 态。maintenance_loop 周期为 24h，对这类秒级泄漏
    # 太慢，故单独设快频回收器。
    orphan_reaper_task = asyncio.create_task(_orphan_stream_reaper(stop_event))
    yield
    await plugin_bus.emit_async("shutdown", {})
    # iter 367（批次四）：优雅关停——取消在途生成/对话任务并等收口。分钟级 LLM 流
    # 否则会把 uvicorn 关停挂住（无超时则无限等；硬杀则丢 finalize 落账，stale
    # running 只能等下次启动恢复收敛）。取消后 plans 走 _finalize 落 error 终态、
    # memory 走 CancelledError -> _finalize_chat 落 interrupted，均为既有路径
    from app.api.memory import _RUN_STREAMS
    from app.api.plans import _STREAMS

    pending: list[asyncio.Task] = []
    for st in list(_STREAMS.values()):
        task = getattr(st, "task", None)
        if task is not None and not task.done() and st.terminal_id is None:
            task.cancel()
            pending.append(task)
    for st in list(_RUN_STREAMS.values()):
        try:
            st.request_cancel()
        except Exception:  # noqa: BLE001  测试假流可能未实现——跳过软取消
            pass
        task = getattr(st, "task", None)
        if task is not None and not task.done():
            task.cancel()
            pending.append(task)
    if pending:
        _done, still = await asyncio.wait(pending, timeout=15)
        if still:
            log.warning("关停时 %d 个任务未在 15s 内收口（放弃等待，由启动恢复兜底）", len(still))
    # iter 392：等待 finalize 收口任务（持久化在 worker 线程，事件循环侧投递终态）。
    # 不等则循环关闭时收口丢失 -> 会话停在 running 态（只能靠下次启动恢复收敛）。
    from app.api.plans import _FINALIZE_TASKS

    fin_pending = [t for t in list(_FINALIZE_TASKS) if not t.done()]
    if fin_pending:
        _done, still = await asyncio.wait(fin_pending, timeout=10)
        if still:
            log.warning("关停时 %d 个 finalize 收口未在 10s 内完成（由启动恢复兜底）", len(still))
    stop_event.set()
    maintenance_task.cancel()
    orphan_reaper_task.cancel()
    # iter 392：注销主事件循环——避免关停后仍有线程池请求向已关闭循环投递终态事件
    set_main_loop(None)
    try:
        await maintenance_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    try:
        await orphan_reaper_task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    # iter 393：回收写线程池（写提交移出事件循环的专用池）——不再等在途写完成
    shutdown_pool()
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
        # iter 393：备份导入/预检不再**豁免**上限，改按路径给更高但有界的阈值
        # （v2 备份含 kb 原件可超通用上限 ~55MB，不设则「导出后导不回」；但无上限
        # 时未认证客户端可用单请求写满系统临时目录——见 _BACKUP_BODY_LIMIT 注释）。
        is_backup = request.url.path in ("/api/backup/import", "/api/backup/preview")
        limit = _BACKUP_BODY_LIMIT if is_backup else _MAX_BODY_BYTES
        cl = request.headers.get("content-length")
        if cl:
            try:
                declared = int(cl)
            except ValueError:
                declared = 0
            if declared > limit:
                return JSONResponse(
                    {"error": {"code": "payload_too_large", "message": "请求体超上限", "details": {}}},
                    status_code=413,
                )
            # iter 393：全局在途体预算（并发上传叠加的临时磁盘占用上界）
            if not _acquire_body_budget(declared):
                return _body_budget_response()
            try:
                return await call_next(request)
            finally:
                _release_body_budget(declared)
        # err.md A5/C18：chunked 传输无 Content-Length，预检被绕过 -> 流式限量读取，
        # 超限即拒；未超限则把已读 body 重新包装给下游
        if "chunked" in request.headers.get("transfer-encoding", "").lower():
            # 无 Content-Length：按上限计入预算（最坏情况）
            if not _acquire_body_budget(limit):
                return _body_budget_response()
            body = bytearray()
            try:
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > limit:
                        return JSONResponse(
                            {"error": {"code": "payload_too_large", "message": "请求体超上限", "details": {}}},
                            status_code=413,
                        )

                async def _receive():
                    return {"type": "http.request", "body": bytes(body), "more_body": False}

                from starlette.requests import Request as _StarletteRequest

                request = _StarletteRequest(request.scope, _receive)
                return await call_next(request)
            finally:
                _release_body_budget(limit)
        return await call_next(request)

    # 安全响应头（iter 371，网络暴露面基础防护）。注册在 body 限流之后
    # （Starlette 后注册者在外层）——413 短路响应同样带头：
    # - nosniff：防 MIME 嗅探把用户上传内容当脚本执行（KB/材料文件经本服务回传）
    # - X-Frame-Options DENY：防点击劫持（token 存 localStorage 的 SPA 被嵌框架钓鱼）
    # - Referrer-Policy no-referrer：防 URL（含分享 token 等 query）外泄给第三方链接
    # CSP（iter 372 R15）：v1 插件体系依赖运行时注入 inline 脚本/样式——script/style
    # 保留 'unsafe-inline'（并已核全仓零 eval/new Function，**不含** unsafe-eval），
    # 其余全收紧：connect-src 'self'（XSS 后数据外带被拦）、object-src 'none'、
    # base-uri/form-action/frame-ancestors 收口、媒体仅 self+blob（KB 预览）、
    # 图片放行 https:（用户 markdown 外链图为被动内容，rehype-sanitize 已清洗标签面）。
    # v2 前端（无 inline 注入）再收紧 script/style。
    _CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob: https:; "
        "media-src 'self' blob:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-src 'self' blob:; "
        "frame-ancestors 'none'"
    )
    _SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": _CSP,
    }

    @app.middleware("http")
    async def _add_security_headers(request, call_next):
        resp = await call_next(request)
        for k, v in _SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp

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
    # iter 392：SQLite 写锁争用 -> 503 + Retry-After（可重试语义），非争用错误仍 500
    import sqlite3

    from sqlalchemy.exc import OperationalError

    app.add_exception_handler(OperationalError, errors.db_busy_exception_handler)
    app.add_exception_handler(sqlite3.OperationalError, errors.db_busy_exception_handler)
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

"""安全防护中间件（后端插件，复合 middleware+events+storage+routes）。

注册全局 HTTP 中间件：为所有响应加安全头（X-Content-Type-Options / X-Frame-Options /
Referrer-Policy）+ 内存请求计数（GET /stats 查看流量）。计数跨重启持久化（startup 恢复 /
shutdown 落盘）。

实际用途：iter 334 api.middleware 扩展点的真实用例——一行注册即增强全站安全响应头 + 轻量请求
统计（用户 /goal「后端可引入额外安全防护插件」）。

复合 API（验证扩展点协同）：
- api.middleware：注册全请求拦截中间件（加安全头 + 计数；fn 异常由宿主透传不阻断）
- api.events：startup 恢复 / shutdown 落盘累计计数
- api.storage：持久化请求计数（跨重启）
- api.routes：GET /stats 查看请求计数与所加安全头

稳定性：中间件仅加头 + 内存计数（无每请求 I/O，轻量）；计数为闭包变量（per 插件实例）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

PLUGIN_META = {
    "id": "security_guard",
    "name": "安全防护中间件",
    "version": "0.2.0",
    "description": "安全响应头 + 请求统计（middleware+events+storage+routes；stats 管理员）",
    "min_api": "1.17",  # middleware(1.17) + events(1.0) + storage(1.11) + routes(1.0)
}

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def register(api):
    counts: dict[str, int] = {}  # 闭包：method -> count（per 插件实例）

    async def security_mw(request, call_next):
        method = request.method
        counts[method] = counts.get(method, 0) + 1
        response = await call_next(request)
        for key, val in _SECURITY_HEADERS.items():
            response.headers[key] = val
        return response

    api.middleware.register(security_mw)

    def on_startup(payload: dict) -> None:
        saved = api.storage.get("request_counts", {})
        if isinstance(saved, dict):
            counts.update({k: int(v) for k, v in saved.items() if isinstance(v, (int, float))})

    def on_shutdown(payload: dict) -> None:
        api.storage.set("request_counts", dict(counts))

    api.events.on("startup", on_startup)
    api.events.on("shutdown", on_shutdown)

    router = APIRouter()

    @router.get("/stats")
    def stats(admin=Depends(api.deps.require_admin)):
        # iter 375 R18：请求计数是全站路径->频次画像（哪些端点被用、量多大），
        # 对普通认证用户属运营情报（侦察辅助）——收紧为管理员（插件本身即运维工具）
        return {
            "plugin": api.id,
            "request_counts": dict(counts),
            "total_requests": sum(counts.values()),
            "security_headers": dict(_SECURITY_HEADERS),
        }

    api.routes.mount(router)
    api.log.info("security_guard 已加载：安全响应头 + 请求统计中间件")

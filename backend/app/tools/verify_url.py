"""verify_url 工具（plan/02 §6，纯 Python）。

校验链接可达性与适配性。httpx GET，检查 HTTP 200；返回 label: ok|uncertain|invalid。
- ✅ ok：HTTP 200 可达（step 4 仅可达性；内容适配性筛选由 step 5 LLM deep-tutor-resources 提升/下调）
- ⚠️ uncertain：可达但需人工确认（step 5 LLM 判定）
- ❌ invalid：4xx/5xx/超时/被抓取拦截
SSRF 防护（R19）：禁私网/环回/元数据端点。
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from urllib.parse import urlparse

import httpx

from app.agent.context import ExecContext
from app.agent.registry import Tool
from app.tools.base import ToolError, ToolInput, ToolOutput

log = logging.getLogger("deep_tutor.tools.verify")

URL_TIMEOUT = 30.0
# 云元数据端点（R19）
_METADATA_HOSTS = {"169.254.169.254", "169.254.170.2", "metadata.google.internal"}

# 结果缓存（P5 缓存深度优化）：进程内 TTL 缓存，同 URL 在有效期内不重复发 HTTP。
# 成功判定缓存 24h；失败判定（可能瞬时故障）缓存 1h。容量上限 FIFO 淘汰。
_CACHE_OK_TTL = 24 * 3600.0
_CACHE_ERR_TTL = 3600.0
_CACHE_MAX = 4096
_cache: dict[str, tuple[float, dict]] = {}  # url -> (过期时间 monotonic, data)


def _cache_get(url: str) -> dict | None:
    hit = _cache.get(url)
    if hit is None:
        return None
    expires, data = hit
    if time.monotonic() > expires:
        _cache.pop(url, None)
        return None
    return data


def _cache_put(url: str, data: dict, ok: bool) -> None:
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))  # FIFO 淘汰最旧
    ttl = _CACHE_OK_TTL if ok else _CACHE_ERR_TTL
    _cache[url] = (time.monotonic() + ttl, data)


def reset_verify_cache() -> None:
    """测试隔离 / 手动清空。"""
    _cache.clear()


async def verify_url(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    url = inp.args.get("url", "")
    if not url:
        return _err("invalid_url", "空 URL")

    safe, reason = _is_safe_url(url)
    if not safe:
        return ToolOutput(
            tool="verify_url",
            status="ok",
            data={"ok": False, "status": None, "suitable": False, "final_url": url, "label": "invalid"},
            errors=[ToolError(code="ssrf_blocked", message=f"URL 被安全策略拒绝: {reason}")],
        )

    cached = _cache_get(url)
    if cached is not None:
        return ToolOutput(tool="verify_url", status="ok", data={**cached, "cached": True})

    try:
        # 流式抓取仅取状态码与最终 URL，不加载正文（防超大响应 OOM；follow_redirects 仍生效）
        async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(ctx.timeouts.url)) as client:
            async with client.stream("GET", url) as resp:
                status = resp.status_code
                final_url = str(resp.url)
    except httpx.HTTPError as exc:
        log.info("verify_url 抓取失败 %s: %s", url, exc)
        out = _ok_data(url, ok=False, status=None, label="invalid")
        _cache_put(url, out.data, ok=False)
        return out

    if status == 200:
        # step 4：可达即 ok；内容适配性留 step 5 LLM 判定
        out = _ok_data(final_url, ok=True, status=status, label="ok")
    else:
        out = _ok_data(final_url, ok=False, status=status, label="invalid")
    _cache_put(url, out.data, ok=out.data["ok"])
    return out


def _ok_data(url: str, *, ok: bool, status: int | None, label: str) -> ToolOutput:
    return ToolOutput(
        tool="verify_url",
        status="ok",
        data={"ok": ok, "status": status, "suitable": ok, "final_url": url, "label": label},
    )


def _is_safe_url(url: str) -> tuple[bool, str]:
    """SSRF 防护：拒绝私网/环回/元数据/非 http(s)（R19）。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "URL 解析失败"
    if parsed.scheme not in {"http", "https"}:
        return False, f"非 http/https: {parsed.scheme}"
    host = parsed.hostname or ""
    if not host:
        return False, "无 host"
    if host in _METADATA_HOSTS:
        return False, "云元数据端点"
    # 解析主机为 IP 判断私有/环回
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # 域名：解析看是否指向私网（尽力；DNS 可能多变，这里只做基本拦截）
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return True, ""  # 无法解析，放行让 httpx 决定（可达性会失败）
        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            if _is_private_ip(ip):
                return False, f"域名解析到私网 {addr}"
        return True, ""
    if _is_private_ip(ip):
        return False, f"私网/环回 IP {ip}"
    return True, ""


def _is_private_ip(ip: ipaddress._BaseAddress) -> bool:
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _err(code: str, message: str) -> ToolOutput:
    return ToolOutput(tool="verify_url", status="error", errors=[ToolError(code=code, message=message)])


verify_url_tool = Tool(name="verify_url", llm=False, func=verify_url)

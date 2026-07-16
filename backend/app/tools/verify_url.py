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
from urllib.parse import urlparse

import httpx

from app.agent.context import ExecContext
from app.agent.registry import Tool
from app.tools.base import ToolError, ToolInput, ToolOutput

log = logging.getLogger("deep_tutor.tools.verify")

URL_TIMEOUT = 30.0
# 云元数据端点（R19）
_METADATA_HOSTS = {"169.254.169.254", "169.254.170.2", "metadata.google.internal"}


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

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(ctx.timeouts.url)) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        log.info("verify_url 抓取失败 %s: %s", url, exc)
        return _ok_data(url, ok=False, status=None, label="invalid")

    status = resp.status_code
    final_url = str(resp.url)
    if status == 200:
        # step 4：可达即 ok；内容适配性留 step 5 LLM 判定
        label = "ok"
        return _ok_data(final_url, ok=True, status=status, label=label)
    return _ok_data(final_url, ok=False, status=status, label="invalid")


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

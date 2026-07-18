"""verify_url 工具（plan/02 §6，纯 Python）。

校验链接可达性与适配性。httpx GET，检查 HTTP 200；返回 label: ok|uncertain|invalid。
- ✅ ok：HTTP 200 可达（step 4 仅可达性；内容适配性筛选由 step 5 LLM deep-tutor-resources 提升/下调）
- ⚠️ uncertain：可达但需人工确认（登录墙/CAPTCHA/正文过短/robots  disallow；step 5 LLM 判定）
- ❌ invalid：4xx/5xx/超时重试后仍失败/被抓取拦截
SSRF 防护（R19）：禁私网/环回/元数据端点。
合规（plan/09 §一）：遵守 robots.txt，仅校验不存全文。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
import urllib.robotparser
from urllib.parse import urljoin, urlparse

import httpx

from app.agent.context import ExecContext
from app.agent.registry import Tool
from app.tools.base import ToolError, ToolInput, ToolOutput

log = logging.getLogger("deep_tutor.tools.verify")

URL_TIMEOUT = 30.0
_TIMEOUT_RETRIES = 1  # 超时重试 1 次（plan/02 §6 errors）
_MAX_REDIRECTS = 5  # 手动跟随重定向的跳数上限（err.md B4）
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_USER_AGENT = "Deep-tutor/0.1 (learning-plan verifier)"
# 登录墙/CAPTCHA 检测（plan/11 §九：200 但无实内容 -> 内容长度/关键字校验标 ⚠️）
_BODY_PREFIX_BYTES = 16 * 1024  # 仅读响应前缀（不加载全文，plan/09 §一）
_MIN_CONTENT_BYTES = 512  # 正文低于此长度 -> 疑似空壳/拦截页
_LOGIN_WALL_KEYWORDS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "cf-challenge",
    "challenge-platform",
    "sign in to continue",
    "log in to continue",
    "请登录",
    "登录后继续",
    "登录以继续",
    "验证您是真人",
    "安全验证",
)
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
    _robots_cache.clear()


# robots.txt 缓存（plan/09 §一 遵守 robots）：host -> (过期时间, 允许抓取)
_ROBOTS_TTL = 3600.0
_robots_cache: dict[str, tuple[float, bool]] = {}


async def _robots_allows(client: httpx.AsyncClient, url: str) -> bool:
    """按 robots.txt 判定是否允许抓取该 URL；robots 获取/解析失败 -> 放行（可达性自会判定）。"""
    parsed = urlparse(url)
    host = parsed.netloc
    hit = _robots_cache.get(host)
    if hit is not None and time.monotonic() <= hit[0]:
        return hit[1]
    allowed = True
    try:
        resp = await client.get(f"{parsed.scheme}://{host}/robots.txt", timeout=5.0)
        if resp.status_code == 200:
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(resp.text.splitlines())
            allowed = rp.can_fetch(_USER_AGENT, url)
    except httpx.HTTPError:
        pass
    _robots_cache[host] = (time.monotonic() + _ROBOTS_TTL, allowed)
    return allowed


def _analyze_body(prefix: bytes) -> str | None:
    """登录墙/CAPTCHA/空壳检测（plan/11 §九）：命中返回原因，否则 None。"""
    text = prefix.decode("utf-8", errors="ignore")
    if len(text.strip()) < _MIN_CONTENT_BYTES:
        return "content_too_short"
    lowered = text.lower()
    for kw in _LOGIN_WALL_KEYWORDS:
        if kw in lowered:
            return f"login_wall:{kw}"
    return None


class UnsafeRedirectError(Exception):
    """重定向目标未过 SSRF 校验或跳数超限（err.md B4）。"""


async def _fetch(client: httpx.AsyncClient, url: str) -> tuple[int, str, bytes]:
    """抓取（超时重试 _TIMEOUT_RETRIES 次，plan/02 §6）；返回 (status, final_url, 正文前缀)。

    err.md B4：不用 follow_redirects=True（302 到内网/云元数据端点会绕过 SSRF
    校验），改手动逐跳跟随，每跳重新过 _is_safe_url（B7：DNS 解析走 to_thread
    防阻塞事件循环）。
    """
    last_exc: httpx.HTTPError | None = None
    for attempt in range(1 + _TIMEOUT_RETRIES):
        current = url
        try:
            for _ in range(_MAX_REDIRECTS + 1):
                safe, reason = await asyncio.to_thread(_is_safe_url, current)
                if not safe:
                    raise UnsafeRedirectError(f"目标被安全策略拒绝: {reason}")
                # 流式抓取仅取状态码/最终 URL/正文前缀，不加载全文（防超大响应 OOM）
                async with client.stream("GET", current) as resp:
                    status = resp.status_code
                    if status in _REDIRECT_STATUSES and resp.headers.get("location"):
                        current = urljoin(str(resp.url), resp.headers["location"])
                        continue
                    prefix = b""
                    if status == 200:
                        async for chunk in resp.aiter_bytes(8192):
                            prefix += chunk
                            if len(prefix) >= _BODY_PREFIX_BYTES:
                                break
                    return status, str(resp.url), prefix[:_BODY_PREFIX_BYTES]
            raise UnsafeRedirectError(f"重定向次数超上限 {_MAX_REDIRECTS}")
        except httpx.TimeoutException as exc:
            last_exc = exc
            log.info("verify_url 超时（第 %d 次）%s", attempt + 1, url)
    assert last_exc is not None
    raise last_exc


async def verify_url(inp: ToolInput, ctx: ExecContext) -> ToolOutput:
    url = inp.args.get("url", "")
    if not url:
        return _err("invalid_url", "空 URL")

    safe, reason = await asyncio.to_thread(_is_safe_url, url)  # B7：DNS 解析防阻塞事件循环
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
        async with httpx.AsyncClient(
            follow_redirects=False,  # err.md B4：重定向由 _fetch 手动逐跳过 SSRF 校验
            timeout=httpx.Timeout(ctx.timeouts.url),
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            # 遵守 robots.txt（plan/09 §一）；disallow -> ⚠️ 不抓取
            if not await _robots_allows(client, url):
                out = _ok_data(url, ok=False, status=None, label="uncertain")
                out.data["reason"] = "robots_disallowed"
                _cache_put(url, out.data, ok=False)
                return out
            status, final_url, prefix = await _fetch(client, url)
    except UnsafeRedirectError as exc:
        log.info("verify_url 重定向被拦截 %s: %s", url, exc)
        out = _ok_data(url, ok=False, status=None, label="invalid")
        out.data["reason"] = f"ssrf_blocked_redirect: {exc}"
        _cache_put(url, out.data, ok=False)
        return out
    except httpx.HTTPError as exc:
        log.info("verify_url 抓取失败 %s: %s", url, exc)
        out = _ok_data(url, ok=False, status=None, label="invalid")
        _cache_put(url, out.data, ok=False)
        return out

    if status == 200:
        suspicion = _analyze_body(prefix)
        if suspicion is not None:
            # 200 但无实内容（登录墙/CAPTCHA/空壳，plan/11 §九）-> ⚠️ 存疑
            out = _ok_data(final_url, ok=True, status=status, label="uncertain")
            out.data["reason"] = suspicion
        else:
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

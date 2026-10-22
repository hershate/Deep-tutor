"""出站 URL 目标安全判定（SSRF 防护共享实现，iter 394）。

单一事实源：私网/环回/链路本地/云元数据的判定规则原先只存在于
app/tools/verify_url.py（材料抓取路径）。LLM 供应商档案的 base_url 也是
**服务端出站目标**且用户可自选——允许指向 127.0.0.1/内网/云元数据即等于
「借服务器探测内网」的 SSRF 面（api/providers.py 复用本模块）。

已知限制（与 verify_url 同口径，iter 242 已评估接受）：校验与实际连接是两次
独立 DNS 解析，DNS rebinding（TTL=0，二次应答内网地址）理论上可绕过；彻底修法
需传输层 IP 钉扎（httpx 无 resolver 钩子，HTTPS SNI 下需自研 HTTP 栈），与
本地优先威胁模型不成比例。现有缓解：写时校验 + 调用前复检 + 不回显响应正文。

本模块为叶子模块（不 import app.* 业务模块），避免与 api/tools/agent 形成环。
"""

from __future__ import annotations

import ipaddress
import socket
import time
from urllib.parse import urlparse

# 云元数据端点（R19）
METADATA_HOSTS = {"169.254.169.254", "169.254.170.2", "metadata.google.internal"}

# CGNAT（100.64/10）与已废弃 site-local（fec0::/10）在 ipaddress 的
# is_private/is_reserved 下均为 False（部分云内网/校园网使用），显式补拦——
# 这两段无合法公网学习资源，拦截零误伤
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_SITE_LOCAL_NETWORK = ipaddress.ip_network("fec0::/10")

# DNS 判定缓存：写时校验每个档案都要解析一次（未命中时可达 ~1s），
# 同 host 短时间重复提交不应反复解析。TTL 5min / 上限 1024（FIFO）。
_DNS_TTL = 300.0
_DNS_CACHE_MAX = 1024
_dns_cache: dict[str, tuple[float, str | None]] = {}


def is_private_ip(ip: ipaddress._BaseAddress) -> bool:
    """私网/环回/链路本地/保留段判定（含 CGNAT 与 site-local 补拦）。"""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip in _CGNAT_NETWORK
        or ip in _SITE_LOCAL_NETWORK
    )


def host_block_reason(host: str) -> str | None:
    """字面量 host 判定（**不解析 DNS**，纯计算，可安全在事件循环线程内调用）：
    私网/环回/链路本地/元数据 -> 返回拒绝原因；否则 None（域名需解析后判定）。
    """
    if not host:
        return "无 host"
    h = host.strip().lower().strip("[]")
    if h in METADATA_HOSTS:
        return "云元数据端点"
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return None  # 域名
    if is_private_ip(ip):
        return f"私网/环回 IP {ip}"
    return None


def _resolve(host: str) -> list[tuple]:
    """域名解析（iter 418：测试隔离缝）。

    判定逻辑与解析动作分离，使测试可以把解析结果换成确定答案而不必改判定
    语义（tests/conftest.py 的 _deterministic_dns 夹具替换本函数）。真实解析
    失败 -> 空列表（放行），语义见 resolve_block_reason。
    """
    try:
        return socket.getaddrinfo(host, None)
    except OSError:
        return []


def resolve_block_reason(host: str) -> str | None:
    """解析域名后判定（尽力）：解析到私网/环回 -> 返回原因；解析失败 -> None（放行）。"""
    now = time.monotonic()
    cached = _dns_cache.get(host)
    if cached is not None and now - cached[0] < _DNS_TTL:
        return cached[1]
    reason: str | None = None
    for info in _resolve(host):
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        # iter 418：0.0.0.0 / :: 是「未指定地址」——真实成因是 DNS 拦截或黑洞
        # （被墙的域名常被解析到此处），不是内网主机。原措辞说「私网」会把
        # 用户引向错误方向（改 hosts / 查内网），实际该查的是 DNS 与出口。
        if ip.is_unspecified:
            reason = f"域名解析到无效地址 {addr}（DNS 拦截或黑洞，不是真实内网主机）"
            break
        if is_private_ip(ip):
            reason = f"域名解析到私网 {addr}"
            break
    if len(_dns_cache) >= _DNS_CACHE_MAX:
        _dns_cache.pop(next(iter(_dns_cache)), None)  # FIFO
    _dns_cache[host] = (now, reason)
    return reason


def check_outbound_url(url: str, *, resolve: bool = True) -> tuple[bool, str]:
    """出站 URL 目标安全判定 -> (是否放行, 拒绝原因)。

    resolve=False 时只做字面量判定（不触网，可在事件循环线程内同步调用）。
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "URL 解析失败"
    if parsed.scheme not in {"http", "https"}:
        return False, f"非 http/https: {parsed.scheme}"
    host = parsed.hostname or ""
    reason = host_block_reason(host)
    if reason:
        return False, reason
    if resolve:
        reason = resolve_block_reason(host)
        if reason:
            return False, reason
    return True, ""


def display_url(url: str) -> str:
    """日志用安全展示：剔除 userinfo（URL 里可能带口令/token）。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "<无法解析的 URL>"
    if parsed.username or parsed.password:
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://***@{host}{port}{parsed.path}"
    return url


def reset_cache() -> None:
    """清空 DNS 判定缓存（测试隔离）。"""
    _dns_cache.clear()

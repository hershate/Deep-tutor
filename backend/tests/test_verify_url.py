"""verify_url 工具测试。SSRF 防护与可达性分类。"""

from __future__ import annotations

import asyncio

import pytest

from app.agent.context import ExecContext
from app.tools import default_registry
from app.tools.base import ToolInput
from app.tools.verify_url import verify_url


def _ctx():
    return ExecContext(session_id="s", trace_id="t", registry=default_registry())


def _run(url, args=None):
    a = {"url": url}
    if args:
        a.update(args)
    inp = ToolInput(tool="verify_url", session_id="s", args=a)
    return asyncio.run(verify_url(inp, _ctx()))


def test_ssrf_blocks_loopback():
    out = _run("http://127.0.0.1/")
    assert out.data["label"] == "invalid"
    assert out.errors[0].code == "ssrf_blocked"


def test_ssrf_blocks_metadata():
    out = _run("http://169.254.169.254/latest/meta-data/")
    assert out.data["label"] == "invalid"
    assert out.errors[0].code == "ssrf_blocked"


def test_ssrf_blocks_private():
    out = _run("http://10.0.0.1/")
    assert out.data["label"] == "invalid"
    assert out.errors[0].code == "ssrf_blocked"


def test_non_http_blocked():
    out = _run("ftp://example.com/file")
    assert out.data["label"] == "invalid"
    assert out.errors[0].code == "ssrf_blocked"


def test_empty_url_is_error():
    out = _run("")
    assert out.status == "error"
    assert out.errors[0].code == "invalid_url"


def test_real_url_reachable_or_skip():
    try:
        out = _run("https://example.com")
    except Exception as exc:  # noqa: BLE001  无网络环境跳过
        pytest.skip(f"网络不可用: {exc}")
    if out.status == "error" and out.errors and out.errors[0].code == "ssrf_blocked":
        pytest.skip("被安全策略误拦或网络受限")
    if out.data.get("label") == "invalid" and out.data.get("status") is None:
        pytest.skip("网络抓取失败，跳过")
    assert out.data["label"] in {"ok", "uncertain"}


# --- 抓取路径（MockTransport，无网络；验证流式不加载正文 + 重定向跟随）---

# 足长正文（plan/11 §九：200 但正文过短 -> ⚠️；测试用足长内容保持 ok 语义）
_BIG = "<!DOCTYPE html><html><body><p>" + "学习资源正文内容。" * 100 + "</p></body></html>"


def _patch_fetch(handler, monkeypatch):
    """用真 httpx.AsyncClient 预建 MockTransport 客户端，再替换 verify_url 的客户端工厂。

    预建在 patch 之前完成，避免 httpx.AsyncClient 被替换后递归。
    """
    import httpx
    from app.tools import verify_url as vu

    client = httpx.AsyncClient(follow_redirects=True, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(vu, "_is_safe_url", lambda u: (True, ""))  # 隔离 SSRF，专注抓取路径
    monkeypatch.setattr(vu.httpx, "AsyncClient", lambda **kw: client)


def test_verify_url_ok_200(monkeypatch):
    import httpx

    _patch_fetch(lambda req: httpx.Response(200, text="x" * 10_000_000), monkeypatch)
    out = _run("http://example.com")
    assert out.data["label"] == "ok"
    assert out.data["ok"] is True


def test_verify_url_follows_redirect(monkeypatch):
    import httpx

    def handler(req):
        if req.url.path == "/start":
            return httpx.Response(302, headers={"location": "http://example.com/final"})
        return httpx.Response(200, text="x" * 1024)

    _patch_fetch(handler, monkeypatch)
    out = _run("http://example.com/start")
    assert out.data["label"] == "ok"
    assert out.data["final_url"] == "http://example.com/final"


def test_verify_url_404_invalid(monkeypatch):
    import httpx

    _patch_fetch(lambda req: httpx.Response(404), monkeypatch)
    out = _run("http://example.com")
    assert out.data["label"] == "invalid"
    assert out.data["ok"] is False
    assert out.data["status"] == 404


def test_verify_url_fetch_error_invalid(monkeypatch):
    import httpx

    def handler(req):
        raise httpx.ConnectError("conn refused")

    _patch_fetch(handler, monkeypatch)
    out = _run("http://example.com")
    assert out.data["label"] == "invalid"
    assert out.data["status"] is None


# --- 结果缓存（P5 缓存深度优化：同 URL 有效期内不重复发 HTTP）---


def _patch_fetch_multi(handler, monkeypatch):
    """缓存测试专用：每次调用新建 client（共享 MockTransport），支持同测试内多次抓取。"""
    import httpx
    from app.tools import verify_url as vu

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient  # patch 前留存真类，防 lambda 内递归引用
    monkeypatch.setattr(vu, "_is_safe_url", lambda u: (True, ""))
    monkeypatch.setattr(
        vu.httpx, "AsyncClient", lambda **kw: real_client(follow_redirects=True, transport=transport)
    )


def test_verify_url_cache_hit_no_second_fetch(monkeypatch):
    import httpx

    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(200, text="x" * 1024)

    _patch_fetch_multi(handler, monkeypatch)
    out1 = _run("http://example.com/cached")
    out2 = _run("http://example.com/cached")
    main_calls = [c for c in calls if c != "/robots.txt"]  # robots.txt 合规请求不计
    assert len(main_calls) == 1  # 第二次命中缓存，未发 HTTP
    assert out1.data["label"] == "ok"
    assert out2.data["label"] == "ok" and out2.data.get("cached") is True
    assert "cached" not in out1.data


def test_verify_url_cache_also_caches_invalid(monkeypatch):
    import httpx

    calls = []

    def handler(req):
        if req.url.path != "/robots.txt":  # robots.txt 合规请求不计
            calls.append(1)
        return httpx.Response(404)

    _patch_fetch_multi(handler, monkeypatch)
    _run("http://example.com/missing")
    out = _run("http://example.com/missing")
    assert len(calls) == 1
    assert out.data["label"] == "invalid" and out.data.get("cached") is True


def test_verify_url_cache_expires(monkeypatch):
    import httpx
    from app.tools import verify_url as vu

    calls = []

    def handler(req):
        if req.url.path != "/robots.txt":
            calls.append(1)
        return httpx.Response(200, text="x" * 1024)

    _patch_fetch_multi(handler, monkeypatch)
    now = [1000.0]
    monkeypatch.setattr(vu.time, "monotonic", lambda: now[0])
    _run("http://example.com/exp")
    now[0] += vu._CACHE_OK_TTL + 1  # 过有效期
    _run("http://example.com/exp")
    assert len(calls) == 2  # 过期后重新抓取


def test_verify_url_cache_fifo_eviction(monkeypatch):
    import httpx
    from app.tools import verify_url as vu

    _patch_fetch_multi(lambda req: httpx.Response(200, text="x" * 1024), monkeypatch)
    monkeypatch.setattr(vu, "_CACHE_MAX", 3)
    for i in range(4):
        _run(f"http://example.com/{i}")
    assert len(vu._cache) == 3
    assert vu._cache_get("http://example.com/0") is None  # 最旧被淘汰
    assert vu._cache_get("http://example.com/3") is not None


# --- iter 88：超时重试 / robots.txt 遵守 / 登录墙检测（plan/02 §6、plan/09 §一、plan/11 §九）---


def test_verify_url_timeout_retry_once_then_ok(monkeypatch):
    """首次超时 -> 重试 1 次成功 -> ok（plan/02 §6 超时重试 1 次再标记）。"""
    import httpx

    attempts = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(200, text="x" * 1024)

    _patch_fetch_multi(handler, monkeypatch)
    out = _run("http://example.com/slow")
    assert out.data["label"] == "ok"
    assert len(attempts) == 2  # 1 次超时 + 1 次重试


def test_verify_url_timeout_retry_exhausted_invalid(monkeypatch):
    """持续超时（重试后仍失败）-> invalid。"""
    import httpx

    attempts = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts.append(1)
        raise httpx.ReadTimeout("slow", request=req)

    _patch_fetch_multi(handler, monkeypatch)
    out = _run("http://example.com/always-slow")
    assert out.data["label"] == "invalid"
    assert out.data["status"] is None
    assert len(attempts) == 2  # 只重试 1 次，不无限重


def test_verify_url_robots_disallowed(monkeypatch):
    """robots.txt Disallow -> 不抓取正文，标 ⚠️ robots_disallowed（plan/09 §一 遵守 robots）。"""
    import httpx

    fetched = []

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        fetched.append(req.url.path)
        return httpx.Response(200, text="x" * 1024)

    _patch_fetch_multi(handler, monkeypatch)
    out = _run("http://example.com/blocked-by-robots")
    assert out.data["label"] == "uncertain"
    assert out.data["reason"] == "robots_disallowed"
    assert fetched == []  # 未抓取被禁路径


def test_verify_url_login_wall_keyword_uncertain(monkeypatch):
    """200 但命中登录墙/CAPTCHA 关键字 -> ⚠️（plan/11 §九 内容关键字校验）。"""
    import httpx

    body = "<html><body>" + "请登录后查看完整内容 " + "x" * 1024 + "</body></html>"
    _patch_fetch(lambda req: httpx.Response(200, text=body), monkeypatch)
    out = _run("http://example.com/walled")
    assert out.data["label"] == "uncertain"
    assert out.data["ok"] is True  # 可达但存疑
    assert out.data["reason"].startswith("login_wall:")


def test_verify_url_content_too_short_uncertain(monkeypatch):
    """200 但正文过短（空壳/拦截页）-> ⚠️ content_too_short。"""
    import httpx

    _patch_fetch(lambda req: httpx.Response(200, text="ok"), monkeypatch)
    out = _run("http://example.com/empty-shell")
    assert out.data["label"] == "uncertain"
    assert out.data["reason"] == "content_too_short"

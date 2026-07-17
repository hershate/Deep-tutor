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
        return httpx.Response(200, text="ok")

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

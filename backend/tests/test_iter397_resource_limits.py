"""iter 397-B4：资源耗尽面回归（K1 导出限流+体积上限 / K4 URL 总时长 / K7 检索限流）。

- **K1**：`GET /api/kb/export` 此前**无限流、无总字节上限**——单次导出把用户全部
  KB 原件 ZIP 化落进系统临时目录（SpooledTemporaryFile 超 64MB 即滚到磁盘），
  200 篇 × 50MB = 10GB/次；反复调用即可写满系统盘并占满 anyio 工作线程。
- **K4**：服务端出站 URL 抓取此前**只有单次读间隔上限**（httpx.Timeout(30)）与
  字节上限（10MB），**无总时长上限**——攻击者控制的「慢滴」服务器（每 29s 吐
  1 字节）可无限期挂住请求。
- **K7**：`GET /api/kb/documents?q=...` 此前无限流；每次内容检索把最近 50 篇已
  解析文档**全文读进内存**（≤25MB）再逐篇匹配（无索引）。
"""

from __future__ import annotations

import asyncio

import pytest


# ---------------------------------------------------------------------------
# K1：知识库导出的限流与体积上限
# ---------------------------------------------------------------------------


def _mk_text_doc(client, auth_headers, title="导出用例"):
    r = client.post(
        "/api/kb/documents/text", json={"title": title, "text": "导出测试正文"}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_kb_export_rate_limited(client, auth_headers, monkeypatch):
    """导出限流（与其余导出端点同桶 export:{uid}）。"""
    import app.api.kb as kb_mod

    monkeypatch.setattr(kb_mod, "_EXPORT_RATE_PER_MIN", 2)
    _mk_text_doc(client, auth_headers)
    codes = []
    for _ in range(3):
        codes.append(client.get("/api/kb/export", headers=auth_headers).status_code)
    assert 429 in codes, f"导出未限流：{codes}"
    assert codes[-1] == 429


def test_kb_export_rejects_oversized_bundle(client, auth_headers, monkeypatch):
    """体积超限 -> 413（不落临时文件、不占工作线程）。"""
    import app.api.kb as kb_mod

    _mk_text_doc(client, auth_headers)
    monkeypatch.setattr(kb_mod, "_MAX_KB_EXPORT_BYTES", 1)  # 1 字节：必然超限
    r = client.get("/api/kb/export", headers=auth_headers)
    assert r.status_code == 413, r.text
    assert r.json()["error"]["code"] == "payload_too_large"


def test_kb_export_within_limit_succeeds(client, auth_headers):
    """未超限时导出仍正常（限流/上限不得误伤正常路径）。"""
    _mk_text_doc(client, auth_headers)
    r = client.get("/api/kb/export", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"


# ---------------------------------------------------------------------------
# K4：URL 抓取的总时长上限
# ---------------------------------------------------------------------------


def test_url_fetch_has_total_duration_budget(monkeypatch):
    """慢滴服务器可无限挂住请求 -> 必须有总时长预算。"""
    from app.tools import parse_material as pm

    async def _hang(_source, _max_bytes=0):
        # iter 397（T5）：_parse_url_inner 增 max_bytes 形参（体积上限可下调）
        await asyncio.sleep(30)

    monkeypatch.setattr(pm, "_parse_url_inner", _hang)
    monkeypatch.setattr(pm, "URL_TOTAL_TIMEOUT", 0.2)

    async def scenario():
        with pytest.raises(pm._ParseError) as ei:
            await pm._parse_url("https://example.com")
        return ei.value

    exc = asyncio.run(scenario())
    assert exc.code == "upstream_timeout", exc.code


def test_url_fetch_budget_is_configured():
    """总时长上限必须存在且 > 单次读间隔上限（否则退化为无意义）。"""
    from app.tools import parse_material as pm

    assert pm.URL_TOTAL_TIMEOUT > 0
    assert pm.URL_TOTAL_TIMEOUT >= pm.URL_TIMEOUT


# ---------------------------------------------------------------------------
# K7：知识库内容检索限流
# ---------------------------------------------------------------------------


def test_kb_content_search_rate_limited(client, auth_headers, monkeypatch):
    """内容检索（q 命中）无限流 -> 脚本循环可打满磁盘 I/O。"""
    import app.api.kb as kb_mod

    monkeypatch.setattr(kb_mod, "_KB_SEARCH_RATE_PER_MIN", 2)
    _mk_text_doc(client, auth_headers)
    codes = []
    for _ in range(3):
        codes.append(client.get("/api/kb/documents?q=正文", headers=auth_headers).status_code)
    assert 429 in codes, f"检索未限流：{codes}"
    assert codes[-1] == 429


def test_kb_plain_list_not_rate_limited(client, auth_headers, monkeypatch):
    """普通列表（无 q）是廉价 DB 查询，不应被检索限流误伤。"""
    import app.api.kb as kb_mod

    monkeypatch.setattr(kb_mod, "_KB_SEARCH_RATE_PER_MIN", 1)
    _mk_text_doc(client, auth_headers)
    for _ in range(5):
        assert client.get("/api/kb/documents", headers=auth_headers).status_code == 200

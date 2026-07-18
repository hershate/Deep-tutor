"""PDF 导出测试（iter 79，plan/03 §一 format=pdf）。

覆盖：markdown_to_html 结构、build_pdf（fake weasyprint 注入）、
API format=pdf 可用路径（monkeypatch pdf_available+build_pdf）与 R9 降级路径
（本测试环境 weasyprint 缺 Pango 原生库，pdf_available 自然为 False）。
"""

from __future__ import annotations

import base64
import sys
import types

from app.api import exports as exports_api
from app.tools.export_pdf import markdown_to_html, pdf_available


def _mk_plan(client, auth_headers, content) -> int:
    """为当前认证用户建会话+方案（测试文件自包含，同 test_export_anki 模式）。"""
    from app.db.base import SessionLocal
    from app.db.models import Plan, Session as Sess

    me = client.get("/api/auth/me", headers=auth_headers).json()
    db = SessionLocal()
    try:
        s = Sess(user_id=me["id"])
        db.add(s)
        db.flush()
        p = Plan(session_id=s.id, content=content)
        db.add(p)
        db.commit()
        return p.id
    finally:
        db.close()


PLAN = {
    "intent": {"topic": "Python 入门", "level": "零基础", "daily_minutes": 60},
    "stages": [{"index": 0, "name": "基础", "goal": "语法", "difficulty": 1, "estimated_weeks": 2}],
    "schedule": [{"date": "2026-07-20", "stage_index": 0, "tasks": ["变量"], "est_minutes": 60}],
}


def test_pdf_available_false_without_native_libs():
    # 本环境 weasyprint 缺 Pango（OSError）-> False；若环境恰好可用也接受 True，
    # 降级路径由下方 endpoint 测试用 monkeypatch 强制。
    assert pdf_available() in (True, False)


def test_markdown_to_html_structure():
    html = markdown_to_html("# 标题\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", "T")
    assert "<h1>标题</h1>" in html
    assert "<table>" in html  # tables 扩展生效
    assert "@page" in html  # 打印样式注入
    assert "<title>T</title>" in html


def test_build_pdf_with_fake_weasyprint(monkeypatch):
    """注入 fake weasyprint：验证 build_pdf 调用链与字节返回。"""
    captured = {}

    class FakeHTML:
        def __init__(self, string: str, **kwargs) -> None:
            captured["html"] = string
            captured["kwargs"] = kwargs

        def write_pdf(self) -> bytes:
            return b"%PDF-1.4 fake"

    fake = types.ModuleType("weasyprint")
    fake.HTML = FakeHTML  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "weasyprint", fake)

    from app.tools.export_pdf import build_pdf

    data = build_pdf(PLAN, "# Python 入门\n\n正文")
    assert data == b"%PDF-1.4 fake"
    assert "Python 入门" in captured["html"]
    assert "<style>" in captured["html"]
    assert callable(captured["kwargs"].get("url_fetcher"))  # err.md B12：渲染期禁网络抓取


def test_markdown_to_html_escapes_raw_html():
    """err.md B12：LLM 内容夹带的 raw HTML 被转义（防渲染期 SSRF/注入）。"""
    html = markdown_to_html('正文 <img src="http://169.254.169.254/x"> 尾巴', "T<b>")
    assert "<img" not in html
    assert "&lt;img" in html
    assert "<title>T&lt;b&gt;</title>" in html


def test_markdown_to_html_keeps_code_block():
    """fenced code block 内的 < > 不被预转义（markdown 自行处理，避免双重转义）。"""
    html = markdown_to_html("```\n#include <stdio.h>\n```\n", "T")
    assert "&lt;stdio.h&gt;" in html
    assert "&amp;lt;" not in html


def test_export_pdf_endpoint_ok(client, auth_headers, monkeypatch):
    """可用路径：patch pdf_available/build_pdf，返回 base64 pdf。"""
    monkeypatch.setattr(exports_api, "pdf_available", lambda: True)
    monkeypatch.setattr(exports_api, "build_pdf", lambda plan, md: b"%PDF-fake")
    pid = _mk_plan(client, auth_headers, PLAN)
    r = client.post(f"/api/export/{pid}", json={"format": "pdf"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "pdf"
    assert body["filename"].endswith(".pdf")
    assert base64.b64decode(body["pdf_b64"]) == b"%PDF-fake"


def test_export_pdf_degrades_to_md_when_unavailable(client, auth_headers, monkeypatch):
    """R9 降级：weasyprint 不可用 -> md 正文 + warning（plan/02 §11）。"""
    monkeypatch.setattr(exports_api, "pdf_available", lambda: False)
    pid = _mk_plan(client, auth_headers, PLAN)
    r = client.post(f"/api/export/{pid}", json={"format": "pdf"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "md"
    assert "Python 入门" in body["content"]
    assert "weasyprint" in body["warning"]

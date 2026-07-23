"""epub_reader 真实插件装载测试（API 稳定性验证）。

加载仓库 plugins/epub_reader.py 真实源码，验证 parsers+tools+routes 协同：
- parsers：.epub 解析器注册 + parse_material 自动委托 + 上传校验接纳
- tools：epub_reader.chapters 工具（章节标题列表）
- routes：GET /info
端到端经 parse_material 工具委托 + HTTP 上传 + 边界（损坏 zip / 空 epub）。
"""

from __future__ import annotations

import asyncio
import io
import shutil
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

_PLUGINS_SRC = Path(__file__).resolve().parents[2] / "plugins"


def _load_real(name: str, tmp_path: Path):
    src = _PLUGINS_SRC / f"{name}.py"
    assert src.is_file(), f"真实插件源码缺失: {src}"
    shutil.copy2(src, tmp_path / f"{name}.py")
    from app.main import create_app
    from app.plugins.loader import load_plugins

    app = create_app()
    load_plugins(app, tmp_path)
    return app


def _status_map():
    from app.plugins.loader import get_plugin_statuses

    return {s.id: s for s in get_plugin_statuses()}


def _ensure_admin():
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        if not db.query(models.User).filter_by(is_admin=True).first():
            db.add(models.User(name="admin", username="admin", password_hash="x", is_admin=True))
            db.commit()
    finally:
        db.close()


def _make_epub(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("OEBPS/ch1.xhtml", "<html><body><h1>第一章 React 基础</h1><p>组件与 props。</p></body></html>")
        zf.writestr("OEBPS/ch2.xhtml", "<html><body><h1>第二章 Hooks</h1><p>useState 与 useEffect。</p></body></html>")
    return path


def _make_epub_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("OEBPS/ch1.xhtml", "<html><body><h1>第一章</h1><p>内容。</p></body></html>")
    return buf.getvalue()


# --- 装载 ---


def test_loads(tmp_path):
    _load_real("epub_reader", tmp_path)
    s = _status_map()["epub_reader"]
    assert s.state == "loaded"
    assert ".epub" in s.parsers
    assert "epub_reader.chapters" in s.tools
    assert "/api/plugins/epub_reader" in s.routes


# --- parsers：handler + parse_material 委托 ---


def test_handler_parses_epub(tmp_path):
    _load_real("epub_reader", tmp_path)
    from app.plugins import parsers

    entry = next(e for e in parsers.all_entries() if ".epub" in e.extensions)
    epub = _make_epub(tmp_path / "book.epub")
    text, meta = entry.handler(epub, "full")
    assert "第一章" in text and "React" in text and "Hooks" in text
    assert meta["source_type"] == "epub"
    assert meta["pages"] == 2


def test_parse_material_delegates_to_plugin(tmp_path):
    """经 parse_material 工具委托 epub_reader handler（复合 parsers 委托链路）。"""
    _load_real("epub_reader", tmp_path)
    from app.agent.context import ExecContext
    from app.tools.base import ToolInput
    from app.tools.parse_material import parse_material

    epub = _make_epub(tmp_path / "book.epub")
    inp = ToolInput(tool="parse_material", session_id="s", args={"kind": "file", "source": str(epub)})
    ctx = ExecContext(session_id="s", trace_id="t")
    out = asyncio.run(parse_material(inp, ctx))
    assert out.status == "ok", out
    assert "第一章" in out.data["text"]
    assert out.data["meta"]["source_type"] == "epub"
    assert out.data["meta"]["parser"] == "epub_reader"


# --- tools：chapters 工具 ---


def test_tool_chapters(tmp_path):
    _load_real("epub_reader", tmp_path)
    from app.agent.context import ExecContext
    from app.tools import default_registry
    from app.tools.base import ToolInput

    epub = _make_epub(tmp_path / "book.epub")
    reg = default_registry()
    tool = reg.get("epub_reader.chapters")
    inp = ToolInput(tool="epub_reader.chapters", session_id="s", args={"source": str(epub)})
    out = asyncio.run(tool.func(inp, ExecContext(session_id="s", trace_id="t")))
    assert out["count"] == 2
    assert any("第一章" in c for c in out["chapters"])


def test_tool_chapters_missing_source(tmp_path):
    _load_real("epub_reader", tmp_path)
    from app.agent.context import ExecContext
    from app.tools import default_registry
    from app.tools.base import ToolInput

    tool = default_registry().get("epub_reader.chapters")
    inp = ToolInput(tool="epub_reader.chapters", session_id="s", args={})
    out = asyncio.run(tool.func(inp, ExecContext(session_id="s", trace_id="t")))
    assert "error" in out  # 缺 source 优雅返回错误（不抛）


# --- routes ---


def test_info_route(tmp_path, auth_headers):
    app = _load_real("epub_reader", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    r = client.get("/api/plugins/epub_reader/info", headers=auth_headers)
    assert r.status_code == 200
    assert ".epub" in r.json()["formats"]


def test_info_requires_auth(tmp_path):
    app = _load_real("epub_reader", tmp_path)
    assert TestClient(app).get("/api/plugins/epub_reader/info").status_code == 401


# --- 上传校验链路 ---


def test_formats_endpoint_lists_epub(tmp_path, auth_headers):
    app = _load_real("epub_reader", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    r = client.get("/api/materials/formats", headers=auth_headers)
    assert r.status_code == 200
    plugin = [p for p in r.json()["plugin"] if p["plugin"] == "epub_reader"]
    assert len(plugin) == 1
    assert ".epub" in plugin[0]["extensions"]
    assert plugin[0]["label"] == "EPUB 电子书"


def test_upload_accepts_epub(tmp_path, auth_headers):
    """上传 .epub 经校验接纳（parsers 注册扩展名 + magic PK）-> pending（iter 225 暂存）。"""
    app = _load_real("epub_reader", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    r = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("book.epub", io.BytesIO(_make_epub_bytes()), "application/epub+zip")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"


def test_upload_rejects_magic_mismatch(tmp_path, auth_headers):
    """.epub 扩展名但内容非 zip（不以 PK 开头）-> magic 校验 unsupported_format。"""
    app = _load_real("epub_reader", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    r = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("fake.epub", io.BytesIO(b"NOTAZIP"), "application/octet-stream")},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_format"


# --- 边界 ---


def test_edge_bad_zip_is_parse_failed(tmp_path):
    _load_real("epub_reader", tmp_path)
    from app.agent.context import ExecContext
    from app.tools.base import ToolInput
    from app.tools.parse_material import parse_material

    bad = tmp_path / "bad.epub"
    bad.write_bytes(b"not a zip at all")
    inp = ToolInput(tool="parse_material", session_id="s", args={"kind": "file", "source": str(bad)})
    out = asyncio.run(parse_material(inp, ExecContext(session_id="s", trace_id="t")))
    assert out.status == "error"
    assert out.errors[0].code == "parse_failed"  # 插件 handler 抛 -> 宿主转 parse_failed


def test_edge_empty_epub_is_empty_content(tmp_path):
    """无 XHTML 章节的 epub -> 空文本 -> empty_content（不静默注入空）。"""
    _load_real("epub_reader", tmp_path)
    from app.agent.context import ExecContext
    from app.tools.base import ToolInput
    from app.tools.parse_material import parse_material

    empty = tmp_path / "empty.epub"
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")  # 合法 zip 但无 xhtml
    inp = ToolInput(tool="parse_material", session_id="s", args={"kind": "file", "source": str(empty)})
    out = asyncio.run(parse_material(inp, ExecContext(session_id="s", trace_id="t")))
    assert out.status == "error"
    assert out.errors[0].code == "empty_content"

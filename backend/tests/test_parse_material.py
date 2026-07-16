"""parse_material 工具单元测试（快速用例，不依赖 marker）。

覆盖：text / txt / md / docx / pptx / 不支持格式 / 空内容 / 分块 / 截断 / 注册表。
marker PDF 集成测试见 test_parse_material_marker.py。
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from app.agent.context import ExecContext
from app.tools import default_registry
from app.tools.base import ToolInput
from app.tools.parse_material import CHUNK_SIZE, MAX_PARSED_CHARS, parse_material


def _ctx() -> ExecContext:
    return ExecContext(session_id="sess_test", trace_id="trace_test")


def _inp(args: dict) -> ToolInput:
    return ToolInput(tool="parse_material", session_id="sess_test", args=args)


def _run(args: dict):
    return asyncio.run(parse_material(_inp(args), _ctx()))


def _tmp(suffix: str, content: str | bytes = "") -> str:
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb")
    f.write(content.encode("utf-8") if isinstance(content, str) else content)
    f.close()
    return f.name


# --- text ---


def test_text_kind():
    out = _run({"kind": "text", "source": "hello world"})
    assert out.status == "ok"
    assert out.data["text"] == "hello world"
    assert out.data["meta"]["source_type"] == "text"
    assert out.data["meta"]["chars"] == 11
    assert out.data["chunks"] == ["hello world"]


def test_empty_text_is_error():
    out = _run({"kind": "text", "source": "   "})
    assert out.status == "error"
    assert out.errors[0].code == "empty_content"


def test_unsupported_kind():
    out = _run({"kind": "image", "source": "x"})
    assert out.status == "error"
    assert out.errors[0].code == "unsupported_format"


# --- file: txt / md ---


def test_txt_file():
    path = _tmp(".txt", "plain text content")
    out = _run({"kind": "file", "source": path})
    assert out.status == "ok"
    assert out.data["text"] == "plain text content"
    assert out.data["meta"]["source_type"] == "txt"


def test_md_file():
    path = _tmp(".md", "# Title\n\nsome **markdown** text")
    out = _run({"kind": "file", "source": path})
    assert out.status == "ok"
    assert "markdown" in out.data["text"]
    assert out.data["meta"]["source_type"] == "md"


def test_empty_file_is_error():
    path = _tmp(".txt", "")
    out = _run({"kind": "file", "source": path})
    assert out.status == "error"
    assert out.errors[0].code == "empty_content"


def test_unsupported_extension():
    path = _tmp(".xlsx", "fake")
    out = _run({"kind": "file", "source": path})
    assert out.status == "error"
    assert out.errors[0].code == "unsupported_format"


def test_missing_file():
    out = _run({"kind": "file", "source": "/no/such/file.pdf"})
    assert out.status == "error"
    assert out.errors[0].code == "unsupported_format"


# --- file: docx / pptx ---


def test_docx_file():
    from docx import Document

    path = _tmp(".docx")
    doc = Document()
    doc.add_paragraph("docx paragraph content")
    doc.save(path)
    out = _run({"kind": "file", "source": path})
    assert out.status == "ok"
    assert "docx paragraph content" in out.data["text"]
    assert out.data["meta"]["source_type"] == "docx"


def test_pptx_file():
    from pptx import Presentation

    path = _tmp(".pptx")
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
    tb = slide.shapes.add_textbox(0, 0, 300, 100)
    tb.text_frame.text = "ppt slide content"
    prs.save(path)
    out = _run({"kind": "file", "source": path})
    assert out.status == "ok"
    assert "ppt slide content" in out.data["text"]
    assert out.data["meta"]["source_type"] == "pptx"


# --- 分块 / 截断 ---


def test_chunking_splits_long_text():
    text = "a" * (CHUNK_SIZE * 2 + 100)
    out = _run({"kind": "text", "source": text})
    assert out.status == "ok"
    assert len(out.data["chunks"]) == 3
    assert len(out.data["chunks"][0]) == CHUNK_SIZE
    assert len(out.data["chunks"][2]) == 100


def test_truncation_over_max():
    text = "b" * (MAX_PARSED_CHARS + 1000)
    out = _run({"kind": "text", "source": text})
    assert out.status == "ok"
    assert out.data["meta"]["truncated"] is True
    assert out.data["meta"]["chars"] == MAX_PARSED_CHARS


# --- 注册表 ---


def test_default_registry_has_parse_material():
    reg = default_registry()
    assert reg.has("parse_material")
    assert reg.get("parse_material").llm is False

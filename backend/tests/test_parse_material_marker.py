"""parse_material 的 Marker PDF 集成测试。

需 marker_single 在 PATH（pip install marker-pdf）。未安装时跳过。
较慢（~5s，含 OCR），故独立文件。
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from app.agent.context import ExecContext
from app.tools.base import ToolInput
from app.tools.parse_material import parse_material

FIXTURE = Path(__file__).parent / "fixtures" / "sample.pdf"

pytestmark = pytest.mark.skipif(
    shutil.which("marker_single") is None or not FIXTURE.exists(),
    reason="marker_single 未安装或 fixture 缺失",
)


def _run():
    inp = ToolInput(
        tool="parse_material",
        session_id="sess_test",
        args={"kind": "file", "source": str(FIXTURE)},
    )
    ctx = ExecContext(session_id="sess_test", trace_id="trace_test")
    return asyncio.run(parse_material(inp, ctx))


def test_marker_pdf_parses_text():
    out = _run()
    assert out.status == "ok", out.errors
    assert "Hello Marker" in out.data["text"]
    assert out.data["meta"]["source_type"] == "pdf"
    assert out.data["meta"]["parser"] == "marker"
    assert out.data["meta"]["pages"] >= 1
    assert out.data["chunks"]

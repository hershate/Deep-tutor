"""sse_utils 叶子 helper 单元测试（iter 345）。

锁定 SSE 事件帧格式（供 plan-gen 与新对话/Agent SSE 共用）。
"""

from __future__ import annotations

from app.api.sse_utils import (
    HEARTBEAT_SECONDS,
    KEEPALIVE_FRAME,
    SSE_HEADERS,
    format_sse_frame,
)


def test_sse_headers_has_anti_buffering():
    assert SSE_HEADERS["Cache-Control"] == "no-cache"
    assert SSE_HEADERS["X-Accel-Buffering"] == "no"  # nginx 禁缓冲
    assert SSE_HEADERS["Connection"] == "keep-alive"


def test_format_frame_basic():
    frame = format_sse_frame("tool_start", {"tool": "x"})
    assert frame == 'event: tool_start\ndata: {"tool": "x"}\n\n'


def test_format_frame_with_event_id():
    frame = format_sse_frame("done", {"plan_id": 1}, event_id=42)
    assert frame.startswith("id: 42\n")
    assert "event: done\n" in frame
    assert 'data: {"plan_id": 1}\n\n' in frame


def test_format_frame_non_ascii_not_escaped():
    """ensure_ascii=False：中文原样输出（不转 \\uXXXX），前端可读。"""
    frame = format_sse_frame("msg", {"text": "你好"})
    assert "你好" in frame
    assert "\\u" not in frame


def test_keepalive_and_heartbeat_constants():
    assert KEEPALIVE_FRAME == ": keepalive\n\n"
    assert HEARTBEAT_SECONDS == 15.0

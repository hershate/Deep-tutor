"""SSE 公共叶子 helper（iter 345 抽自 plans.py，供 plan-gen 与新对话/Agent SSE 共用）。

只含**无状态叶子**：响应头、事件帧格式化、心跳常量。带状态的流缓冲（如 plans.py 的
``_SessionStream``、后续 Agent 对话的 ``_RunStream``）与配额/占用策略留在各自模块——
``_SessionStream`` 携带 plan-gen 运行态（budget/current_tool/executed 等）+ 配额耦合，
不属通用工具，不可外提（见 Plan 校验结论）。

抽取目的：消除 ``StreamingResponse(text/event-stream, _SSE_HEADERS)`` 样板重复，
让新对话 SSE（``POST /api/threads/{id}/runs``）与既有生成 SSE 共用同一套叶子 helper。
"""

from __future__ import annotations

import json

# SSE 响应头：no-cache + keep-alive + nginx 禁缓冲（plan/09 §十九）
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

# 心跳间隔：长 LLM 调用期间防 nginx/代理/浏览器空闲断连（plan/09 §十九）。
HEARTBEAT_SECONDS = 15.0

# SSE 注释行（`: keepalive`）——不触发前端事件，仅保活。
KEEPALIVE_FRAME = ": keepalive\n\n"


def format_sse_frame(event: str, data: dict, event_id: int | None = None) -> str:
    """格式化一条 SSE 事件帧。

    event_id 非空时附 ``id:`` 行（Last-Event-ID 续接游标，单调递增——plan/10 §十二）。
    """
    parts: list[str] = []
    if event_id is not None:
        parts.append(f"id: {event_id}")
    parts.append(f"event: {event}")
    parts.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    return "\n".join(parts) + "\n\n"

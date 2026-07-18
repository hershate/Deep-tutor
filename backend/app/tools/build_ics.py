"""ics 日历导出构建器（P5，plan/README 输出形式 4）。

纯 Python（stdlib），从方案 content.schedule（schedule_plan 工具产出：
[{date, stage_index, tasks, est_minutes}]）生成 RFC 5545 iCalendar 文本：
每个学习日一个**全天事件**，描述含当日任务与预计时长。

稳定性约定：
- 非法日期条目跳过并计数；全部非法/无 schedule -> ValueError（API 层转 no_schedule）；
- 文本按 RFC 转义（\\ ; , 换行）；行按 75 字节折叠；CRLF 行尾。
"""

from __future__ import annotations

from datetime import datetime, timezone


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def _fold(line: str) -> list[str]:
    """按 75 字节（UTF-8）折叠长行，续行以空格开头（前导空格计入 75 字节，RFC 5545 §3.1）。"""
    if len(line.encode("utf-8")) <= 75:
        return [line]
    parts: list[str] = []
    buf = ""
    buf_len = 0
    for ch in line:
        ch_len = len(ch.encode("utf-8"))
        if buf_len + ch_len > 75:
            parts.append(buf)
            buf = " " + ch
            buf_len = 1 + ch_len
        else:
            buf += ch
            buf_len += ch_len
    parts.append(buf)
    return parts


def build_ics(plan_id: int, content: dict, now: datetime | None = None) -> tuple[str, int, int]:
    """生成 ics 文本。返回 (ics_text, 事件数, 跳过条数)。无有效日程抛 ValueError。"""
    schedule = content.get("schedule") or []
    stages = content.get("stages") or []
    stage_names: dict[int, str] = {}
    for i, st in enumerate(stages):
        if isinstance(st, dict):
            idx = st.get("index", i)
            try:
                stage_names[int(idx)] = str(st.get("name", ""))
            except (TypeError, ValueError):
                continue

    dtstamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    title = str(content.get("title") or content.get("topic") or "学习计划")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Deep-tutor//Study Schedule//CN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:" + _escape(f"Deep-tutor：{title}"),
    ]
    events = 0
    skipped = 0
    for i, item in enumerate(schedule):
        if not isinstance(item, dict):
            skipped += 1
            continue
        try:
            day = datetime.fromisoformat(str(item.get("date", "")).strip()).date()
        except ValueError:
            skipped += 1
            continue
        stage_idx = item.get("stage_index", 0)
        try:
            stage_idx = int(stage_idx)
        except (TypeError, ValueError):
            stage_idx = 0
        stage_name = stage_names.get(stage_idx, "")
        tasks = [str(t) for t in (item.get("tasks") or [])]
        est = item.get("est_minutes", 0) or 0

        summary = f"[{title}] " + (f"阶段{stage_idx + 1} {stage_name}" if stage_name else f"学习日 {i + 1}")
        desc_parts = [f"阶段：{stage_name}"] if stage_name else []
        desc_parts += [f"· {t}" for t in tasks]
        if est:
            desc_parts.append(f"预计时长：{est} 分钟")
        desc = "\n".join(desc_parts) or "学习日"

        event = [
            "BEGIN:VEVENT",
            f"UID:dt-{plan_id}-{i}-{day.isoformat()}@deep-tutor",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
            "SUMMARY:" + _escape(summary),
            "DESCRIPTION:" + _escape(desc),
            "END:VEVENT",
        ]
        for ln in event:
            lines.extend(_fold(ln))
        events += 1

    if events == 0:
        raise ValueError("方案无有效日程（schedule 为空或日期全部非法）")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n", events, skipped

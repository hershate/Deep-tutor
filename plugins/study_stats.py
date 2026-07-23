"""学习活动统计仪表盘（后端插件，复合 events+storage+scheduler+routes）。

监听学习活动事件，跨方案聚合统计（今日/本周/累计学习分钟、测验作答、进度更新、连续学习天数），
周期性重算连续天数保证最终一致，经 GET /dashboard 暴露——一个轻量跨方案学习仪表盘。

实际用途：多方案用户看「我全局学了多久、做了多少题、坚持几天」——宿主的 /progress/stats 按方案
分，本插件跨方案聚合全部学习活动（打卡 + 测验 + 进度），一个端点看全貌。

复合 API（验证四扩展点协同）：
- api.events：监听 learning_log_added / quiz_submitted / progress_updated 实时累计
- api.storage：持久化每日分钟 / 计数 / 连续天数（跨重启）
- api.scheduler：周期重算连续天数（兜底最终一致；register 期无运行循环则跳过）
- api.routes：GET /dashboard 返回聚合统计

稳定性要点：scheduler.every 须运行事件循环——register 期（如测试同步 load）无循环时 try/except
跳过（不影响 events/storage/routes 核心功能）；真实启动在 lifespan 内 load，有循环，周期任务生效。
"""

from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter, Depends

PLUGIN_META = {
    "id": "study_stats",
    "name": "学习活动统计仪表盘",
    "version": "0.1.0",
    "description": "跨方案学习活动聚合统计（events+storage+scheduler+routes）",
    "min_api": "1.11",  # events(1.0) + storage(1.11) + scheduler(1.10) + routes(1.0)
}


def _today() -> str:
    return _dt.date.today().isoformat()


def _streak(day_minutes: dict) -> int:
    """从「日期->分钟」字典算连续学习天数（从最新活跃日回溯连续日期）。"""
    if not day_minutes:
        return 0
    try:
        dates = sorted(_dt.date.fromisoformat(d) for d in day_minutes if d)
    except ValueError:
        return 0
    if not dates:
        return 0
    streak = 1
    for i in range(len(dates) - 1, 0, -1):
        if (dates[i] - dates[i - 1]).days == 1:
            streak += 1
        else:
            break
    return streak


def register(api):
    # --- events：实时累计学习活动到 storage ---
    def on_learning_log(payload: dict) -> None:
        raw_date = payload.get("date")
        if raw_date:
            try:
                _dt.date.fromisoformat(raw_date)  # 校验合法 ISO 日期
                day = raw_date
            except (ValueError, TypeError):
                day = _today()  # 非法 date -> fallback today（不污染 days 为非法 key）
        else:
            day = _today()
        try:
            minutes = int(payload.get("minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        days = api.storage.get("days", {}) or {}
        days[day] = days.get(day, 0) + minutes
        api.storage.set("days", days)
        api.storage.set("streak", _streak(days))

    def on_quiz(payload: dict) -> None:
        api.storage.set("quiz_count", api.storage.get("quiz_count", 0) + 1)
        if payload.get("correct"):
            api.storage.set("quiz_correct", api.storage.get("quiz_correct", 0) + 1)

    def on_progress(payload: dict) -> None:
        api.storage.set("progress_updates", api.storage.get("progress_updates", 0) + 1)

    api.events.on("learning_log_added", on_learning_log)
    api.events.on("quiz_submitted", on_quiz)
    api.events.on("progress_updated", on_progress)

    # --- scheduler：周期重算连续天数（兜底最终一致）---
    def recompute_streak() -> None:
        days = api.storage.get("days", {}) or {}
        api.storage.set("streak", _streak(days))
        api.storage.set("last_streak_compute", _dt.datetime.now().isoformat(timespec="seconds"))

    try:
        # 真实启动（lifespan 内 load）有运行循环 -> 周期任务生效；run_immediately 启动即重算一次
        api.scheduler.every(6 * 3600, recompute_streak, run_immediately=True)
    except RuntimeError:  # noqa: BLE001  register 期无运行循环（测试同步 load 等）-> 跳过
        api.log.info("study_stats: 周期重算任务跳过（无运行事件循环），真实启动时生效")

    # --- routes：GET /dashboard 聚合统计（默认带宿主认证）---
    router = APIRouter()

    @router.get("/dashboard")
    def dashboard(user=Depends(api.deps.get_current_user)):
        days = api.storage.get("days", {}) or {}
        today = _today()
        week_ago = (_dt.date.today() - _dt.timedelta(days=7)).isoformat()
        week_minutes = sum(m for d, m in days.items() if isinstance(m, (int, float)) and d >= week_ago)
        return {
            "plugin": api.id,
            "today_minutes": days.get(today, 0),
            "week_minutes": week_minutes,
            "total_minutes": sum(m for m in days.values() if isinstance(m, (int, float))),
            "active_days": len(days),
            "streak": api.storage.get("streak", 0),
            "quiz_count": api.storage.get("quiz_count", 0),
            "quiz_correct": api.storage.get("quiz_correct", 0),
            "progress_updates": api.storage.get("progress_updates", 0),
            "last_streak_compute": api.storage.get("last_streak_compute"),
        }

    api.routes.mount(router)
    api.log.info("study_stats 已加载：学习活动统计仪表盘")

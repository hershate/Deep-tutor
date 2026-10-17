"""学习活动统计仪表盘（后端插件，复合 events+storage+scheduler+routes）。

监听学习活动事件，跨方案聚合统计（今日/本周/累计学习分钟、测验作答、进度更新、连续学习天数），
周期性重算连续天数保证最终一致，经 GET /dashboard 暴露——一个轻量跨方案学习仪表盘。

实际用途：多方案用户看「我全局学了多久、做了多少题、坚持几天」——宿主的 /progress/stats 按方案
分，本插件跨方案聚合全部学习活动（打卡 + 测验 + 进度），一个端点看全貌。

iter 375 R18（多用户隔离修正）：storage 是每插件全局 KV——此前把**所有用户**的学习分钟/答题数
聚成一份，任何认证用户经 /dashboard 都能看到他人学习数据（跨用户隐私泄露）。三类事件 payload
均带 user_id（quizzes.py / progress.py 发射点），自此按 `days:{uid}` 等键分用户记账，
dashboard 只读本人。旧全局键（days/quiz_count/…）不再读取，遗留部署数据弃置（v1 演示插件
不做迁移）。

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
    "version": "0.2.0",
    "description": "跨方案学习活动聚合统计（events+storage+scheduler+routes；按用户隔离）",
    "min_api": "1.11",  # events(1.0) + storage(1.11) + scheduler(1.10) + routes(1.0)
}


def _today() -> str:
    return _dt.date.today().isoformat()


def _uid(payload_or_user_id) -> str:
    """事件 payload / 路由 user 对象 -> 存储键用的用户标识（缺失记 0，不与真实 uid 混淆）。"""
    v = payload_or_user_id.get("user_id") if isinstance(payload_or_user_id, dict) else payload_or_user_id
    return str(v or 0)


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
    # --- events：实时累计学习活动到 storage（按用户分键，iter 375）---
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
        uid = _uid(payload)
        days = api.storage.get(f"days:{uid}", {}) or {}
        days[day] = days.get(day, 0) + minutes
        api.storage.set(f"days:{uid}", days)
        api.storage.set(f"streak:{uid}", _streak(days))

    def on_quiz(payload: dict) -> None:
        uid = _uid(payload)
        api.storage.set(f"quiz_count:{uid}", api.storage.get(f"quiz_count:{uid}", 0) + 1)
        if payload.get("correct"):
            api.storage.set(f"quiz_correct:{uid}", api.storage.get(f"quiz_correct:{uid}", 0) + 1)

    def on_progress(payload: dict) -> None:
        uid = _uid(payload)
        api.storage.set(f"progress_updates:{uid}", api.storage.get(f"progress_updates:{uid}", 0) + 1)

    api.events.on("learning_log_added", on_learning_log)
    api.events.on("quiz_submitted", on_quiz)
    api.events.on("progress_updated", on_progress)

    # --- scheduler：周期重算连续天数（兜底最终一致；逐用户重算）---
    def recompute_streak() -> None:
        for key in list(api.storage.keys()):
            if not key.startswith("days:"):
                continue
            uid = key[len("days:") :]
            api.storage.set(f"streak:{uid}", _streak(api.storage.get(key, {}) or {}))
        api.storage.set("last_streak_compute", _dt.datetime.now().isoformat(timespec="seconds"))

    try:
        # 真实启动（lifespan 内 load）有运行循环 -> 周期任务生效；run_immediately 启动即重算一次
        api.scheduler.every(6 * 3600, recompute_streak, run_immediately=True)
    except RuntimeError:  # noqa: BLE001  register 期无运行事件循环（测试同步 load 等）-> 跳过
        api.log.info("study_stats: 周期重算任务跳过（无运行事件循环），真实启动时生效")

    # --- routes：GET /dashboard 聚合统计（默认带宿主认证；只读本人数据，iter 375）---
    router = APIRouter()

    @router.get("/dashboard")
    def dashboard(user=Depends(api.deps.get_current_user)):
        uid = _uid(user.id)
        days = api.storage.get(f"days:{uid}", {}) or {}
        today = _today()
        week_ago = (_dt.date.today() - _dt.timedelta(days=7)).isoformat()
        week_minutes = sum(m for d, m in days.items() if isinstance(m, (int, float)) and d >= week_ago)
        return {
            "plugin": api.id,
            "today_minutes": days.get(today, 0),
            "week_minutes": week_minutes,
            "total_minutes": sum(m for m in days.values() if isinstance(m, (int, float))),
            "active_days": len(days),
            "streak": api.storage.get(f"streak:{uid}", 0),
            "quiz_count": api.storage.get(f"quiz_count:{uid}", 0),
            "quiz_correct": api.storage.get(f"quiz_correct:{uid}", 0),
            "progress_updates": api.storage.get(f"progress_updates:{uid}", 0),
            "last_streak_compute": api.storage.get("last_streak_compute"),
        }

    api.routes.mount(router)
    api.log.info("study_stats 已加载：学习活动统计仪表盘（按用户隔离）")

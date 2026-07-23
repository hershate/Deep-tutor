"""study_stats 真实插件装载测试（API 稳定性验证）。

加载仓库 plugins/study_stats.py 真实源码，验证 events+storage+scheduler+routes 四扩展点协同：
- events：三类学习活动事件实时累计
- storage：持久化（跨重启）
- scheduler：周期重算连续天数（asyncio.run 内 load 提供运行循环；同步 _load 无循环时 try/except 跳过）
- routes：GET /dashboard 聚合
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import date, timedelta
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


# --- 装载（同步 _load 无运行循环，scheduler 须 try/except 跳过不致 failed）---


def test_loads(tmp_path):
    _load_real("study_stats", tmp_path)
    s = _status_map()["study_stats"]
    assert s.state == "loaded"  # scheduler 跳过不影响装载
    assert {"learning_log_added", "quiz_submitted", "progress_updated"} <= set(s.events)
    assert "/api/plugins/study_stats" in s.routes


# --- events + storage + routes 协同 ---


def test_events_accumulate_to_dashboard(tmp_path, auth_headers):
    app = _load_real("study_stats", tmp_path)
    from app.plugins import bus

    today = date.today().isoformat()
    bus.emit("learning_log_added", {"date": today, "minutes": 45, "user_id": 1, "plan_id": 1})
    bus.emit("quiz_submitted", {"correct": True, "user_id": 1, "plan_id": 1})
    bus.emit("quiz_submitted", {"correct": False, "user_id": 1, "plan_id": 1})
    bus.emit("progress_updated", {"user_id": 1, "plan_id": 1, "status": "done"})
    _ensure_admin()
    client = TestClient(app)
    r = client.get("/api/plugins/study_stats/dashboard", headers=auth_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["today_minutes"] == 45
    assert d["total_minutes"] == 45
    assert d["active_days"] == 1
    assert d["quiz_count"] == 2 and d["quiz_correct"] == 1
    assert d["progress_updates"] == 1
    assert d["streak"] == 1


def test_streak_consecutive_days(tmp_path, auth_headers):
    app = _load_real("study_stats", tmp_path)
    from app.plugins import bus

    today = date.today()
    bus.emit("learning_log_added", {"date": today.isoformat(), "minutes": 30})
    bus.emit("learning_log_added", {"date": (today - timedelta(days=1)).isoformat(), "minutes": 20})
    _ensure_admin()
    client = TestClient(app)
    r = client.get("/api/plugins/study_stats/dashboard", headers=auth_headers)
    assert r.json()["streak"] == 2  # 今昨连续


def test_streak_breaks_on_gap(tmp_path, auth_headers):
    app = _load_real("study_stats", tmp_path)
    from app.plugins import bus

    today = date.today()
    bus.emit("learning_log_added", {"date": today.isoformat(), "minutes": 10})
    bus.emit("learning_log_added", {"date": (today - timedelta(days=3)).isoformat(), "minutes": 10})  # 隔 3 天
    _ensure_admin()
    client = TestClient(app)
    assert client.get("/api/plugins/study_stats/dashboard", headers=auth_headers).json()["streak"] == 1


def test_week_minutes_window(tmp_path, auth_headers):
    app = _load_real("study_stats", tmp_path)
    from app.plugins import bus

    today = date.today()
    bus.emit("learning_log_added", {"date": today.isoformat(), "minutes": 40})
    bus.emit("learning_log_added", {"date": (today - timedelta(days=3)).isoformat(), "minutes": 60})  # 本周内
    bus.emit("learning_log_added", {"date": (today - timedelta(days=10)).isoformat(), "minutes": 100})  # 本周外
    _ensure_admin()
    client = TestClient(app)
    d = client.get("/api/plugins/study_stats/dashboard", headers=auth_headers).json()
    assert d["week_minutes"] == 100  # 40 + 60，10 天前的 100 不计
    assert d["total_minutes"] == 200


# --- 持久化（跨重启）---


def test_persists_across_reload(tmp_path, auth_headers):
    from app.plugins import bus
    from app.plugins.loader import reset_plugins

    _load_real("study_stats", tmp_path)
    bus.emit("learning_log_added", {"date": "2026-07-23", "minutes": 30})
    reset_plugins()  # 清运行时（storage 文件保留）

    from app.main import create_app
    from app.plugins.loader import load_plugins

    app2 = create_app()
    load_plugins(app2, tmp_path)
    _ensure_admin()
    client = TestClient(app2)
    r = client.get("/api/plugins/study_stats/dashboard", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["total_minutes"] == 30  # 跨"重启"读到持久化值


# --- scheduler：asyncio.run 内 load 提供运行循环，周期任务 run_immediately 执行 ---


def test_scheduler_recompute_runs(tmp_path):
    """scheduler.every 须运行循环；在 asyncio.run 内 load -> run_immediately 跑 recompute 写 storage。"""
    from app.core.settings import settings

    async def main():
        shutil.copy2(_PLUGINS_SRC / "study_stats.py", tmp_path / "study_stats.py")
        from app.main import create_app
        from app.plugins.loader import load_plugins

        app = create_app()
        load_plugins(app, tmp_path)  # 运行循环内 -> scheduler.every 成功注册
        await asyncio.sleep(0.2)  # 让 run_immediately 的 recompute 执行

    asyncio.run(main())
    storage_file = Path(settings.data_dir) / "plugins" / "study_stats" / "storage.json"
    assert storage_file.is_file(), "scheduler 未写 storage（周期任务可能未跑）"
    data = json.loads(storage_file.read_text(encoding="utf-8"))
    assert data.get("last_streak_compute"), "scheduler run_immediately 未执行 recompute_streak"


# --- 边界 ---


def test_dashboard_requires_auth(tmp_path):
    app = _load_real("study_stats", tmp_path)
    client = TestClient(app)
    assert client.get("/api/plugins/study_stats/dashboard").status_code == 401


def test_empty_dashboard(tmp_path, auth_headers):
    """无任何活动 -> dashboard 返回全零，不崩。"""
    app = _load_real("study_stats", tmp_path)
    _ensure_admin()
    client = TestClient(app)
    d = client.get("/api/plugins/study_stats/dashboard", headers=auth_headers).json()
    assert d["total_minutes"] == 0 and d["streak"] == 0 and d["quiz_count"] == 0


def test_invalid_date_falls_back_today(tmp_path, auth_headers):
    """非法 date 不崩（fallback today，不污染 days 为非法 key）。"""
    app = _load_real("study_stats", tmp_path)
    from app.plugins import bus

    bus.emit("learning_log_added", {"date": "not-a-date", "minutes": 50})  # 非法 date
    _ensure_admin()
    client = TestClient(app)
    d = client.get("/api/plugins/study_stats/dashboard", headers=auth_headers).json()
    assert d["today_minutes"] == 50  # 非法 date fallback today -> 计入今日
    assert d["streak"] == 1  # today 合法 -> streak 不因非法 date 崩

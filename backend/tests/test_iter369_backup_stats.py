"""iter 369 第三轮审计 B14：备份/恢复与统计性能回归。

覆盖：
- 导入互斥（restore 持 SQLite 写锁期间第二个导入立即 409，不再配额竞态/写锁 500）
- /stats/summary SQL 聚合口径与旧 Python 求和一致（总量/近 7 日/streak/条数）
- /stats/by-plan 主题缓存（重复调用不再逐 plan 读溢出 JSON）
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from app.db import models
from app.db.base import SessionLocal


def _mk_admin():
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="admin", username="admin", password_hash="", is_admin=True))
        db.commit()
    finally:
        db.close()


def test_import_mutex_conflict(client, auth_headers):
    """互斥锁被占（等价于另一个导入正在进行）-> 409 立即拒绝。"""
    from app.api.backup import _RESTORE_LOCK

    _mk_admin()
    assert _RESTORE_LOCK.acquire(blocking=False)
    try:
        files = {"file": ("b.json", json.dumps({}).encode("utf-8"), "application/json")}
        r = client.post("/api/backup/import", headers=auth_headers, files=files)
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "conflict"
    finally:
        _RESTORE_LOCK.release()
    # 释放后可正常导入（合法空备份路径）
    files = {
        "file": (
            "b.json",
            json.dumps({"format": "deep-tutor-backup", "version": 1, "sessions": []}).encode("utf-8"),
            "application/json",
        )
    }
    r = client.post("/api/backup/import", headers=auth_headers, files=files)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_stats_summary_aggregation_matches_python_sum(client, auth_headers):
    """SQL 聚合改写后口径不变：总量/近 7 日/总条数/streak 与手工求和一致。"""
    _mk_admin()
    db = SessionLocal()
    try:
        u = db.query(models.User).filter_by(username="admin").one()
        sess = models.Session(user_id=u.id, title="stats")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"intent": {"topic": "t"}})
        db.add(plan)
        db.flush()
        today = date.today()
        rows = [
            (today - timedelta(days=10), 30),   # 第 10 天（总量计入、近 7 日不计）
            (today - timedelta(days=3), 20),    # streak 链内
            (today - timedelta(days=2), 15),
            (today - timedelta(days=1), 25),
            (today - timedelta(days=1), 10),    # 同日多条打卡（求和）
        ]
        for d, m in rows:
            db.add(models.LearningLog(plan_id=plan.id, date=d, minutes=m))
        db.commit()
        expected_total = sum(m for _d, m in rows)
        expected_week = 20 + 15 + 25 + 10
        # streak：今天无打卡不清零；昨天-前天-大前天连链 = 3 天
    finally:
        db.close()

    r = client.get("/api/progress/stats/summary", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total_minutes"] == expected_total
    assert data["week_minutes"] == expected_week
    assert data["total_logs"] == len(rows)
    assert data["streak_days"] == 3


def test_stats_by_plan_topic_cache(client, auth_headers, monkeypatch):
    """主题缓存：第二次调用不再触发 _load_plan_content（溢出 JSON 磁盘读）。"""
    from app.api import plans as plans_mod
    from app.api import progress as progress_mod

    progress_mod._plan_topic_cache.clear()
    _mk_admin()
    db = SessionLocal()
    try:
        u = db.query(models.User).filter_by(username="admin").one()
        sess = models.Session(user_id=u.id, title="bp")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"intent": {"topic": "缓存主题"}})
        db.add(plan)
        db.flush()
        db.add(models.LearningLog(plan_id=plan.id, date=date.today(), minutes=42))
        db.commit()
        pid = plan.id
    finally:
        db.close()

    r1 = client.get("/api/progress/stats/by-plan", headers=auth_headers)
    assert r1.status_code == 200, r1.text
    assert r1.json()["items"][0]["topic"] == "缓存主题"
    assert pid in progress_mod._plan_topic_cache

    calls = []
    orig = plans_mod._load_plan_content

    def spy(plan):
        calls.append(plan.id)
        return orig(plan)

    monkeypatch.setattr(plans_mod, "_load_plan_content", spy)
    r2 = client.get("/api/progress/stats/by-plan", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["items"][0]["topic"] == "缓存主题"
    assert not calls  # 命中缓存，未再读盘

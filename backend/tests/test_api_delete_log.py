"""学习日志删除测试（iter 150）：删除成功 / 他人 404 / 不存在 404 / 401 / 删除后统计正确。"""

from __future__ import annotations

from datetime import date

from app.db.base import SessionLocal


def _seed() -> dict:
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        other = User(name="other", username="other", password_hash="x", is_admin=False)
        db.add_all([admin, other])
        db.flush()
        s1 = models.Session(user_id=admin.id, title="A")
        s2 = models.Session(user_id=other.id, title="B")
        db.add_all([s1, s2])
        db.flush()
        p1 = models.Plan(session_id=s1.id, version=1, content={})
        p2 = models.Plan(session_id=s2.id, version=1, content={})
        db.add_all([p1, p2])
        db.flush()
        l1 = models.LearningLog(plan_id=p1.id, date=date.today(), minutes=30)
        l2 = models.LearningLog(plan_id=p1.id, date=date.today(), minutes=20)
        l3 = models.LearningLog(plan_id=p2.id, date=date.today(), minutes=99)
        db.add_all([l1, l2, l3])
        db.commit()
        return {"plan_id": p1.id, "l1": l1.id, "l2": l2.id, "l3": l3.id}
    finally:
        db.close()


def test_delete_learning_log(client, auth_headers):
    ids = _seed()
    r = client.delete(f"/api/progress/logs/{ids['l1']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True, "id": ids["l1"]}
    # 列表只剩 l2，累计分钟更新
    r = client.get(f"/api/progress/logs/{ids['plan_id']}", headers=auth_headers)
    body = r.json()
    assert [lg["id"] for lg in body["logs"]] == [ids["l2"]]
    assert body["total_minutes"] == 20
    # 再删 -> 404
    assert client.delete(f"/api/progress/logs/{ids['l1']}", headers=auth_headers).status_code == 404


def test_delete_learning_log_isolation(client, auth_headers):
    ids = _seed()
    # 他人日志 404（不泄露存在性）
    assert client.delete(f"/api/progress/logs/{ids['l3']}", headers=auth_headers).status_code == 404
    # 不存在 404
    assert client.delete("/api/progress/logs/99999", headers=auth_headers).status_code == 404


def test_delete_learning_log_requires_auth(client):
    ids = _seed()
    assert client.delete(f"/api/progress/logs/{ids['l1']}").status_code == 401


# --- iter 268：某日跨方案打卡明细 GET /api/progress/logs?date=（热力图当日明细） ---


def _seed_two_days() -> dict:
    """admin 两方案三日日志：p1 今天两条（含备注）、昨天一条；p2（admin）今天一条；
    other 今天一条（隔离哨兵）。主题经 content.intent.topic。"""
    from datetime import timedelta

    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        other = User(name="other", username="other", password_hash="x", is_admin=False)
        db.add_all([admin, other])
        db.flush()
        s1 = models.Session(user_id=admin.id, title="A")
        s2 = models.Session(user_id=admin.id, title="B")
        s3 = models.Session(user_id=other.id, title="C")
        db.add_all([s1, s2, s3])
        db.flush()
        p1 = models.Plan(session_id=s1.id, version=1, content={"intent": {"topic": "数学"}})
        p2 = models.Plan(session_id=s2.id, version=1, content={"intent": {"topic": "英语"}})
        p3 = models.Plan(session_id=s3.id, version=1, content={"intent": {"topic": "他人方案"}})
        db.add_all([p1, p2, p3])
        db.flush()
        today = date.today()
        yesterday = today - timedelta(days=1)
        l1 = models.LearningLog(plan_id=p1.id, date=today, minutes=30, note="导数复习")
        l2 = models.LearningLog(plan_id=p1.id, date=today, minutes=20)
        l3 = models.LearningLog(plan_id=p2.id, date=today, minutes=45, note="单词")
        l4 = models.LearningLog(plan_id=p1.id, date=yesterday, minutes=60)
        l5 = models.LearningLog(plan_id=p3.id, date=today, minutes=99)
        db.add_all([l1, l2, l3, l4, l5])
        db.commit()
        return {"today": today.isoformat(), "yesterday": yesterday.isoformat(), "p1": p1.id, "p2": p2.id}
    finally:
        db.close()


def test_day_logs_cross_plan(client, auth_headers):
    ids = _seed_two_days()
    r = client.get(f"/api/progress/logs?date={ids['today']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["date"] == ids["today"]
    items = body["items"]
    # admin 两方案今日共 3 条（他人方案不可见；昨天的不出现），id 倒序（后打的在前）
    assert len(items) == 3
    assert [i["id"] for i in items] == sorted((i["id"] for i in items), reverse=True)
    by_plan = {i["plan_id"]: i for i in items if i["plan_id"] in (ids["p1"], ids["p2"])}
    assert by_plan[ids["p2"]]["topic"] == "英语"
    assert by_plan[ids["p2"]]["minutes"] == 45
    assert by_plan[ids["p2"]]["note"] == "单词"
    p1_notes = sorted(i["note"] or "" for i in items if i["plan_id"] == ids["p1"])
    assert p1_notes == ["", "导数复习"]
    # 无打卡的日期 -> 空列表
    r2 = client.get("/api/progress/logs?date=2000-01-01", headers=auth_headers)
    assert r2.status_code == 200 and r2.json()["items"] == []


def test_day_logs_isolation_and_validation(client, auth_headers):
    ids = _seed_two_days()
    # 他人当日有打卡（99 分钟哨兵）——不可见（上面 cross_plan 已断言仅 3 条，此处独立复核口径）
    items = client.get(f"/api/progress/logs?date={ids['today']}", headers=auth_headers).json()["items"]
    assert all(i["minutes"] != 99 for i in items)
    # 非法日期 422（FastAPI date 解析）；缺参 422
    assert client.get("/api/progress/logs?date=not-a-date", headers=auth_headers).status_code == 422
    assert client.get("/api/progress/logs", headers=auth_headers).status_code == 422


def test_day_logs_requires_auth(client):
    _seed_two_days()
    assert client.get(f"/api/progress/logs?date={date.today().isoformat()}").status_code == 401


# --- iter 277：补卡（显式过去日期）+ 未来日期拒绝 ---


def test_backfill_log_past_date(client, auth_headers):
    """补卡：显式传过去日期 -> 200 且按该日期落库；缺省仍今天。"""
    from datetime import timedelta

    ids = _seed()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    r = client.post(
        "/api/progress/logs",
        json={"plan_id": ids["plan_id"], "minutes": 25, "note": "补昨天的", "date": yesterday},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["date"] == yesterday
    # 缺省 date 仍记今天（回归）
    r2 = client.post("/api/progress/logs", json={"plan_id": ids["plan_id"], "minutes": 10}, headers=auth_headers)
    assert r2.json()["date"] == date.today().isoformat()


def test_log_future_date_rejected(client, auth_headers):
    """未来日期 -> 400（污染热力图/streak，无正当用途）。"""
    from datetime import timedelta

    ids = _seed()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    r = client.post(
        "/api/progress/logs",
        json={"plan_id": ids["plan_id"], "minutes": 30, "date": tomorrow},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_date"

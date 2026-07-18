"""跨方案错题总览测试（iter 156）：按方案聚合错题数 / 排序 / 隔离 / 空态 / 401。"""

from __future__ import annotations

from app.db.base import SessionLocal


def _seed() -> dict:
    """方案 A：2 错题 1 对；方案 B：1 错题；他人方案：1 错题（隔离）。"""
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        other = User(name="other", username="other", password_hash="x", is_admin=False)
        db.add_all([admin, other])
        db.flush()
        sa = models.Session(user_id=admin.id, title="A")
        sb = models.Session(user_id=admin.id, title="B")
        sc = models.Session(user_id=other.id, title="C")
        db.add_all([sa, sb, sc])
        db.flush()
        pa = models.Plan(session_id=sa.id, version=1, content={"intent": {"topic": "方案甲"}})
        pb = models.Plan(session_id=sb.id, version=1, content={"intent": {"topic": "方案乙"}})
        pc = models.Plan(session_id=sc.id, version=1, content={"intent": {"topic": "他人方案"}})
        db.add_all([pa, pb, pc])
        db.flush()

        def quiz(plan_id: int) -> models.Quiz:
            q = models.Quiz(plan_id=plan_id, type=models.QuizType.choice, payload={"question": "?"})
            db.add(q)
            db.flush()
            return q

        qa1, qa2, qa3 = quiz(pa.id), quiz(pa.id), quiz(pa.id)
        qb1 = quiz(pb.id)
        qc1 = quiz(pc.id)
        db.add_all(
            [
                models.QuizAttempt(quiz_id=qa1.id, user_answer={}, correct=False),
                models.QuizAttempt(quiz_id=qa2.id, user_answer={}, correct=False),
                models.QuizAttempt(quiz_id=qa3.id, user_answer={}, correct=True),
                models.QuizAttempt(quiz_id=qb1.id, user_answer={}, correct=False),
                models.QuizAttempt(quiz_id=qc1.id, user_answer={}, correct=False),  # 他人 -> 隔离
            ]
        )
        db.commit()
        return {"pa": pa.id, "pb": pb.id}
    finally:
        db.close()


def test_wrong_overview_groups_by_plan(client, auth_headers):
    ids = _seed()
    r = client.get("/api/quizzes/wrong-overview", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3  # 他人错题不计
    assert body["items"] == [
        {"plan_id": ids["pa"], "topic": "方案甲", "wrong_count": 2},  # 错题多者在前
        {"plan_id": ids["pb"], "topic": "方案乙", "wrong_count": 1},
    ]


def test_wrong_overview_empty(client, auth_headers):
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(admin)
        db.commit()
    finally:
        db.close()
    r = client.get("/api/quizzes/wrong-overview", headers=auth_headers)
    assert r.json() == {"items": [], "total": 0}


def test_wrong_overview_requires_auth(client):
    assert client.get("/api/quizzes/wrong-overview").status_code == 401

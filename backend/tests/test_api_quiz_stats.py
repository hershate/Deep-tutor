"""测验历史统计测试（iter 152）：作答计数 / 判分正确率 / 错题数 / 自评题不计 / 隔离 / 401。"""

from __future__ import annotations

from app.db.base import SessionLocal


def _seed() -> dict:
    """3 题 4 作答：q1 错（最新）+曾对，q2 对，q3 自评 None。"""
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
        p1 = models.Plan(session_id=s1.id, version=1, content={"intent": {"topic": "T"}})
        p2 = models.Plan(session_id=s2.id, version=1, content={"intent": {"topic": "他人"}})
        db.add_all([p1, p2])
        db.flush()
        q1 = models.Quiz(plan_id=p1.id, type=models.QuizType.choice, payload={"question": "1"})
        q2 = models.Quiz(plan_id=p1.id, type=models.QuizType.choice, payload={"question": "2"})
        q3 = models.Quiz(plan_id=p1.id, type=models.QuizType.short, payload={"question": "3"})
        q4 = models.Quiz(plan_id=p2.id, type=models.QuizType.choice, payload={"question": "他人题"})
        db.add_all([q1, q2, q3, q4])
        db.flush()
        db.add_all(
            [
                models.QuizAttempt(quiz_id=q1.id, user_answer={}, correct=True),  # 旧：曾对
                models.QuizAttempt(quiz_id=q1.id, user_answer={}, correct=False),  # 新：判错 -> 错题 1
                models.QuizAttempt(quiz_id=q2.id, user_answer={}, correct=True),
                models.QuizAttempt(quiz_id=q3.id, user_answer={}, correct=None),  # 自评不计
                models.QuizAttempt(quiz_id=q4.id, user_answer={}, correct=False),  # 他人 -> 隔离
            ]
        )
        db.commit()
        return {"plan_id": p1.id, "other_plan_id": p2.id}
    finally:
        db.close()


def test_quiz_stats_counts(client, auth_headers):
    ids = _seed()
    r = client.get(f"/api/plans/{ids['plan_id']}/quiz/stats", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["quiz_count"] == 3
    assert body["attempts"] == 4  # 他人作答不计
    assert body["graded"] == 3  # 自评 None 不计
    assert body["correct"] == 2
    assert body["accuracy"] == round(2 / 3, 4)
    assert body["wrong_count"] == 1  # q1 最新判错


def test_quiz_stats_empty_plan(client, auth_headers):
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(admin)
        db.flush()
        sess = models.Session(user_id=admin.id, title="空")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"intent": {"topic": "空"}})
        db.add(plan)
        db.commit()
        plan_id = plan.id
    finally:
        db.close()
    r = client.get(f"/api/plans/{plan_id}/quiz/stats", headers=auth_headers)
    assert r.json() == {
        "quiz_count": 0,
        "attempts": 0,
        "graded": 0,
        "correct": 0,
        "accuracy": None,
        "wrong_count": 0,
    }


def test_quiz_stats_isolation_and_auth(client, auth_headers):
    ids = _seed()
    assert (
        client.get(f"/api/plans/{ids['other_plan_id']}/quiz/stats", headers=auth_headers).status_code
        == 404
    )
    assert client.get(f"/api/plans/{ids['plan_id']}/quiz/stats").status_code == 401

"""错题重练端点测试（iter 114）：最新作答判错筛选 / 改对即出列 / 自评题不计 / 隔离 / 空态。"""

from __future__ import annotations

from app.db.base import SessionLocal


def _seed():
    """q1 最新判错、q2 先错后对、q3 自评题判分 None、q4 未作答。"""
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(admin)
        db.flush()
        sess = models.Session(user_id=admin.id, title="错题会话")
        db.add(sess)
        db.flush()
        plan = models.Plan(
            session_id=sess.id, version=1, content={"intent": {"topic": "数据结构"}}
        )
        db.add(plan)
        db.flush()
        q1 = models.Quiz(plan_id=plan.id, type=models.QuizType.choice, payload={"question": "题1"})
        q2 = models.Quiz(plan_id=plan.id, type=models.QuizType.choice, payload={"question": "题2"})
        q3 = models.Quiz(plan_id=plan.id, type=models.QuizType.short, payload={"question": "题3"})
        q4 = models.Quiz(plan_id=plan.id, type=models.QuizType.choice, payload={"question": "题4"})
        db.add_all([q1, q2, q3, q4])
        db.flush()
        db.add_all(
            [
                models.QuizAttempt(quiz_id=q1.id, user_answer={"selected": "A"}, correct=False),
                models.QuizAttempt(quiz_id=q2.id, user_answer={"selected": "A"}, correct=False),
                models.QuizAttempt(quiz_id=q2.id, user_answer={"selected": "B"}, correct=True),
                models.QuizAttempt(quiz_id=q3.id, user_answer={"text": "自评"}, correct=None),
            ]
        )
        db.commit()
        return {"plan_id": plan.id, "q1": q1.id, "q2": q2.id, "q3": q3.id, "q4": q4.id}
    finally:
        db.close()


def test_wrong_quizzes_filters_latest_incorrect(client, auth_headers):
    ids = _seed()
    r = client.get(f"/api/plans/{ids['plan_id']}/quiz/wrong", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["quiz_count"] == 1  # 仅 q1：q2 已改对、q3 自评 None、q4 未作答
    assert body["quizzes"][0]["id"] == ids["q1"]
    assert body["quizzes"][0]["question"] == "题1"
    assert "错题重练" in body["title"]


def test_wrong_quizzes_empty_when_no_attempts(client, auth_headers):
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(admin)
        db.flush()
        sess = models.Session(user_id=admin.id, title="空会话")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"intent": {"topic": "T"}})
        db.add(plan)
        db.flush()
        db.add(models.Quiz(plan_id=plan.id, type=models.QuizType.choice, payload={"question": "题"}))
        db.commit()
        plan_id = plan.id
    finally:
        db.close()
    r = client.get(f"/api/plans/{plan_id}/quiz/wrong", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["quiz_count"] == 0 and r.json()["quizzes"] == []


def test_wrong_quizzes_isolation_and_auth(client, auth_headers):
    from app.db import models
    from app.db.models import User

    ids = _seed()
    db = SessionLocal()
    try:
        other = User(name="other", username="other", password_hash="x", is_admin=False)
        db.add(other)
        db.flush()
        other_sess = models.Session(user_id=other.id, title="他人会话")
        db.add(other_sess)
        db.flush()
        other_plan = models.Plan(session_id=other_sess.id, version=1, content={"intent": {}})
        db.add(other_plan)
        db.commit()
        other_plan_id = other_plan.id
    finally:
        db.close()
    # 他人方案 404（不泄露存在性）
    assert client.get(f"/api/plans/{other_plan_id}/quiz/wrong", headers=auth_headers).status_code == 404
    # 未鉴权 401
    assert client.get(f"/api/plans/{ids['plan_id']}/quiz/wrong").status_code == 401


def test_wrong_quizzes_recovers_after_retry_correct(client, auth_headers):
    """重练答对后该题出列（提交走 /quizzes/{id}/submit 追加 attempt）。"""
    ids = _seed()
    r = client.post(
        f"/api/quizzes/{ids['q1']}/submit",
        json={"user_answer": {"selected": "A"}},  # payload 无 answer 字段时判分逻辑见 quizzes.py
        headers=auth_headers,
    )
    assert r.status_code == 200
    # 模拟改对：直接补一条 correct=True 作答（提交判分依赖 payload.answer，种子未含）
    from app.db import models

    db = SessionLocal()
    try:
        db.add(models.QuizAttempt(quiz_id=ids["q1"], user_answer={"selected": "B"}, correct=True))
        db.commit()
    finally:
        db.close()
    r = client.get(f"/api/plans/{ids['plan_id']}/quiz/wrong", headers=auth_headers)
    assert r.json()["quiz_count"] == 0

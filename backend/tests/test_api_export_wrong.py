"""错题 Anki 导出测试（iter 134）：export wrong_only 仅收错题 / 无错题报错 / 非 anki 拒绝 / 隔离。"""

from __future__ import annotations

from app.db.base import SessionLocal


def _seed(wrong: bool = True) -> int:
    """1 方案 2 题：q1 判错（有 answer，可成卡）、q2 判对。wrong=False 时 q1 也判对。"""
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(admin)
        db.flush()
        sess = models.Session(user_id=admin.id, title="错题导出会话")
        db.add(sess)
        db.flush()
        plan = models.Plan(
            session_id=sess.id,
            version=1,
            content={"intent": {"topic": "算法"}, "quizzes": []},
        )
        db.add(plan)
        db.flush()
        q1 = models.Quiz(
            plan_id=plan.id,
            type=models.QuizType.choice,
            payload={"question": "什么是栈？", "options": ["A", "B"], "answer": "后进先出"},
        )
        q2 = models.Quiz(
            plan_id=plan.id,
            type=models.QuizType.choice,
            payload={"question": "什么是队列？", "options": ["A", "B"], "answer": "先进先出"},
        )
        db.add_all([q1, q2])
        db.flush()
        db.add_all(
            [
                models.QuizAttempt(quiz_id=q1.id, user_answer={"selected": "A"}, correct=not wrong),
                models.QuizAttempt(quiz_id=q2.id, user_answer={"selected": "A"}, correct=True),
            ]
        )
        db.commit()
        return plan.id
    finally:
        db.close()


def test_export_wrong_only_anki_contains_only_wrong(client, auth_headers):
    plan_id = _seed()
    r = client.post(
        f"/api/export/{plan_id}", json={"format": "anki", "wrong_only": True}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "apkg"
    assert body["card_count"] == 1  # 仅 q1（q2 已答对）
    assert body["filename"] == f"deep-tutor-plan-{plan_id}-wrong.apkg"
    assert body["apkg_b64"]  # base64 非空

    # 全量导出不受影响：content.quizzes 为空 -> no_quizzes（口径分离验证）
    r2 = client.post(f"/api/export/{plan_id}", json={"format": "anki"}, headers=auth_headers)
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "no_quizzes"


def test_export_wrong_only_no_wrong(client, auth_headers):
    plan_id = _seed(wrong=False)  # 全部答对
    r = client.post(
        f"/api/export/{plan_id}", json={"format": "anki", "wrong_only": True}, headers=auth_headers
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "no_wrong_quizzes"


def test_export_wrong_only_rejects_other_formats(client, auth_headers):
    plan_id = _seed()
    r = client.post(
        f"/api/export/{plan_id}", json={"format": "md", "wrong_only": True}, headers=auth_headers
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_export_wrong_only_isolation_and_auth(client, auth_headers):
    plan_id = _seed()
    # 未鉴权 401
    r = client.post(f"/api/export/{plan_id}", json={"format": "anki", "wrong_only": True})
    assert r.status_code == 401
    # 他人方案 404
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        other = User(name="other", username="other", password_hash="x", is_admin=False)
        db.add(other)
        db.flush()
        sess = models.Session(user_id=other.id, title="他人会话")
        db.add(sess)
        db.flush()
        other_plan = models.Plan(session_id=sess.id, version=1, content={"intent": {}})
        db.add(other_plan)
        db.commit()
        other_plan_id = other_plan.id
    finally:
        db.close()
    r = client.post(
        f"/api/export/{other_plan_id}",
        json={"format": "anki", "wrong_only": True},
        headers=auth_headers,
    )
    assert r.status_code == 404

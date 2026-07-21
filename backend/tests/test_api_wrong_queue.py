"""错题复习队列测试（iter 285，/review 跨方案错题重练数据源）。

覆盖：最新作答判错筛选 / 重答正确即移除 / payload 完整透传 / plan_topic / 用户隔离 /
排序（最近判错在前）/ 空态 / 401。判分口径与 /wrong-overview、/plans/{id}/quiz/wrong 一致。
"""

from __future__ import annotations

from app.db.base import SessionLocal


def _seed() -> dict:
    """方案甲：qa1（先错后对->最新对，不进队列）、qa2（错）；方案乙：qb1（错）；他人：qc1（错，隔离）。"""
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

        def quiz(plan_id: int, q: str, opts: list[str], ans: str) -> models.Quiz:
            row = models.Quiz(
                plan_id=plan_id,
                type=models.QuizType.choice,
                payload={"question": q, "options": opts, "answer": ans, "explanation": "解析"},
            )
            db.add(row)
            db.flush()
            return row

        qa1 = quiz(pa.id, "甲1", ["x", "y"], "x")
        qa2 = quiz(pa.id, "甲2", ["p", "q"], "q")
        qb1 = quiz(pb.id, "乙1", ["m", "n"], "m")
        qc1 = quiz(pc.id, "他1", ["a", "b"], "a")
        db.add_all(
            [
                # qa1：先错后对 -> 最新正确 -> 不进错题队列（重答正确即移除，复习核心语义）
                models.QuizAttempt(quiz_id=qa1.id, user_answer={}, correct=False),
                models.QuizAttempt(quiz_id=qa1.id, user_answer={}, correct=True),
                models.QuizAttempt(quiz_id=qa2.id, user_answer={}, correct=False),
                models.QuizAttempt(quiz_id=qb1.id, user_answer={}, correct=False),
                models.QuizAttempt(quiz_id=qc1.id, user_answer={}, correct=False),  # 他人 -> 隔离
            ]
        )
        db.commit()
        return {"qa2": qa2.id, "qb1": qb1.id, "pa": pa.id, "pb": pb.id}
    finally:
        db.close()


def test_wrong_queue_filters_payload_and_isolates(client, auth_headers):
    ids = _seed()
    r = client.get("/api/quizzes/wrong-queue", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # qa1 重答正确已移除；qa3 无；qc1 他人隔离 -> 仅 qa2、qb1
    assert body["total"] == 2
    assert [i["id"] for i in body["items"]] == [ids["qb1"], ids["qa2"]]  # 最近判错在前（qb1 后插）
    qa2 = next(i for i in body["items"] if i["id"] == ids["qa2"])
    assert qa2["plan_id"] == ids["pa"]
    assert qa2["plan_topic"] == "方案甲"
    assert qa2["type"] == "choice"
    assert qa2["question"] == "甲2"
    assert qa2["options"] == ["p", "q"]
    assert qa2["answer"] == "q"
    assert qa2["explanation"] == "解析"


def test_wrong_queue_empty(client, auth_headers):
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="admin", username="admin", password_hash="", is_admin=True))
        db.commit()
    finally:
        db.close()
    r = client.get("/api/quizzes/wrong-queue", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}


def test_wrong_queue_requires_auth(client):
    assert client.get("/api/quizzes/wrong-queue").status_code == 401

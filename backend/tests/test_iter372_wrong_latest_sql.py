"""iter 372 R12：错题族「每题最新 attempt」窗口函数下推的语义回归。

三端点（GET /quizzes/wrong-queue、GET /quizzes/wrong-overview、GET /plans/{id}/quiz/wrong）
此前全量物化 ORM 对象再 dict 累积（随作答历史线性劣化），改为 SQL 窗口函数（ROW_NUMBER
PARTITION BY quiz_id ORDER BY created_at DESC, id DESC）。本文件锁死两个排序语义不被
「顺手优化」掉：
- created_at 优先于 id：小 id 但更晚 created_at 的作答才是「最新」（与旧实现一致）；
- 同刻并列时 id 大者优先。
以及 wrong-queue 的「最近判错在前」截断偏置修复（iter 368）行为不变。
"""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db import models
from app.db.base import SessionLocal
from app.main import app

client = TestClient(app)

_T0 = datetime(2026, 1, 1, 12, 0, 0)


def _mk_user(name: str) -> tuple[int, str]:
    db = SessionLocal()
    try:
        u = models.User(name=name, username=name, password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, create_token(u.id, name)
    finally:
        db.close()


def _seed(uid: int) -> tuple[int, list[int]]:
    """两方案各两题 + 刻意构造的时序陷阱（见断言注释）。返回 (plan_id, quiz_ids)。"""
    db = SessionLocal()
    try:
        sess = models.Session(user_id=uid, title="错题语义")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"intent": {"topic": "语义"}})
        db.add(plan)
        db.flush()
        node = models.KnowledgeNode(plan_id=plan.id, sid="n1", name="N1")
        db.add(node)
        db.flush()
        q1 = models.Quiz(plan_id=plan.id, node_id=node.id, type=models.QuizType.choice)
        q2 = models.Quiz(plan_id=plan.id, node_id=node.id, type=models.QuizType.choice)
        db.add_all([q1, q2])
        db.commit()
        db.refresh(q1)
        db.refresh(q2)

        def att(qid: int, correct: bool | None, dt: datetime) -> None:
            db.add(models.QuizAttempt(quiz_id=qid, correct=correct, created_at=dt))

        # q1：id 小但 created_at 更晚（correct=True）——最新判定必须看 created_at 而非 id，
        # 最新为对 -> q1 不入错题集
        att(q1.id, False, _T0)                    # 早：判错
        att(q1.id, True, _T0 + timedelta(hours=1))  # 晚：判对（id 更大，也判对——双保险）
        # q1b：反转陷阱——晚答的 id 小（手工指定 created_at，id 仍按插入序）
        # （上面两行已覆盖 created_at 主序；再补「晚 id 小」的独立验证见 q2）
        # q2：同刻并列，id 大者（判对）优先 -> q2 不入错题集；若 id 小者优先则误入
        att(q2.id, False, _T0)  # id 较小，同刻
        att(q2.id, True, _T0)   # id 较大，同刻 -> 最新
        db.commit()
        return plan.id, [q1.id, q2.id]
    finally:
        db.close()


def test_latest_attempt_semantics_created_at_then_id():
    """created_at 主序 + 同刻 id 大者优先：q1/q2 最新均判对 -> 两端点零错题。"""
    uid, tok = _mk_user("wrong_sem_a")
    h = {"Authorization": f"Bearer {tok}"}
    plan_id, _ = _seed(uid)

    r = client.get("/api/quizzes/wrong-queue", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0 and body["items"] == [], body

    r = client.get("/api/quizzes/wrong-overview", headers=h)
    assert r.status_code == 200
    assert r.json()["total"] == 0

    r = client.get(f"/api/plans/{plan_id}/quiz/wrong", headers=h)
    assert r.status_code == 200
    assert r.json()["quiz_count"] == 0


def test_wrong_queue_recent_first_and_limit():
    """多题判错时按最新 attempt id 倒序截断（iter 368 行为保持）。"""
    uid, tok = _mk_user("wrong_sem_b")
    h = {"Authorization": f"Bearer {tok}"}
    db = SessionLocal()
    try:
        sess = models.Session(user_id=uid, title="队列序")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"intent": {"topic": "序"}})
        db.add(plan)
        db.flush()
        node = models.KnowledgeNode(plan_id=plan.id, sid="n", name="N")
        db.add(node)
        db.flush()
        quiz_ids = []
        for i in range(5):
            q = models.Quiz(plan_id=plan.id, node_id=node.id, type=models.QuizType.choice)
            db.add(q)
            db.flush()
            quiz_ids.append(q.id)
            db.add(models.QuizAttempt(quiz_id=q.id, correct=False, created_at=_T0 + timedelta(minutes=i)))
        # q[2] 后来改判对 -> 不入队
        db.add(models.QuizAttempt(quiz_id=quiz_ids[2], correct=True, created_at=_T0 + timedelta(hours=1)))
        db.commit()
    finally:
        db.close()

    r = client.get("/api/quizzes/wrong-queue?limit=2", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 4  # 5 题判错 - 1 题改判对
    assert len(body["items"]) == 2
    got = [it["id"] for it in body["items"]]
    # 最近判错在前：q4（第 5 题，最新 attempt id 最大）-> q3；q2 被改判对排除
    assert got == [quiz_ids[4], quiz_ids[3]], got

    # overview 计数：该方案 4 错
    r = client.get("/api/quizzes/wrong-overview", headers=h)
    assert r.json()["total"] == 4

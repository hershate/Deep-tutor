"""删除会话/用户的级联测试（iter 353）。

复现并守护：DELETE /api/sessions/{id} 与 delete_user 在 session 含
quiz/progress/study_unit（裸 FK 引用 knowledge_nodes，此前无 ORM cascade）+
thread（裸 FK 引用 session，iter 345 记忆系统）时不应抛
500 FOREIGN KEY constraint failed。

根因：PRAGMA foreign_keys=ON 下，Plan.knowledge_nodes cascade 删 KnowledgeNode，
但 quizzes.node_id / progress.node_id / study_units.node_id 仅裸 FK、无 ORM
relationship 表达依赖——SQLAlchemy UoW 不知删除顺序，先 flush DELETE knowledge_nodes
撞 FK。threads.session_id 同理撞 sessions.id。此前无测试覆盖此删除路径，故逃过回归。
"""

from __future__ import annotations

from app.core.security import hash_password

BASE = "/api/sessions"


def _mk_user(username: str, password: str = "pw123", is_admin: bool = False) -> int:
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(
            name=username,
            username=username,
            password_hash=hash_password(password),
            is_admin=is_admin,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _login(client, username: str, password: str = "pw123") -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _seed(user_id: int, with_thread: bool = False) -> int:
    """建 session + plan + 2 知识点 + quiz/progress/study_unit/resource/node_note（引用 node）[+ thread]。"""
    from app.db.base import SessionLocal
    from app.db import models

    db = SessionLocal()
    try:
        sess = models.Session(user_id=user_id, title="t")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, content={"topic": "x"}, version=1)
        db.add(plan)
        db.flush()
        n1 = models.KnowledgeNode(plan_id=plan.id, sid="k1", name="node1", difficulty=3)
        n2 = models.KnowledgeNode(plan_id=plan.id, sid="k2", name="node2", difficulty=4)
        db.add_all([n1, n2])
        db.flush()
        # 裸 FK 引用 knowledge_nodes（bug 触发点：无 ORM cascade relationship）
        db.add(
            models.Quiz(plan_id=plan.id, node_id=n1.id, type=models.QuizType.choice, payload={"q": 1})
        )
        db.add(
            models.Progress(
                plan_id=plan.id,
                node_id=n2.id,
                status=models.ProgressStatus.learning,
                mastery=0.2,
            )
        )
        db.add(models.StudyUnit(plan_id=plan.id, node_id=n1.id, title="unit1", node_sids=["k1"]))
        # 走 ORM cascade（KnowledgeNode.resources/notes），一并守护
        db.add(models.Resource(node_id=n2.id, url="https://x.example", title="r"))
        db.add(models.NodeNote(user_id=user_id, node_id=n1.id, content="note"))
        if with_thread:
            # thread.session_id 裸 FK 引用 session（Session 无→Thread relationship）
            db.add(models.Thread(user_id=user_id, title="th", session_id=sess.id))
        db.commit()
        return sess.id
    finally:
        db.close()


def test_delete_session_with_quiz_progress_studyunit(client):
    """删含 quiz/progress/study_unit/resource/note 的 session 不应 500。"""
    uid = _mk_user("u1")
    h = _login(client, "u1")
    sid = _seed(uid)
    r = client.delete(f"{BASE}/{sid}", headers=h)
    assert r.status_code == 200, r.text

    from app.db.base import SessionLocal
    from app.db import models

    db = SessionLocal()
    try:
        assert db.get(models.Session, sid) is None
        assert db.query(models.Plan).count() == 0
        assert db.query(models.KnowledgeNode).count() == 0
        assert db.query(models.Quiz).count() == 0
        assert db.query(models.Progress).count() == 0
        assert db.query(models.StudyUnit).count() == 0
        assert db.query(models.Resource).count() == 0
        assert db.query(models.NodeNote).count() == 0
    finally:
        db.close()


def test_delete_session_with_thread(client):
    """删含 thread（引用 session）的 session 不应 500；thread 脱钩保留（session_id 置空）。"""
    uid = _mk_user("u2")
    h = _login(client, "u2")
    sid = _seed(uid, with_thread=True)

    from app.db.base import SessionLocal
    from app.db import models

    db = SessionLocal()
    try:
        tid = db.query(models.Thread).filter_by(session_id=sid).first().id
    finally:
        db.close()

    r = client.delete(f"{BASE}/{sid}", headers=h)
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        # 对话不随生成会话删除——脱钩保留（session_id 置空）
        t = db.get(models.Thread, tid)
        assert t is not None
        assert t.session_id is None
    finally:
        db.close()


def test_delete_user_cascades_session_with_data(client):
    """管理员删用户（User.sessions cascade 走同一删除链路）不应 500。"""
    _mk_user("admin", is_admin=True)
    ha = _login(client, "admin")
    uid = _mk_user("u3")
    _seed(uid, with_thread=True)

    r = client.delete(f"/api/users/{uid}", headers=ha)
    assert r.status_code == 200, r.text

    from app.db.base import SessionLocal
    from app.db import models

    db = SessionLocal()
    try:
        assert db.get(models.User, uid) is None
        assert db.query(models.Session).count() == 0
        # thread 归属 User（User.threads cascade）——删用户级联删其 thread
        # （区别于删 session：thread 仅脱钩保留）
        assert db.query(models.Thread).count() == 0
    finally:
        db.close()

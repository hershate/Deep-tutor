"""iter 371 R9：级联完整性实测——删用户/删会话后全表零孤儿。

models.py 关系审计（R9-a）确认 ORM delete-orphan 全覆盖，但「审计读码」之外
补一层端到端实测：构造覆盖**每一张用户数据表**的完整数据树（sessions/plans/
materials/kb/threads/runs/messages/memories/notes/node_notes/prefs/quizzes/
attempts/progress/learning_logs/api_usage/provider+credential profiles/share/
study_units/study_materials/kb_chat_records/tool_calls），经真实端点删除后
逐表断言零残留——任何一处级联缺口都会让孤儿行长期堆积（数据操作级 BUG）。
"""

from datetime import date

from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db import models
from app.db.base import SessionLocal
from app.main import app

client = TestClient(app)

# 删用户后必须清零的表 -> (模型, 过滤列)。凡以 user_id/session_id/plan_id 归属的表全列
_CHILD_TABLES = [
    ("sessions", models.Session, "user_id"),
    ("threads", models.Thread, "user_id"),
    ("runs", models.Run, "user_id"),
    ("run_steps", models.RunStep, None),  # 经 run 级联，按 run_id 归属——单独核
    ("memories", models.Memory, "user_id"),
    ("notes", models.Note, "user_id"),
    ("node_notes", models.NodeNote, "user_id"),
    ("user_prefs", models.UserPref, "user_id"),
    ("kb_documents", models.KbDocument, "user_id"),
    ("kb_chat_records", models.KbChatRecord, "user_id"),
    ("api_usage", models.ApiUsage, "user_id"),
    ("provider_profiles", models.LlmProviderProfile, "user_id"),
    ("credential_profiles", models.CredentialProfile, "user_id"),
]


def _build_tree(uid: int) -> None:
    """直接经 ORM 构造全表数据树（端点走通即可，构造走捷径）。"""
    db = SessionLocal()
    try:
        sess = models.Session(user_id=uid, title="级联")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"topic": "T"})
        db.add(plan)
        db.flush()
        node = models.KnowledgeNode(plan_id=plan.id, sid="n1", name="N1")
        db.add(node)
        db.flush()
        db.add_all(
            [
                models.Material(session_id=sess.id, kind=models.MaterialKind.text, source="m"),
                models.ToolCall(session_id=sess.id, tool="t", status=models.ToolCallStatus.ok),
                models.Quiz(plan_id=plan.id, node_id=node.id, type=models.QuizType.choice),
                models.Progress(plan_id=plan.id, node_id=node.id),
                models.LearningLog(plan_id=plan.id, date=date.today(), minutes=10),
                models.NodeNote(user_id=uid, node_id=node.id, content="批注"),
                models.Note(user_id=uid, title="n", content="c"),
                models.UserPref(user_id=uid, key="k", value="v"),
                models.KbDocument(user_id=uid, title="kb", kind=models.MaterialKind.text, source="s"),
                models.KbChatRecord(user_id=uid, question="q", answer="a"),
                models.ApiUsage(user_id=uid, cost=0.1),
                models.LlmProviderProfile(user_id=uid, name="p", api_key="k", kind="anthropic"),
                models.CredentialProfile(user_id=uid, kind="api_key", name="c", api_key="k"),
                models.ShareLink(plan_id=plan.id, token=f"tok-{uid}"),
                models.StudyUnit(plan_id=plan.id, node_id=node.id, title="u"),
            ]
        )
        db.flush()
        quiz = db.query(models.Quiz).filter_by(plan_id=plan.id).first()
        db.add(models.QuizAttempt(quiz_id=quiz.id, user_answer={}, correct=True))
        unit = db.query(models.StudyUnit).filter_by(plan_id=plan.id).first()
        db.add(models.StudyMaterial(unit_id=unit.id, content_md_path="data/x.md"))
        th = models.Thread(user_id=uid, title="th")
        db.add(th)
        db.flush()
        run = models.Run(thread_id=th.id, user_id=uid, status=models.RunStatus.done)
        db.add(run)
        db.flush()
        db.add_all(
            [
                models.Message(thread_id=th.id, role=models.MessageRole.user, content=[]),
                models.RunStep(run_id=run.id, step_index=1, type="tool_call"),
                models.RunArtifact(run_id=run.id, thread_id=th.id, name="a", path="data/runs/a"),
                models.Memory(user_id=uid, scope="user", kind="note", content="m"),
            ]
        )
        db.commit()
    finally:
        db.close()


def test_delete_user_leaves_no_orphans():
    db = SessionLocal()
    try:
        victim = models.User(name="victim", username="cascade_victim", password_hash="x")
        admin = models.User(name="adm", username="cascade_admin", password_hash="x", is_admin=True)
        db.add_all([victim, admin])
        db.commit()
        db.refresh(victim)
        db.refresh(admin)
        vid, aid = victim.id, admin.id
    finally:
        db.close()
    _build_tree(vid)

    r = client.delete(f"/api/users/{vid}", headers={"Authorization": f"Bearer {create_token(aid, 'cascade_admin')}"})
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        for name, model, col in _CHILD_TABLES:
            if col is None:
                continue
            n = db.query(model).filter(getattr(model, col) == vid).count()
            assert n == 0, f"{name} 残留 {n} 行孤儿"
        # 经父表级联的表：按 plan/session/thread 侧全库核对（该用户相关行应全消失）
        assert db.query(models.QuizAttempt).count() == 0, "quiz_attempts 经 quiz 级联应清空"
        assert db.query(models.StudyMaterial).count() == 0, "study_materials 经 unit 级联应清空"
        assert db.query(models.RunStep).count() == 0, "run_steps 经 run 级联应清空"
        assert db.query(models.Message).count() == 0, "messages 经 thread 级联应清空"
        assert db.query(models.RunArtifact).count() == 0, "run_artifacts 经 thread 级联应清空"
        assert db.query(models.ShareLink).count() == 0, "share_links 经 plan 级联应清空"
        assert db.query(models.Quiz).count() == 0
        assert db.query(models.Progress).count() == 0
        assert db.query(models.LearningLog).count() == 0
        assert db.query(models.KnowledgeNode).count() == 0
        assert db.query(models.ToolCall).count() == 0
        assert db.query(models.Material).count() == 0
        assert db.query(models.Plan).count() == 0
        assert db.query(models.OrchestratorState).count() == 0
        # 用户本体删除
        assert db.get(models.User, vid) is None
    finally:
        db.close()


def test_delete_session_nulls_thread_reference_and_cascades():
    """Session.threads 关系默认级联 = 删会话置空 thread.session_id（iter 353 语义），
    plans/materials 等经 ORM delete-orphan 全删。"""
    db = SessionLocal()
    try:
        u = models.User(name="s", username="cascade_sess", password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        uid = u.id
        sess = models.Session(user_id=uid, title="s1")
        db.add(sess)
        db.flush()
        db.add(models.Plan(session_id=sess.id, version=1, content={}))
        db.flush()
        th = models.Thread(user_id=uid, title="引用会话", session_id=sess.id)
        db.add(th)
        db.commit()
        sid, tid = sess.id, th.id
    finally:
        db.close()

    token = create_token(uid, "cascade_sess")
    r = client.delete(f"/api/sessions/{sid}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        assert db.query(models.Plan).count() == 0, "plans 未级联"
        t = db.get(models.Thread, tid)
        assert t is not None and t.session_id is None, "thread.session_id 应被置空而非删行/悬挂"
    finally:
        db.close()

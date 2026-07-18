"""数据备份导出/导入测试（iter 93）：roundtrip 保真 / 凭证边界 / 追加语义 / 格式校验 / 用户隔离。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.core.settings import settings
from app.db.base import Base, SessionLocal, engine


def _mk_admin():
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _seed_rich_data(user_id: int) -> dict:
    """造一份覆盖全部实体的数据，返回关键 id/内容供断言。"""
    from app.core.storage import write_parsed_text, write_unit_content
    from app.db import models

    db = SessionLocal()
    try:
        sess = models.Session(user_id=user_id, title="原始会话")
        db.add(sess)
        db.flush()
        mat = models.Material(
            session_id=sess.id, kind=models.MaterialKind.text, source="粘贴文本",
            parsed_chars=6, meta={"a": 1}, content_hash="h" * 64,
        )
        db.add(mat)
        db.flush()
        parsed_text = "这是解析后的资料文本"
        mat.parsed_text_path = str(write_parsed_text(sess.id, mat.id, parsed_text))

        plan = models.Plan(
            session_id=sess.id, version=3,
            content={"intent": {"topic": "测试主题"}, "knowledge_map": {"nodes": []}},
        )
        db.add(plan)
        db.flush()
        k1 = models.KnowledgeNode(plan_id=plan.id, sid="k1", name="根节点", difficulty=2, must_learn=True)
        db.add(k1)
        db.flush()
        k2 = models.KnowledgeNode(plan_id=plan.id, sid="k2", name="子节点", difficulty=4, parent_id=k1.id)
        db.add(k2)
        db.flush()
        db.add(models.Resource(node_id=k2.id, url="https://example.com", title="示例",
                               type=models.ResourceType.doc, verified=models.VerifyLabel.ok, note="n"))
        db.add(models.Progress(plan_id=plan.id, node_id=k2.id,
                               status=models.ProgressStatus.learning, mastery=0.5))
        from datetime import date

        db.add(models.LearningLog(plan_id=plan.id, date=date(2026, 7, 1), minutes=42, note="打卡"))
        quiz = models.Quiz(plan_id=plan.id, node_id=k1.id, type=models.QuizType.choice,
                           payload={"q": "1+1=?", "answer": "2"})
        db.add(quiz)
        db.flush()
        db.add(models.QuizAttempt(quiz_id=quiz.id, user_answer={"a": "2"}, correct=True))
        unit = models.StudyUnit(plan_id=plan.id, node_id=k2.id, stage_index=0, title="单元一",
                                prerequisites=["p"], objectives=["o"], estimated_time="30min", order=0)
        db.add(unit)
        db.flush()
        unit_md = "# 单元一\n\n这是单元内容" * 3
        path = write_unit_content(sess.id, unit.id, unit_md)
        db.add(models.StudyMaterial(unit_id=unit.id, content_md_path=str(path),
                                    size=len(unit_md), resources=[{"url": "u"}]))
        db.add(models.LlmProviderProfile(user_id=user_id, name="默认", base_url="https://api.example.com",
                                         api_key="sk-secret-should-not-export", model="m", is_active=True))
        db.commit()
        return {
            "session_id": sess.id, "plan_id": plan.id, "unit_id": unit.id,
            "parsed_text": parsed_text, "unit_md": unit_md,
        }
    finally:
        db.close()


def _export(client, headers) -> dict:
    r = client.get("/api/backup/export", headers=headers)
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers.get("content-disposition", "")
    return json.loads(r.content)


def _import(client, headers, payload: bytes | dict):
    blob = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return client.post("/api/backup/import", files={"file": ("backup.json", blob, "application/json")}, headers=headers)


def _wipe_all():
    """库 + 数据目录全清（模拟换机还原）。"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    p = Path(settings.data_dir)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    p.mkdir(parents=True, exist_ok=True)


# --- roundtrip ---


def test_backup_roundtrip_full_fidelity(client, auth_headers):
    _mk_admin()  # env token -> 首个 admin（即数据属主）
    db = SessionLocal()
    from app.db.models import User

    user_id = db.query(User).filter_by(username="admin").one().id
    db.close()
    seeded = _seed_rich_data(user_id)

    exported = _export(client, auth_headers)
    assert exported["format"] == "deep-tutor-backup" and exported["version"] == 1
    raw = json.dumps(exported, ensure_ascii=False)
    assert "sk-secret-should-not-export" not in raw  # 凭证边界：api_key 绝不导出
    assert exported["provider_profiles"][0]["name"] == "默认"
    assert exported["provider_profiles"][0]["is_active"] is True

    _wipe_all()
    _mk_admin()  # 还原目标机上的新管理员

    r = _import(client, auth_headers, exported)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["sessions"] == 1 and body["plans"] == 1 and body["materials"] == 1
    assert body["knowledge_nodes"] == 2 and body["study_units"] == 1 and body["quizzes"] == 1
    assert body["progress"] == 1 and body["learning_logs"] == 1 and body["provider_profiles"] == 1

    db = SessionLocal()
    try:
        from app.core.storage import read_unit_content
        from app.db import models

        sess = db.query(models.Session).one()
        assert sess.title == "原始会话"  # wipe 后 id 复用属正常（追加语义由专门测试覆盖）
        mat = db.query(models.Material).one()
        assert Path(mat.parsed_text_path).read_text(encoding="utf-8") == seeded["parsed_text"]
        plan = db.query(models.Plan).one()
        assert plan.version == 3 and plan.content["intent"]["topic"] == "测试主题"
        k1 = db.query(models.KnowledgeNode).filter_by(sid="k1").one()
        k2 = db.query(models.KnowledgeNode).filter_by(sid="k2").one()
        assert k2.parent_id == k1.id  # 树结构按 sid 重关联
        prog = db.query(models.Progress).one()
        assert prog.node_id == k2.id and prog.status == models.ProgressStatus.learning and prog.mastery == 0.5
        assert db.query(models.LearningLog).one().minutes == 42
        quiz = db.query(models.Quiz).one()
        assert quiz.node_id == k1.id and quiz.attempts[0].correct is True
        unit = db.query(models.StudyUnit).one()
        assert unit.node_id == k2.id
        sm = db.query(models.StudyMaterial).one()
        assert read_unit_content(sm.content_md_path) == seeded["unit_md"]  # 内容文件重落盘可读
        profile = db.query(models.LlmProviderProfile).one()
        assert profile.api_key == "" and profile.is_active is False  # 空 key + 未激活（补填后手动激活）
    finally:
        db.close()


def test_backup_roundtrip_oversized_plan_overflow_file(client, auth_headers):
    """大方案（>512KB 溢出文件）导出内联回读，导入后重新溢出落盘且内容保真。"""
    _mk_admin()
    db = SessionLocal()
    from app.db.models import User

    user_id = db.query(User).filter_by(username="admin").one().id
    big_topic = "超长" * 300_000  # ~600KB，确保溢出
    from app.db import models as m

    sess = m.Session(user_id=user_id, title="大方案会话")
    db.add(sess)
    db.flush()
    db.add(m.Plan(session_id=sess.id, version=1, content={"intent": {"topic": big_topic}}))
    db.commit()
    db.close()

    exported = _export(client, auth_headers)
    assert exported["sessions"][0]["plans"][0]["content"]["intent"]["topic"] == big_topic

    _wipe_all()
    _mk_admin()
    r = _import(client, auth_headers, exported)
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        plan = db.query(m.Plan).one()
        assert plan.content == {} and plan.content_path  # 重新溢出
        assert json.loads(Path(plan.content_path).read_text(encoding="utf-8"))["intent"]["topic"] == big_topic
    finally:
        db.close()


# --- 语义与校验 ---


def test_import_is_additive_not_overwrite(client, auth_headers):
    _mk_admin()
    db = SessionLocal()
    from app.db.models import User

    user_id = db.query(User).filter_by(username="admin").one().id
    db.close()
    _seed_rich_data(user_id)
    exported = _export(client, auth_headers)

    # 不 wipe 直接导入 -> 追加副本（UI 有提示）
    r = _import(client, auth_headers, exported)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        from app.db import models

        assert db.query(models.Session).count() == 2
        assert db.query(models.Plan).count() == 2
    finally:
        db.close()


def test_import_rejects_garbage(client, auth_headers):
    _mk_admin()
    r = _import(client, auth_headers, b"{not json")
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_backup"
    r = _import(client, auth_headers, {"format": "other-tool", "version": 1, "sessions": []})
    assert r.status_code == 400
    r = _import(client, auth_headers, {"format": "deep-tutor-backup", "version": 999, "sessions": []})
    assert r.status_code == 400 and "过新" in r.json()["error"]["message"]


def test_import_empty_backup_ok(client, auth_headers):
    _mk_admin()
    r = _import(client, auth_headers, {"format": "deep-tutor-backup", "version": 1, "sessions": []})
    assert r.status_code == 200 and r.json()["sessions"] == 0


def test_export_isolates_current_user(client, auth_headers):
    _mk_admin()
    db = SessionLocal()
    from app.db import models
    from app.db.models import User

    admin = db.query(User).filter_by(username="admin").one()
    other = User(name="other", username="other", password_hash="x", is_admin=False)
    db.add(other)
    db.flush()
    db.add(models.Session(user_id=admin.id, title="管理员的会话"))
    db.add(models.Session(user_id=other.id, title="别人的会话"))
    db.commit()
    db.close()

    exported = _export(client, auth_headers)
    titles = [s["title"] for s in exported["sessions"]]
    assert titles == ["管理员的会话"]  # 仅当前用户数据


def test_import_profile_name_conflict_skipped(client, auth_headers):
    _mk_admin()
    db = SessionLocal()
    from app.db.models import User

    user_id = db.query(User).filter_by(username="admin").one().id
    db.close()
    _seed_rich_data(user_id)  # 含「默认」档案
    exported = _export(client, auth_headers)
    # 不 wipe：档案名冲突 -> 跳过并记录
    r = _import(client, auth_headers, exported)
    assert r.status_code == 200
    assert r.json()["provider_profiles"] == 0
    assert any("默认" in s for s in r.json()["skipped"])


def test_backup_requires_auth(client):
    assert client.get("/api/backup/export").status_code == 401

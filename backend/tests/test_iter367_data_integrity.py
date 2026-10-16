"""iter 367 数据完整性加固回归测试：

- progress 乐观锁（并发提交同节点不丢加权增量）
- 备份 roundtrip 保真 study_units.node_sids（iter 348 字段此前漏导）
- 备份内重复 sid 不再撞唯一约束 500（去重跳过）
- 备份导入供应商档案条数上限
- 维护清理 rmtree 前即时重查（快照竞态不误删活跃目录）
- 材料流预算中断仍执行收尾清理（旧文件不残留、index 重写）
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from app.core.settings import settings
from app.db.base import SessionLocal


# ---------------- progress 乐观锁 ----------------

def test_progress_concurrent_update_replays_on_latest(client, auth_headers, monkeypatch):
    """并发写发生在读取与条件更新之间 -> 乐观锁失配重放，最终值基于并发后的最新值。"""
    from test_api_progress import _make_plan

    from app.api import progress as progress_mod
    from app.tools.record_progress import compute_mastery as orig_compute

    plan_id = _make_plan(client, auth_headers, monkeypatch)
    r = client.post(
        "/api/progress", json={"plan_id": plan_id, "node_id": "k1", "status": "learning"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    fired = [False]

    def fake_compute(old, status, score=None):
        if not fired[0]:
            fired[0] = True
            # 模拟并发标签页：在本线程「读后写前」窗口内直接改写 DB 行
            db = SessionLocal()
            try:
                from app.db import models

                row = (
                    db.query(models.Progress)
                    .join(models.KnowledgeNode, models.Progress.node_id == models.KnowledgeNode.id)
                    .filter(models.KnowledgeNode.sid == "k1", models.Progress.plan_id == plan_id)
                    .one()
                )
                row.mastery = min(1.0, row.mastery + 0.3)
                db.commit()
            finally:
                db.close()
        return orig_compute(old, status, score)

    monkeypatch.setattr(progress_mod, "compute_mastery", fake_compute)
    r2 = client.post(
        "/api/progress", json={"plan_id": plan_id, "node_id": "k1", "status": "done", "quiz_score": 1.0},
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text

    db = SessionLocal()
    try:
        from app.db import models

        row = (
            db.query(models.Progress)
            .join(models.KnowledgeNode, models.Progress.node_id == models.KnowledgeNode.id)
            .filter(models.KnowledgeNode.sid == "k1", models.Progress.plan_id == plan_id)
            .one()
        )
        db_mastery = row.mastery
    finally:
        db.close()
    # 最终值应基于并发后的 mastery（fake 第一次拿到的 old 是陈旧值）：
    # 模拟并发值为首答 mastery + 0.3，最终 = compute(并发值, learned, 1.0)
    first = orig_compute(0.0, "learning", None)
    concurrent = min(1.0, first + 0.3)
    assert abs(db_mastery - orig_compute(concurrent, "done", 1.0)) < 1e-9


# ---------------- 备份：node_sids roundtrip / 重复 sid / 档案上限 ----------------

def _minimal_backup(**overrides) -> dict:
    plan = {
        "title": "p",
        "version": 1,
        "content": {},
        "knowledge_nodes": [
            {"sid": "k1", "name": "A", "difficulty": 1, "must_learn": True},
        ],
        "resources": [],
        "progress": [],
        "learning_logs": [],
        "quizzes": [],
        "study_units": [
            {"title": "u1", "node_sids": ["k1"], "order": 1, "content_md": "# hi"},
        ],
    }
    data = {
        "format": "deep-tutor-backup",
        "version": 1,
        "user": {"name": "x"},
        "sessions": [{"title": "s", "materials": [], "plans": [plan]}],
        "provider_profiles": [],
        "prefs": {},
        "notes": [],
    }
    data.update(overrides)
    return data


def _mk_user(name: str) -> int:
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(name=name, username=name, password_hash="x", is_admin=False)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def test_backup_roundtrip_preserves_unit_node_sids(client, auth_headers):
    """study_units.node_sids 导出/导入往返保真（此前漏导 -> scope.node_ids 重生成失效）。"""
    from app.core.backup import build_export, restore_backup
    from app.db import models

    uid = _mk_user("ns_owner")
    db = SessionLocal()
    try:
        sess = models.Session(user_id=uid, title="s")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, content={}, version=1)
        db.add(plan)
        db.flush()
        node = models.KnowledgeNode(plan_id=plan.id, sid="k9", name="N", difficulty=1, must_learn=True)
        db.add(node)
        db.flush()
        unit = models.StudyUnit(plan_id=plan.id, node_id=node.id, title="u", order=1, node_sids=["k9"])
        db.add(unit)
        db.commit()
        owner = db.get(models.User, uid)
        exported = build_export(db, owner)
    finally:
        db.close()

    uid2 = _mk_user("ns_target")
    db = SessionLocal()
    try:
        target = db.get(models.User, uid2)
        summary = restore_backup(db, target, exported)
        assert summary["study_units"] == 1
        unit2 = db.query(models.StudyUnit).filter(models.StudyUnit.plan_id != plan.id).one()
        assert unit2.node_sids == ["k9"]
    finally:
        db.close()


def test_backup_duplicate_sids_no_integrity_error(client, auth_headers):
    """备份内同 sid 重复出现（损坏/手工编辑）-> 去重跳过，不再整包 500。"""
    from app.core.backup import restore_backup

    data = _minimal_backup()
    plan = data["sessions"][0]["plans"][0]
    plan["knowledge_nodes"].append(
        {"sid": "k1", "name": "A-dup", "difficulty": 2, "must_learn": True, "note": "dup"}
    )
    plan["knowledge_nodes"][0]["note"] = "first"
    plan["progress"] = [
        {"node_sid": "k1", "status": "learning", "mastery": 0.3},
        {"node_sid": "k1", "status": "learned", "mastery": 0.9},
    ]

    uid = _mk_user("dup_target")
    db = SessionLocal()
    try:
        from app.db import models

        user = db.get(models.User, uid)
        summary = restore_backup(db, user, data)
        assert summary["node_notes"] == 1
        assert summary["progress"] == 1
        assert db.query(models.NodeNote).filter_by(user_id=uid).count() == 1
        assert db.query(models.Progress).count() == 1
    finally:
        db.close()


def test_backup_profiles_capped_at_50(client, auth_headers):
    """构造备份带 60 个不同名档案 -> 只导入 50 个（iter 246 配额族漏项）。"""
    from app.core.backup import restore_backup

    data = _minimal_backup(
        provider_profiles=[{"name": f"p{i}", "base_url": "https://x", "model": "m"} for i in range(60)]
    )
    uid = _mk_user("cap_target")
    db = SessionLocal()
    try:
        from app.db import models

        user = db.get(models.User, uid)
        summary = restore_backup(db, user, data)
        assert summary["provider_profiles"] == 50
        assert db.query(models.LlmProviderProfile).filter_by(user_id=uid).count() == 50
        assert any("供应商档案达上限" in s for s in summary["skipped"])
    finally:
        db.close()


# ---------------- 维护清理即时重查 ----------------

def test_cleanup_rechecks_db_before_rmtree(client, auth_headers):
    """快照查询竞态（返回空集）下，活跃会话/run 目录因即时重查而保留。"""
    from app.core.maintenance import cleanup_orphan_run_dirs, cleanup_orphan_session_dirs
    from app.db import models

    sid = client.post("/api/sessions", json={"title": "t"}, headers=auth_headers).json()["session_id"]
    sess_root = Path(settings.data_dir) / "sessions"
    (sess_root / str(sid)).mkdir(parents=True, exist_ok=True)

    db = SessionLocal()
    try:
        uid = db.get(models.Session, sid).user_id
        thread = models.Thread(user_id=uid, title="t")
        db.add(thread)
        db.flush()
        run = models.Run(
            thread_id=thread.id, user_id=uid, status=models.RunStatus.running, trace_id="t"
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        rid = run.id
    finally:
        db.close()
    runs_root = Path(settings.data_dir) / "runs"
    (runs_root / str(rid)).mkdir(parents=True, exist_ok=True)

    # 模拟快照竞态：.all() 全部返回空（重查走 .first()/.scalar()/.one_or_none() 不受影响）
    with patch("sqlalchemy.orm.Query.all", return_value=[]):
        removed_sessions = cleanup_orphan_session_dirs()
        removed_runs = cleanup_orphan_run_dirs()

    assert removed_sessions == 0 and removed_runs == 0
    assert (sess_root / str(sid)).exists()  # 活会话目录未被误删
    assert (runs_root / str(rid)).exists()  # running run 的 scratch 未被误删


# ---------------- 材料流预算中断仍收尾清理 ----------------

def test_materials_budget_exceeded_still_cleans_old_files(client, auth_headers, monkeypatch):
    """预算超限 return 路径执行收尾：旧单元文件清理 + index 重写（此前成永久孤儿）。"""
    from test_api_plans_materials import _MaterialsProvider, _make_plan
    from test_api_plans_materials_sse import _parse_sse

    from app.api import plans as plans_mod
    from app.db import models

    plan_id = _make_plan(client, auth_headers)
    spec_json = (
        '{"title":"m","units":['
        '{"title":"u1","objectives":["o"],"prerequisites":["无"],"estimated_time":"10 min","content_md":"# a"},'
        '{"title":"u2","objectives":["o"],"prerequisites":["无"],"estimated_time":"10 min","content_md":"# b"}]}'
    )
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: _MaterialsProvider([_resp_json(spec_json)]))
    r = client.post(f"/api/plans/{plan_id}/materials", json={"depth": "full"}, headers=auth_headers)
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        old_paths = [m.content_md_path for m in db.query(models.StudyMaterial).all()]
        assert old_paths, "前置：首轮生成应有内容文件"
        sid = db.query(models.Plan).get(plan_id).session_id
    finally:
        db.close()

    class _BoomBudget:
        def __init__(self, limit=None):
            self.spent = 0.0
            self.limit = limit

        def add(self, cost):
            self.spent += float(cost or 0)

        def exceeded(self):
            return True

    monkeypatch.setattr(plans_mod, "BudgetTracker", _BoomBudget)
    monkeypatch.setattr(
        plans_mod, "get_provider",
        lambda *a, **k: _MaterialsProvider([_resp_json(spec_json)]),
    )
    r2 = client.post(
        f"/api/plans/{plan_id}/materials",
        json={"depth": "full", "refresh": True},
        headers={**auth_headers, "Accept": "text/event-stream"},
    )
    events = _parse_sse(r2.text)
    names = [e for e, _ in events]
    assert "error" in names
    err = dict(events)["error"] if isinstance(dict(events).get("error"), dict) else None
    assert err and err.get("code") == "budget_exceeded"

    # 收尾清理已执行：旧内容文件删除、index.md 重写
    assert not any(Path(p).exists() for p in old_paths)
    assert (Path(settings.data_dir) / "sessions" / str(sid) / "materials" / "index.md").exists()


def _resp_json(payload: str):
    from app.llm.base import LLMResponse, LLMUsage

    return LLMResponse(
        content=payload,
        usage=LLMUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-5"),
    )

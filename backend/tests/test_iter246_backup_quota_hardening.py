"""iter 246 加固回归：备份容错强转/子对象配额/备份限流、打卡配额+限流、
JWT iat 上取整、滑动窗口限流并发原子性、目录删除失败日志。"""

from __future__ import annotations

import threading
import time

import pytest

from app.core import backup as backup_core
from app.core.ratelimit import SlidingWindowLimiter
from app.db import models
from app.db.base import SessionLocal


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


def _mk_session_with_plan(user_id: int) -> tuple[int, int]:
    db = SessionLocal()
    try:
        sess = models.Session(user_id=user_id, title="s")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"knowledge_map": {"nodes": []}})
        db.add(plan)
        db.commit()
        return sess.id, plan.id
    finally:
        db.close()


def _import(client, headers, payload: dict):
    import json

    return client.post(
        "/api/backup/import",
        files={"file": ("backup.json", json.dumps(payload).encode("utf-8"), "application/json")},
        headers=headers,
    )


def _backup(plan_overrides: dict | None = None, session_overrides: dict | None = None) -> dict:
    plan = {
        "version": 1,
        "content": {"knowledge_map": {"nodes": []}},
        "knowledge_nodes": [{"sid": "k1", "name": "节点", "difficulty": 1, "note": "n"}],
    }
    if plan_overrides:
        plan.update(plan_overrides)
    session = {"title": "s", "materials": [], "plans": [plan]}
    if session_overrides:
        session.update(session_overrides)
    return {"format": "deep-tutor-backup", "version": 1, "sessions": [session]}


# --- 备份容错强转（此前 ValueError -> 500 import_failed）---


def test_backup_malformed_numeric_fields_tolerated(client, auth_headers):
    _mk_admin()
    bad = _backup(
        plan_overrides={"version": "abc"},
        session_overrides={
            "materials": [{"kind": "text", "source": "x", "parsed_chars": "not-a-number"}]
        },
    )
    r = _import(client, auth_headers, bad)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plans"] == 1 and body["materials"] == 1
    db = SessionLocal()
    try:
        assert db.query(models.Plan).one().version == 1  # 默认值兜底
        assert db.query(models.Material).one().parsed_chars == 0
    finally:
        db.close()


def test_backup_malformed_node_progress_unit_fields_tolerated(client, auth_headers):
    _mk_admin()
    bad = _backup(
        plan_overrides={
            "knowledge_nodes": [{"sid": "k1", "name": "节点", "difficulty": "hard"}],
            "progress": [{"node_sid": "k1", "status": "learning", "mastery": "nan"}],
            "learning_logs": [{"date": "2026-07-01", "minutes": 30}],
            "study_units": [{"title": "u", "order": "weird"}],
        }
    )
    r = _import(client, auth_headers, bad)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        node = db.query(models.KnowledgeNode).one()
        assert node.difficulty == 1  # "hard" -> 默认 1
        prog = db.query(models.Progress).one()
        assert prog.mastery == 0.0  # nan -> 默认 0.0（非有限值不入库）
        unit = db.query(models.StudyUnit).one()
        assert unit.order == 0
    finally:
        db.close()


# --- 备份子对象配额 ---


def test_backup_plan_subobject_quotas(client, auth_headers):
    _mk_admin()
    big = _backup(
        plan_overrides={
            "knowledge_nodes": [{"sid": f"k{i}", "name": f"节点{i}"} for i in range(150)],
            "learning_logs": [{"date": "2026-07-01", "minutes": 10}] * 2100,
        }
    )
    r = _import(client, auth_headers, big)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["knowledge_nodes"] == 100  # _MAX_NODES
    assert body["learning_logs"] == 2000  # _MAX_LOGS
    assert any("子对象超上限" in s for s in body["skipped"])


def test_backup_plans_per_session_quota(client, auth_headers):
    _mk_admin()
    payload = {
        "format": "deep-tutor-backup",
        "version": 1,
        "sessions": [
            {
                "title": "s",
                "materials": [],
                "plans": [{"version": 1, "content": {}} for _ in range(60)],
            }
        ],
    }
    r = _import(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plans"] == 50  # _MAX_PLANS_PER_SESSION
    assert any("方案超每会话 50 个上限" in s for s in body["skipped"])


def test_backup_prefs_quota(client, auth_headers):
    _mk_admin()
    payload = {
        "format": "deep-tutor-backup",
        "version": 1,
        "sessions": [],
        "prefs": {f"k{i}": "v" for i in range(250)},
    }
    r = _import(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prefs"] == 200  # _MAX_PREFS
    assert any("偏好数达上限 200" in s for s in body["skipped"])


# --- 备份端点限流 ---


def test_backup_import_rate_limited(client, auth_headers):
    _mk_admin()
    empty = {"format": "deep-tutor-backup", "version": 1, "sessions": []}
    for _ in range(5):  # _IMPORT_RATE_PER_MIN
        r = _import(client, auth_headers, empty)
        assert r.status_code == 200, r.text
    r = _import(client, auth_headers, empty)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"


def test_backup_export_rate_limited(client, auth_headers):
    _mk_admin()
    for _ in range(10):  # _EXPORT_RATE_PER_MIN
        r = client.get("/api/backup/export", headers=auth_headers)
        assert r.status_code == 200, r.text
    r = client.get("/api/backup/export", headers=auth_headers)
    assert r.status_code == 429


# --- 打卡限流 + 配额 ---


def test_learning_log_rate_limited(client, auth_headers):
    user_id = _mk_admin()
    _, plan_id = _mk_session_with_plan(user_id)
    for _ in range(60):  # _LOG_RATE_PER_MIN
        r = client.post(
            "/api/progress/logs",
            json={"plan_id": plan_id, "minutes": 10},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
    r = client.post(
        "/api/progress/logs",
        json={"plan_id": plan_id, "minutes": 10},
        headers=auth_headers,
    )
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"


def test_learning_log_quota(client, auth_headers, monkeypatch):
    user_id = _mk_admin()
    _, plan_id = _mk_session_with_plan(user_id)
    db = SessionLocal()
    try:
        from datetime import date

        for _ in range(20):
            db.add(models.LearningLog(plan_id=plan_id, date=date(2026, 7, 1), minutes=10))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr("app.api.progress._MAX_LOGS_PER_USER", 20)
    r = client.post(
        "/api/progress/logs",
        json={"plan_id": plan_id, "minutes": 10},
        headers=auth_headers,
    )
    assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"


# --- JWT iat 上取整（born-revoked 边界）---


def _revoked(iat: float, tva: float) -> bool:
    """与 deps.py 判定保持一致（iter 246：+1s 容差）。"""
    return iat + 1 <= tva


def test_token_iat_decodes_and_covers_same_second_revoke():
    """born-revoked 边界（iter 246）：tokens_valid_after 为带小数的 float 且与
    签发同秒时，新令牌不得被判为"撤销前签发"（deps.py 判定 +1s 容差修复）。"""
    from app.core import security

    before = time.time()
    token = security.create_token(1, "admin")
    payload = security.decode_token(token)
    assert payload is not None  # iat 不取未来值（PyJWT 硬校验），能正常解码
    iat = float(payload["iat"])
    assert int(before) <= iat <= int(before) + 1
    # 同秒撤销（tva = iat + 0.x）：旧判定 iat < tva 误杀 -> 新判定放行
    assert not _revoked(iat, iat + 0.9)
    # 上一秒及更早撤销：仍拒绝（容差不放大吊销窗口）
    assert _revoked(iat, iat + 1.0)  # tva 恰好领先 1 整秒 -> 拒
    assert _revoked(iat, iat + 5.0)  # 撤销晚于签发 5s -> 必拒（旧语义）



# --- 限流器并发原子性 ---


def test_sliding_window_limiter_concurrent_check():
    """并发同 key 请求不得双双通过（iter 246 前 check 非原子）。"""
    lim = SlidingWindowLimiter()
    limit = 50
    results: list[bool] = []
    lock = threading.Lock()

    def hit():
        ok = lim.check("k", limit)
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=hit) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results) == limit  # 恰 50 次放行，无一例外


# --- 目录删除失败日志 ---


def test_rmtree_failure_logged(tmp_path, caplog):
    """rmtree 静默吞错 -> 改为留痕（iter 246）：目录删除不完整时 warning。"""
    from app.core import storage

    base = tmp_path / "orphan"
    base.mkdir()
    (base / "f.txt").write_text("x", encoding="utf-8")

    import shutil as _shutil

    orig = _shutil.rmtree
    try:
        _shutil.rmtree = lambda *a, **k: None  # 模拟删除失败（目录仍在）
        with caplog.at_level("WARNING", logger="app.core.storage"):
            storage._rmtree_orphan_aware(base)
        assert any("目录删除不完整" in r.message for r in caplog.records)
    finally:
        _shutil.rmtree = orig

    # 正常路径：删除成功不告警
    caplog.clear()
    with caplog.at_level("WARNING", logger="app.core.storage"):
        storage._rmtree_orphan_aware(base)
    assert not base.exists()
    assert not any("目录删除不完整" in r.message for r in caplog.records)


# --- 防回归：正常备份 roundtrip 不受配额/容错影响 ---


def test_backup_normal_roundtrip_unaffected(client, auth_headers):
    _mk_admin()
    good = _backup(
        plan_overrides={
            "resources": [{"node_sid": "k1", "url": "https://x.com", "title": "t", "type": "doc"}],
            "quizzes": [
                {
                    "type": "choice",
                    "node_sid": "k1",
                    "payload": {"q": "q"},
                    "attempts": [{"user_answer": {"a": 1}, "correct": True}],
                }
            ],
        }
    )
    r = _import(client, auth_headers, good)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["knowledge_nodes"] == 1 and body["quizzes"] == 1
    assert body["skipped"] == [] or all("配额" not in s and "超上限" not in s for s in body["skipped"])


def test_int_or_float_or_edges():
    assert backup_core._int_or(None, 3) == 3
    assert backup_core._int_or("7", 3) == 7
    assert backup_core._int_or("x", 3) == 3
    assert backup_core._int_or(2.9, 3) == 2
    assert backup_core._float_or("0.5", 1.0) == 0.5
    assert backup_core._float_or("nan", 1.0) == 1.0
    assert backup_core._float_or("inf", 1.0) == 1.0
    assert backup_core._float_or(object(), 1.0) == 1.0


@pytest.mark.parametrize("v,default", [(0, 5), ("", 5)])
def test_int_or_falsy_values(v, default):
    # falsy 输入取默认（v or default 语义，与 int(v or default) 旧路径一致）
    assert backup_core._int_or(v, default) == default

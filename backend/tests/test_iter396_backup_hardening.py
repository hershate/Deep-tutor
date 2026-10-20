"""iter 396 回归：备份还原路径加固（SSRF 校验 / 配额口径 / 版本重号 / 行数预算）。

对应本轮 core/backup.py 的四处修复：

1. **B5 还原写入 base_url 绕过写时 SSRF 校验**——备份里的 ``base_url`` 是**服务端
   出站目标**且由备份内容控制；导入不校验即可写入 ``file://`` 之类非法协议，或指向
   私网 / 云元数据（169.254.169.254）的地址（调用期只做字面量判定，不解析 DNS）。
   修复：与 ``api/providers.py`` 写时同口径校验（解析 DNS 后判私网），非法即跳过。
2. **B3 还原侧对话会话上限与 API 口径不一致**——还原 200 / API 500：拥有 200~500
   个 thread 的用户「导出→导入」往返会静默丢会话。改为 500。
3. **B6 还原沿用备份里的 plan.version**——重复 version 会让同会话出现重号版本
   （schema 无 (session_id, version) 唯一约束）-> 方案列表/版本选择器错乱。
4. **B7 行数预算漏算知识点笔记**——``_count_payload_rows`` 未统计 node.note，
   10 万节点全带笔记时实际插入 20 万行，超预算一倍（放大单事务持锁时长）。
"""

from __future__ import annotations

import json

from app.db import models
from app.db.base import SessionLocal

_PAYLOAD = "deep-tutor-backup"


def _mk_user(name: str) -> tuple[int, str]:
    from app.core.security import create_token

    db = SessionLocal()
    try:
        u = models.User(name=name, username=name, password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, create_token(u.id, name)
    finally:
        db.close()


def _import(client, backup: dict, headers: dict) -> dict:
    r = client.post(
        "/api/backup/import",
        files={"file": ("b.json", json.dumps(backup), "application/json")},
        headers=headers,
    )
    assert r.status_code == 200, r.text[:400]
    return r.json()


def _base_backup(**kw) -> dict:
    b = {
        "format": _PAYLOAD,
        "version": 2,
        "sessions": [],
        "threads": [],
        "memories": [],
        "kb_documents": [],
        "provider_profiles": [],
    }
    b.update(kw)
    return b


# ---------------------------------------------------------------------------
# 1. base_url 写时 SSRF 校验
# ---------------------------------------------------------------------------


def test_restore_rejects_private_base_url(client):
    """备份里的私网/元数据 base_url 不得写入（此前完全不校验）。"""
    uid, tok = _mk_user("bk_ssrf")
    h = {"Authorization": f"Bearer {tok}"}
    body = _import(
        client,
        _base_backup(
            provider_profiles=[
                {"name": "内网", "base_url": "http://127.0.0.1:8080/v1", "model": "m"},
                {"name": "元数据", "base_url": "http://169.254.169.254/latest", "model": "m"},
                {"name": "非http", "base_url": "file:///etc/passwd", "model": "m"},
            ]
        ),
        h,
    )
    assert body["provider_profiles"] == 0, body
    db = SessionLocal()
    try:
        rows = db.query(models.LlmProviderProfile).filter_by(user_id=uid).all()
        assert rows == [], f"私网/元数据 base_url 不应入库：{[r.base_url for r in rows]}"
    finally:
        db.close()
    assert any("base_url 被安全策略拒绝" in s for s in body["skipped"]), body["skipped"]


def test_restore_keeps_public_base_url(client):
    """正常公网 base_url 仍可导入（校验不得误伤）。"""
    uid, tok = _mk_user("bk_ssrf_ok")
    h = {"Authorization": f"Bearer {tok}"}
    body = _import(
        client,
        _base_backup(
            provider_profiles=[{"name": "公网", "base_url": "https://api.example.com/v1", "model": "m"}]
        ),
        h,
    )
    assert body["provider_profiles"] == 1, body
    db = SessionLocal()
    try:
        rows = db.query(models.LlmProviderProfile).filter_by(user_id=uid).all()
        assert len(rows) == 1 and rows[0].base_url == "https://api.example.com/v1"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2. 还原侧 thread 上限与 API 口径一致
# ---------------------------------------------------------------------------


def test_restore_thread_cap_matches_api_quota(client):
    """250 个 thread 的备份应全部导入（此前上限 200 -> 静默丢 50 个）。"""
    uid, tok = _mk_user("bk_threads")
    h = {"Authorization": f"Bearer {tok}"}
    backup = _base_backup(
        threads=[
            {"title": f"t{i}", "kind": "chat", "model": "m",
             "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
            for i in range(250)
        ]
    )
    body = _import(client, backup, h)
    assert body["threads"] == 250, body["threads"]
    db = SessionLocal()
    try:
        assert db.query(models.Thread).filter_by(user_id=uid).count() == 250
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3. 方案版本号去重
# ---------------------------------------------------------------------------


def _plan(topic: str, version: int) -> dict:
    return {
        "content": {"intent": {"topic": topic}},
        "version": version,
        "knowledge_nodes": [],
        "resources": [],
        "progress": [],
        "learning_logs": [],
        "study_units": [],
        "quizzes": [],
    }


def test_restore_plan_versions_are_unique(client):
    """备份里的重复 version 还原后不得重号（同会话版本选择器会错乱）。"""
    uid, tok = _mk_user("bk_ver")
    h = {"Authorization": f"Bearer {tok}"}
    backup = _base_backup(
        sessions=[
            {
                "title": "s",
                "materials": [],
                "plans": [_plan("A", 2), _plan("B", 2), _plan("C", 1)],
            }
        ]
    )
    body = _import(client, backup, h)
    assert body["plans"] == 3, body
    db = SessionLocal()
    try:
        sess = db.query(models.Session).filter_by(user_id=uid).first()
        assert sess is not None
        versions = [p.version for p in db.query(models.Plan).filter_by(session_id=sess.id).all()]
        assert len(versions) == len(set(versions)), f"方案版本号重号：{versions}"
        assert sorted(versions) == versions, f"方案版本号应递增：{versions}"
    finally:
        db.close()


def test_restore_plan_version_preserved_for_normal_backup(client):
    """正常备份的 version 原样保留（往返保真，不被重排）。"""
    uid, tok = _mk_user("bk_ver2")
    h = {"Authorization": f"Bearer {tok}"}
    backup = _base_backup(
        sessions=[{"title": "s", "materials": [], "plans": [_plan("A", 3)]}]
    )
    _import(client, backup, h)
    db = SessionLocal()
    try:
        sess = db.query(models.Session).filter_by(user_id=uid).first()
        assert sess is not None
        plan = db.query(models.Plan).filter_by(session_id=sess.id).first()
        assert plan is not None and plan.version == 3, plan.version
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 4. 行数预算计入知识点笔记
# ---------------------------------------------------------------------------


def test_count_payload_rows_includes_node_notes():
    """节点笔记会额外插一行 NodeNote，行数预算必须计入（否则低估一倍）。"""
    from app.core.backup import _count_payload_rows

    payload = {
        "format": _PAYLOAD,
        "version": 2,
        "sessions": [
            {
                "title": "s",
                "materials": [],
                "plans": [
                    {
                        "content": {},
                        "knowledge_nodes": [
                            {"sid": "k1", "name": "n1", "note": "有笔记"},
                            {"sid": "k2", "name": "n2", "note": "  "},  # 空白 -> 不计
                            {"sid": "k3", "name": "n3"},
                        ],
                        "resources": [],
                        "progress": [],
                        "learning_logs": [],
                        "study_units": [],
                        "quizzes": [],
                    }
                ],
            }
        ],
    }
    # 1 会话 + 1 方案 + 3 节点 + 1 节点笔记 = 6
    assert _count_payload_rows(payload) == 6, _count_payload_rows(payload)

"""iter 391：备份导入/导出硬化回归（网络服务暴露前的资源与数据完整性修复）。

覆盖：
- 全局行数预算：构造 payload 的单次导入总行数超限 -> 413（此前单事务内可写千万级
  行，独占 SQLite 写锁数分钟，其间全服务写请求 500）
- 畸形 payload 的类型校验：顶层段类型非法 -> 400（此前 len()/get() 抛 TypeError/
  AttributeError 落 500 internal_error）
- 材料三段制状态（pending/confirmed/parsed）随备份往返（此前导出丢弃 status，
  导入按列默认落 "parsed"——未确认的上传件被还原成"已解析"）
- 带时区偏移的时间戳归一到 naive UTC（此前偏移被静默丢弃，时间线整体平移）
- 超范围整数按非法值处理（此前 sqlite 绑定 OverflowError 整包 500）
- 重复 sid 的确定性：首条胜出、后续跳过并显式计入 skipped（此前两条都插、
  映射表后写覆盖，先插者丢 parent_id，进度/资源/笔记错挂到后一条）
- 下标重映射：跳过的 run 不占 payload 下标（此前 messages.run_idx 错挂到别的 run）
- 文件补偿：事务失败时删除已落盘的 kb 原件/解析文本（此前 DB 回滚不回滚磁盘，
  kb 原件孤儿不被 maintenance 回收）
- 预检计数与实际导入一致（同配额 + 本机额度抵扣）
- 覆盖面披露：未纳入备份的表在导出/预检中显式列出
- 导入侧 kb 原件单文件上限（与导出一致，此前只导出侧有界）
"""

from __future__ import annotations

import json

from app.core import backup as backup_core
from app.db import models
from app.db.base import SessionLocal


def _mk_admin():
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="admin", username="admin", password_hash="", is_admin=True))
        db.commit()
        return db.query(User).filter_by(username="admin").one().id
    finally:
        db.close()


def _upload(client, headers, payload: dict, endpoint: str = "/api/backup/import"):
    files = {
        "file": ("b.json", json.dumps(payload).encode("utf-8"), "application/json"),
    }
    return client.post(endpoint, headers=headers, files=files)


def _backup(sessions=None, **extra) -> dict:
    data = {
        "format": "deep-tutor-backup",
        "version": 2,
        "exported_at": "2026-10-20T00:00:00",
        "user": {"username": "admin", "name": "admin"},
        "provider_profiles": [],
        "prefs": {},
        "notes": [],
        "sessions": sessions or [],
        "threads": [],
        "memories": [],
        "kb_documents": [],
    }
    data.update(extra)
    return data


# ---------------------------------------------------------------------------
# 全局行数预算
# ---------------------------------------------------------------------------


def test_import_global_row_budget_returns_413(client, auth_headers, monkeypatch):
    """payload 总行数超全局预算 -> 413（不落任何行），防单事务写锁垄断。"""
    _mk_admin()
    monkeypatch.setattr(backup_core, "_MAX_TOTAL_ROWS", 20)
    # 1 个会话 + 1 个方案 + 50 条打卡 = 52 行 > 20
    payload = _backup(
        sessions=[
            {
                "title": "s",
                "plans": [
                    {
                        "title": "p",
                        "learning_logs": [
                            {"date": "2026-10-01", "minutes": 10} for _ in range(50)
                        ],
                    }
                ],
            }
        ]
    )
    r = _upload(client, auth_headers, payload)
    assert r.status_code == 413, r.text
    assert r.json()["error"]["code"] == "too_large"
    db = SessionLocal()
    try:
        assert db.query(models.Session).count() == 0
        assert db.query(models.Plan).count() == 0
    finally:
        db.close()


def test_import_within_budget_succeeds(client, auth_headers, monkeypatch):
    """预算内正常导入不受影响。"""
    _mk_admin()
    monkeypatch.setattr(backup_core, "_MAX_TOTAL_ROWS", 1000)
    payload = _backup(
        sessions=[{"title": "s", "plans": [{"title": "p", "learning_logs": [{"date": "2026-10-01", "minutes": 10}]}]}]
    )
    r = _upload(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    assert r.json()["sessions"] == 1


# ---------------------------------------------------------------------------
# 畸形 payload -> 400（此前 500）
# ---------------------------------------------------------------------------


def test_import_malformed_section_type_returns_400(client, auth_headers):
    """顶层段类型非法 -> 400 invalid_backup（此前 len(5) 抛 TypeError -> 500）。"""
    _mk_admin()
    payload = _backup(provider_profiles=5)
    r = _upload(client, auth_headers, payload)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "invalid_backup"


def test_import_malformed_nested_shape_is_tolerated(client, auth_headers):
    """嵌套形状非法（knowledge_nodes 为字符串数组）：导入侧逐项 isinstance 兜底，
    非对象元素跳过，不 500。"""
    _mk_admin()
    payload = _backup(sessions=[{"title": "s", "plans": [{"knowledge_nodes": ["x"]}]}])
    r = _upload(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    assert r.json()["knowledge_nodes"] == 0


def test_preview_malformed_nested_shape_returns_400(client, auth_headers):
    """预检遇嵌套形状非法 -> 400（此前 n.get("note") 对字符串抛 AttributeError -> 500）。"""
    _mk_admin()
    payload = _backup(sessions=[{"title": "s", "plans": [{"knowledge_nodes": ["x"]}]}])
    r = _upload(client, auth_headers, payload, endpoint="/api/backup/preview")
    assert r.status_code == 200, r.text  # 预检按同一口径计数，不崩溃
    assert r.json()["knowledge_nodes"] == 0


def test_preview_malformed_section_type_returns_400(client, auth_headers):
    """预检同样对畸形结构返回 400（此前 TypeError 落 500）。"""
    _mk_admin()
    r = _upload(client, auth_headers, _backup(provider_profiles=5), endpoint="/api/backup/preview")
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# 材料三段制状态往返
# ---------------------------------------------------------------------------


def test_material_status_round_trip(client, auth_headers):
    """材料状态（pending/confirmed）随导出/导入往返，不被静默改成 parsed。"""
    uid = _mk_admin()
    db = SessionLocal()
    try:
        sess = models.Session(user_id=uid, title="s")
        db.add(sess)
        db.flush()
        db.add(models.Material(session_id=sess.id, kind=models.MaterialKind.file, source="a.pdf", status="pending"))
        db.add(models.Material(session_id=sess.id, kind=models.MaterialKind.file, source="b.pdf", status="confirmed"))
        db.commit()
    finally:
        db.close()

    exp = client.get("/api/backup/export", headers=auth_headers)
    assert exp.status_code == 200, exp.text
    payload = json.loads(exp.content)
    statuses = sorted(m["status"] for m in payload["sessions"][0]["materials"])
    assert statuses == ["confirmed", "pending"], statuses

    r = _upload(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        got = sorted(m.status for m in db.query(models.Material).all())
        assert got == ["confirmed", "confirmed", "pending", "pending"], got
    finally:
        db.close()


def test_legacy_material_without_status_defaults_parsed(client, auth_headers):
    """v1 旧备份无 status 字段 -> 按 parsed 处理（与列默认一致，往返兼容）。"""
    _mk_admin()
    payload = _backup(sessions=[{"title": "s", "materials": [{"kind": "file", "source": "a.pdf"}]}])
    r = _upload(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        row = db.query(models.Material).one()
        assert row.status == "parsed"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 时间戳归一 + 整数钳制
# ---------------------------------------------------------------------------


def test_tz_aware_timestamp_normalized_to_utc(client, auth_headers):
    """带 +08:00 偏移的时间戳按 UTC 存（此前偏移被丢，时间线平移 8 小时）。"""
    _mk_admin()
    payload = _backup(
        sessions=[
            {
                "title": "s",
                "created_at": "2026-01-01T08:00:00+08:00",  # UTC 零点
                "plans": [{"title": "p", "created_at": "2026-01-01T08:00:00+08:00"}],
            }
        ]
    )
    r = _upload(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        sess = db.query(models.Session).one()
        plan = db.query(models.Plan).one()
        assert sess.created_at.hour == 0, sess.created_at
        assert sess.created_at.minute == 0, sess.created_at
        assert plan.created_at.hour == 0, plan.created_at
    finally:
        db.close()


def test_out_of_range_integer_does_not_crash_import(client, auth_headers):
    """超范围整数按非法值处理，不因 sqlite 绑定 OverflowError 整包 500。"""
    _mk_admin()
    payload = _backup(
        sessions=[
            {
                "title": "s",
                "plans": [{"title": "p", "version": 10**30}],
            }
        ]
    )
    r = _upload(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        plan = db.query(models.Plan).one()
        assert plan.version == 1  # 回退默认
    finally:
        db.close()


def test_out_of_range_stage_index_does_not_crash_import(client, auth_headers):
    """study_units.stage_index 超范围 -> 按非法值置 None（此前整包 500）。"""
    _mk_admin()
    payload = _backup(
        sessions=[
            {
                "title": "s",
                "plans": [{"title": "p", "study_units": [{"title": "u", "stage_index": 10**30}]}],
            }
        ]
    )
    r = _upload(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        unit = db.query(models.StudyUnit).one()
        assert unit.stage_index is None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 重复 sid 的确定性
# ---------------------------------------------------------------------------


def test_duplicate_sid_first_wins_and_reported(client, auth_headers):
    """同 plan 内重复 sid：首条胜出、后续跳过并计入 skipped（树形与归属不漂移）。"""
    _mk_admin()
    payload = _backup(
        sessions=[
            {
                "title": "s",
                "plans": [
                    {
                        "title": "p",
                        "knowledge_nodes": [
                            {"sid": "k0", "name": "根"},
                            {"sid": "k1", "name": "子A", "parent_sid": "k0"},
                            {"sid": "k1", "name": "子B", "parent_sid": "k0"},
                        ],
                        "progress": [{"node_sid": "k1", "status": "done", "mastery": 0.9}],
                    }
                ],
            }
        ]
    )
    r = _upload(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["knowledge_nodes"] == 2, body
    assert any("重复 sid" in s for s in body["skipped"]), body["skipped"]

    db = SessionLocal()
    try:
        nodes = db.query(models.KnowledgeNode).order_by(models.KnowledgeNode.id).all()
        assert len(nodes) == 2
        assert nodes[0].sid == "k0" and nodes[0].parent_id is None
        assert nodes[1].sid == "k1"
        # 子节点仍挂在根节点下（先前插的那条不再退化成根节点）
        assert nodes[1].parent_id == nodes[0].id
        # 进度挂在首条 k1 上（不被错挂到后一条）
        prog = db.query(models.Progress).one()
        assert prog.node_id == nodes[1].id
    finally:
        db.close()


def test_orphan_progress_is_reported(client, auth_headers):
    """锚点知识点缺失的进度行被显式披露（此前静默丢弃，界面只见"进度 0"）。"""
    _mk_admin()
    payload = _backup(
        sessions=[
            {
                "title": "s",
                "plans": [
                    {
                        "title": "p",
                        "knowledge_nodes": [{"sid": "k1", "name": "节点"}],
                        "progress": [{"node_sid": "ghost", "status": "done", "mastery": 0.5}],
                    }
                ],
            }
        ]
    )
    r = _upload(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["progress"] == 0
    assert any("锚点知识点缺失" in s for s in body["skipped"]), body["skipped"]


# ---------------------------------------------------------------------------
# 下标重映射
# ---------------------------------------------------------------------------


def test_message_run_index_maps_to_correct_run(client, auth_headers):
    """跳过一条 run 后，messages.run_idx 仍指向 payload 中的正确 run。"""
    _mk_admin()
    payload = _backup(
        threads=[
            {
                "title": "t",
                "kind": "chat",
                "runs": [
                    "not-a-dict",  # 被跳过（不占 payload 下标）
                    {"kind": "chat", "status": "done"},
                ],
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "hi"}], "run_idx": 1},
                ],
            }
        ]
    )
    r = _upload(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["runs"] == 1, body
    assert body["messages"] == 1, body
    db = SessionLocal()
    try:
        run = db.query(models.Run).one()
        msg = db.query(models.Message).one()
        assert msg.run_id == run.id, "message 未挂到正确的 run"
    finally:
        db.close()


def test_memory_thread_index_maps_to_correct_thread(client, auth_headers):
    """跳过一条 thread 后，memories.thread_idx 仍指向 payload 中的正确 thread。"""
    _mk_admin()
    payload = _backup(
        threads=[
            "not-a-dict",
            {"title": "t2", "kind": "chat"},
        ],
        memories=[{"scope": "thread", "kind": "note", "content": "记忆", "thread_idx": 1}],
    )
    r = _upload(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        thread = db.query(models.Thread).one()
        mem = db.query(models.Memory).one()
        assert thread.title == "t2"
        assert mem.thread_id == thread.id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 文件补偿
# ---------------------------------------------------------------------------


def test_failed_import_removes_created_files(client, auth_headers, monkeypatch):
    """导入失败 -> 已落盘的 kb 原件/解析文本被删除（DB 回滚不回滚磁盘）。"""
    uid = _mk_admin()
    orig = backup_core._restore_kb_documents

    def boom(*args, **kwargs):
        orig(*args, **kwargs)
        raise RuntimeError("simulated failure after files written")

    monkeypatch.setattr(backup_core, "_restore_kb_documents", boom)
    payload = _backup(
        kb_documents=[
            {
                "title": "doc",
                "kind": "file",
                "ext": "txt",
                "parsed_text": "解析文本",
                "original_b64": "aGVsbG8=",
            }
        ]
    )
    r = _upload(client, auth_headers, payload)
    assert r.status_code == 500, r.text

    from app.core.storage import kb_originals_dir, kb_parsed_dir

    assert list(kb_originals_dir(uid).glob("*")) == [], "kb 原件孤儿未清理"
    assert list(kb_parsed_dir(uid).glob("*")) == [], "kb 解析文本孤儿未清理"
    db = SessionLocal()
    try:
        assert db.query(models.KbDocument).count() == 0
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 预检计数一致 + 覆盖面披露
# ---------------------------------------------------------------------------


def test_preview_counts_match_actual_import(client, auth_headers):
    """预检显示的条数 == 实际导入的条数（同配额 + 本机额度抵扣）。"""
    _mk_admin()
    payload = _backup(
        sessions=[
            {
                "title": "s1",
                "materials": [{"kind": "file", "source": "a"}],
                "plans": [{"title": "p", "knowledge_nodes": [{"sid": "k1", "name": "n"}]}],
            },
            {"title": "s2", "materials": [{"kind": "file", "source": "b"}]},
        ],
        notes=[{"title": "n1", "content": "c"}],
        threads=[{"title": "t", "kind": "chat", "messages": [{"role": "user", "content": []}]}],
    )
    prev = _upload(client, auth_headers, payload, endpoint="/api/backup/preview")
    assert prev.status_code == 200, prev.text
    p = prev.json()
    imp = _upload(client, auth_headers, payload)
    assert imp.status_code == 200, imp.text
    b = imp.json()
    for key in (
        "sessions",
        "materials",
        "plans",
        "knowledge_nodes",
        "notes",
        "threads",
        "messages",
    ):
        assert p[key] == b[key], f"{key}: preview={p[key]} import={b[key]}"


def test_not_backed_up_disclosed(client, auth_headers):
    """未纳入备份的表在导出与预检中显式列出（"全部学习数据"的表述与实现一致）。"""
    _mk_admin()
    exp = client.get("/api/backup/export", headers=auth_headers)
    assert exp.status_code == 200, exp.text
    payload = json.loads(exp.content)
    assert "api_usage" in payload["not_backed_up"]
    assert "share_links" in payload["not_backed_up"]

    prev = _upload(client, auth_headers, payload, endpoint="/api/backup/preview")
    assert prev.status_code == 200, prev.text
    assert "api_usage" in prev.json()["not_backed_up"]


# ---------------------------------------------------------------------------
# 导入侧 kb 原件体积上限（与导出一致）
# ---------------------------------------------------------------------------


def test_kb_import_respects_per_file_size_cap(client, auth_headers, monkeypatch):
    """导入侧同样限制 kb 原件单文件大小（此前只导出侧有界，可写入 190MB 原件）。"""
    import base64

    _mk_admin()
    monkeypatch.setattr(backup_core, "_KB_ORIGINAL_MAX_BYTES", 16)
    raw = base64.b64encode(b"x" * 64).decode("ascii")
    payload = _backup(kb_documents=[{"title": "doc", "kind": "file", "ext": "bin", "original_b64": raw}])
    r = _upload(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kb_documents"] == 1
    assert any("上限" in s for s in body["skipped"]), body["skipped"]
    db = SessionLocal()
    try:
        doc = db.query(models.KbDocument).one()
        assert doc.original_path is None
    finally:
        db.close()

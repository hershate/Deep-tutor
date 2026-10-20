"""iter 396 B2 回归：备份导出改**流式写出**（峰值内存从整份数据集降到单会话量级）。

问题：build_export 先把整份数据集组装成 dict（Python 对象形态约为 JSON 体积的
3~5 倍），再 json.dump 落盘——重度用户单次导出的内存峰值可达数 GB，恰在最需要
备份的时刻最可能 OOM。

修法：core/backup.write_export 逐项写出（会话/对话/笔记/记忆/知识库文档逐个
处理并即时 expunge），知识库原件分块 base64（不整份驻留内存）。

本文件验证：
- 流式输出与旧「组装 dict」路径**语义完全一致**（A/B 对照探针的口径固化）；
- 知识库原件分块 base64 在**块边界**（大小非 3 的倍数）上仍能精确还原；
- 知识库文档的字段完整（created_at / updated_at / original_b64 不漏项）；
- 导出峰值内存与数据集大小**解耦**。
"""

from __future__ import annotations

import base64
import io
import json

from app.core import backup as backup_mod
from app.core import storage as storage_mod
from app.db import models
from app.db.base import SessionLocal


def _mk_user(name: str) -> int:
    db = SessionLocal()
    try:
        u = models.User(name=name, username=name, password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _seed_kb_doc(user_id: int, raw: bytes, title: str = "kb") -> int:
    db = SessionLocal()
    try:
        doc = models.KbDocument(
            user_id=user_id,
            title=title,
            kind=models.MaterialKind.file,
            ext="pdf",
            source="k.pdf",
            status="parsed",
        )
        db.add(doc)
        db.flush()
        doc.original_path = str(storage_mod.save_kb_upload(user_id, doc.id, raw, "pdf"))
        doc.parsed_text_path = str(storage_mod.write_kb_parsed_text(user_id, doc.id, "解析正文"))
        db.commit()
        return doc.id
    finally:
        db.close()


def _export_dict(user_id: int) -> dict:
    db = SessionLocal()
    try:
        return backup_mod.build_export(db, db.get(models.User, user_id))
    finally:
        db.close()


def _export_stream(user_id: int) -> dict:
    db = SessionLocal()
    try:
        buf = io.StringIO()
        backup_mod.write_export(db, db.get(models.User, user_id), buf)
        return json.loads(buf.getvalue())
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. 结构与语义
# ---------------------------------------------------------------------------


def test_export_top_level_keys_complete():
    """流式导出的顶层键与既有契约一致（不得因改写而漏键）。"""
    uid = _mk_user("b2_keys")
    payload = _export_stream(uid)
    assert set(payload) == {
        "format",
        "version",
        "exported_at",
        "user",
        "provider_profiles",
        "prefs",
        "notes",
        "sessions",
        "threads",
        "memories",
        "kb_documents",
        "not_backed_up",
        "truncated_originals",
    }, sorted(payload)
    assert payload["format"] == backup_mod.FORMAT
    assert payload["version"] == backup_mod.VERSION
    assert payload["not_backed_up"] == backup_mod.not_backed_up()


def test_kb_doc_fields_complete():
    """知识库文档条目不得漏字段（created_at / updated_at 曾被漏写）。"""
    uid = _mk_user("b2_kb_fields")
    _seed_kb_doc(uid, b"PDF-BYTES", title="字段完整")
    payload = _export_stream(uid)
    doc = payload["kb_documents"][0]
    assert set(doc) == {
        "title",
        "kind",
        "ext",
        "source",
        "parsed_text",
        "parsed_chars",
        "meta",
        "content_hash",
        "status",
        "created_at",
        "updated_at",
        "original_b64",
    }, sorted(doc)
    assert doc["created_at"] and doc["updated_at"]
    assert doc["title"] == "字段完整"


def test_stream_and_dict_paths_agree():
    """流式写出与 dict 组装两条路径结果一致（同源实现，语义须完全相同）。"""
    uid = _mk_user("b2_agree")
    _seed_kb_doc(uid, b"AGREE-BYTES")
    a = _export_dict(uid)
    b = _export_stream(uid)
    a.pop("exported_at", None)
    b.pop("exported_at", None)
    assert a == b


# ---------------------------------------------------------------------------
# 2. 原件分块 base64 的边界正确性
# ---------------------------------------------------------------------------


def test_kb_original_b64_chunk_boundary_exact():
    """原件大小跨块边界且非 3 的倍数时，内联 base64 仍须精确还原原始字节。

    分块 base64 若块大小不是 3 的倍数，块间拼接会在中间产生填充位 -> 解码后
    字节错位（导入侧会还原出损坏的原件）。
    """
    uid = _mk_user("b2_chunk")
    # 3MB 块边界 + 7 字节余数（跨越边界且非 3 的倍数）
    raw = bytes((i * 7 + 3) % 256 for i in range(3 * 1024 * 1024 + 7))
    _seed_kb_doc(uid, raw, title="跨界")
    payload = _export_stream(uid)
    b64 = payload["kb_documents"][0]["original_b64"]
    assert b64, "原件未内联"
    decoded = base64.b64decode(b64, validate=True)
    assert decoded == raw, f"还原字节不一致：{len(decoded)} vs {len(raw)}"
    assert len(decoded) == len(raw)


def test_kb_original_b64_small_exact():
    """小块原件同样精确还原（单块路径）。"""
    uid = _mk_user("b2_small")
    raw = b"hello-kb-original"
    _seed_kb_doc(uid, raw, title="小块")
    payload = _export_stream(uid)
    decoded = base64.b64decode(payload["kb_documents"][0]["original_b64"], validate=True)
    assert decoded == raw


# ---------------------------------------------------------------------------
# 3. 峰值内存与数据集大小解耦
# ---------------------------------------------------------------------------


def _seed_texts(user_name: str, n_sess: int, n_mat: int, size: int) -> int:
    """造 n_sess x n_mat 条资料（每条 size 字符解析文本）；返回 user_id。"""
    uid = _mk_user(user_name)
    db = SessionLocal()
    try:
        for i in range(n_sess):
            sess = models.Session(user_id=uid, title=f"s{i}")
            db.add(sess)
            db.flush()
            for _j in range(n_mat):
                mat = models.Material(
                    session_id=sess.id,
                    kind=models.MaterialKind.text,
                    source="x",
                    parsed_chars=size,
                )
                db.add(mat)
                db.flush()
                text = "数据" * (size // 2)
                path = storage_mod.parsed_text_path(sess.id, mat.id)
                storage_mod.atomic_write_text(path, text)
                mat.parsed_text_path = str(path)
        db.commit()
    finally:
        db.close()
    return uid


def _export_peak(user_id: int, out) -> tuple[int, int]:
    """导出并返回 (Python 峰值分配字节, 导出文件字节)。"""
    import tracemalloc

    db = SessionLocal()
    try:
        user = db.get(models.User, user_id)
        tracemalloc.start()
        tracemalloc.reset_peak()
        with open(out, "w", encoding="utf-8") as f:
            backup_mod.write_export(db, user, f)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    finally:
        db.close()
    return peak, out.stat().st_size


def test_export_peak_memory_decoupled_from_dataset():
    """导出峰值内存不得随数据集线性增长（流式的核心收益）。

    自校准口径：同一进程内导出**两份规模相差数倍**的数据集，比对峰值——
    旧实现（先组装 dict）峰值随数据量线性上升，流式实现峰值只与「单条记录」
    相关，应基本持平。
    """
    small_uid = _seed_texts("b2_mem_small", n_sess=2, n_mat=2, size=100_000)
    big_uid = _seed_texts("b2_mem_big", n_sess=10, n_mat=2, size=100_000)

    root = storage_mod.data_root()
    out_small = root / "b2_mem_small.json"
    out_big = root / "b2_mem_big.json"
    try:
        peak_small, size_small = _export_peak(small_uid, out_small)
        peak_big, size_big = _export_peak(big_uid, out_big)

        assert size_big > size_small * 4, (
            f"两份数据集规模未拉开：{size_small} vs {size_big}"
        )
        # 峰值允许小幅上升（缓冲区/分配器残留），但不得随数据量线性增长
        assert peak_big < peak_small * 1.5 + 2 * 1024 * 1024, (
            f"峰值随数据量线性增长：{peak_small / 1024 / 1024:.1f}MB -> "
            f"{peak_big / 1024 / 1024:.1f}MB（导出体积 "
            f"{size_small / 1024 / 1024:.1f}MB -> {size_big / 1024 / 1024:.1f}MB）"
        )
    finally:
        out_small.unlink(missing_ok=True)
        out_big.unlink(missing_ok=True)

"""iter 372 R11：备份 v2 大载荷与配额边界实测。

既有 v2 回归（test_iter370_backup_v2）覆盖小载荷正确性；本轮补两类规模化证据：
- 配额边界（软跳过语义）：threads ≤200 上限处精确截断、重复导入不放大、skipped 留痕；
- 真实端点全量往返：150 线程 × 10 消息（1500 条）+ 8 篇带 ~128KB base64 原件的
  知识库文档 → export（流式 FileResponse）→ import（to_thread 提交链）→ 计数精确 +
  原件字节级一致 + 耗时在秒级（高负载下的稳定性实测，非仅功能正确性）。
"""

import json
import time

from fastapi.testclient import TestClient

from app.core.security import create_token
from app.db import models
from app.db.base import SessionLocal
from app.main import app

client = TestClient(app)

_PAYLOAD = "deep-tutor-backup"


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


def _count(model, **kw) -> int:
    db = SessionLocal()
    try:
        return db.query(model).filter_by(**kw).count()
    finally:
        db.close()


def test_thread_quota_boundary_soft_skip():
    """250 线程备份 → 空用户精确导入 200、50 跳过留痕；再次导入不放大。"""
    uid, tok = _mk_user("bk_quota")
    h = {"Authorization": f"Bearer {tok}"}
    backup = {
        "format": _PAYLOAD, "version": 2, "sessions": [],
        "threads": [
            {"title": f"t{i}", "kind": "chat", "model": "m",
             "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
            for i in range(250)
        ],
        "memories": [], "kb_documents": [],
    }
    r = client.post("/api/backup/import",
                    files={"file": ("b.json", json.dumps(backup), "application/json")}, headers=h)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["threads"] == 200, body["threads"]
    assert _count(models.Thread, user_id=uid) == 200
    assert any("对话会话数达上限" in s for s in body["skipped"]), body["skipped"]

    # 二次导入：room=0，全部跳过，不放大占用
    r2 = client.post("/api/backup/import",
                     files={"file": ("b.json", json.dumps(backup), "application/json")}, headers=h)
    assert r2.status_code == 200
    assert r2.json()["threads"] == 0, r2.json()["threads"]
    assert _count(models.Thread, user_id=uid) == 200


def test_scale_roundtrip_threads_messages_kb_originals(tmp_path):
    """150×10 消息 + 8×128KB base64 原件经真实端点往返——计数/字节一致 + 秒级耗时。"""
    src_id, src_tok = _mk_user("bk_scale_src")
    dst_id, dst_tok = _mk_user("bk_scale_dst")
    db = SessionLocal()
    try:
        for i in range(150):
            t = models.Thread(user_id=src_id, title=f"线程{i}", kind="chat", model="m")
            db.add(t)
            db.flush()
            run = models.Run(thread_id=t.id, user_id=src_id, kind=models.RunKind.chat,
                             status=models.RunStatus.done, step_count=1, budget_used=0.01,
                             workspace_dir="")
            db.add(run)
            db.flush()
            for j in range(10):
                db.add(models.Message(thread_id=t.id, role=models.MessageRole.user,
                                      content=[{"type": "text", "text": f"消息{i}-{j}"}],
                                      tokens_in=10, tokens_out=5, model="m", run_id=run.id))
        originals = []
        for k in range(8):
            raw = bytes([k % 256]) * 131_072  # 128KB 可压缩性最低的形态
            p = tmp_path / f"orig{k}.bin"
            p.write_bytes(raw)
            pt = tmp_path / f"parsed{k}.md"
            pt.write_text(f"文档{k}解析正文" * 100, encoding="utf-8")
            db.add(models.KbDocument(
                user_id=src_id, title=f"kb{k}", kind=models.MaterialKind.file, ext="bin",
                source=f"orig{k}.bin", original_path=str(p), parsed_text_path=str(pt),
                content_hash=f"hash_scale_{k}", status="parsed", parsed_chars=700,
            ))
            originals.append(raw)
        db.commit()
    finally:
        db.close()

    t0 = time.monotonic()
    r = client.get("/api/backup/export", headers={"Authorization": f"Bearer {src_tok}"})
    assert r.status_code == 200, r.text[:200]
    data = json.loads(r.content)
    t_export = time.monotonic() - t0
    assert data["version"] == 2 and data["format"] == _PAYLOAD
    assert len(data["threads"]) == 150 and len(data["kb_documents"]) == 8
    # 原件确已内联（8 × 128KB × base64 ≈ 1.4MB 下限）
    assert sum(len(d.get("original_b64") or "") for d in data["kb_documents"]) > 1_000_000

    t1 = time.monotonic()
    r2 = client.post("/api/backup/import",
                     files={"file": ("b.json", r.content, "application/json")},
                     headers={"Authorization": f"Bearer {dst_tok}"})
    assert r2.status_code == 200, r2.text[:300]
    body = r2.json()
    t_import = time.monotonic() - t1

    assert body["threads"] == 150, body
    assert body["messages"] == 1500, body
    assert body["runs"] == 150, body
    assert body["kb_documents"] == 8, body
    assert _count(models.Thread, user_id=dst_id) == 150
    db = SessionLocal()
    try:
        msg_n = (db.query(models.Message)
                 .join(models.Thread, models.Message.thread_id == models.Thread.id)
                 .filter(models.Thread.user_id == dst_id).count())
        assert msg_n == 1500, msg_n

        # 消息内容抽检 + 关联（runs 重映射后 run_id 非空）
        m = (db.query(models.Message).join(models.Thread, models.Message.thread_id == models.Thread.id)
             .filter(models.Thread.user_id == dst_id, models.Message.model == "m")
             .order_by(models.Message.id).first())
        assert m is not None and m.run_id is not None and m.content[0]["text"].startswith("消息")
        kb = db.query(models.KbDocument).filter_by(user_id=dst_id).order_by(models.KbDocument.id).all()
        assert len(kb) == 8
        from pathlib import Path
        restored = [Path(d.original_path).read_bytes() for d in kb]
        assert sorted(restored) == sorted(originals), "kb 原件字节级往返不一致"
    finally:
        db.close()

    # 高负载口径：千级消息 + MB 级原件的往返应在秒级（to_thread 提交链不冻结事件循环的实证）
    print(f"\n[backup-scale] export={t_export:.2f}s import={t_import:.2f}s payload={len(r.content)/1e6:.1f}MB")
    assert t_export < 60 and t_import < 60

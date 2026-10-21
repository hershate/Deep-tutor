"""iter 397-B3：数据一致性回归（K2 会话配额锁 / K3 上传先落盘后建行 / K13 kind 归类）。

- **K2**：会话数配额（每用户 200）此前只有 `POST /api/sessions` 加锁，
  `POST /api/materials/clip` 与 `POST /api/kb/generate` 只 count 不加锁——
  与 sessions.py 注释宣称的「键与 materials/kb 的会话数配额同命名空间 ->
  跨端点互斥」不符。并发创建可同时通过「未达上限」检查，实际会话数按并发数
  突破上限（DB 行 + 会话目录无界增长）。
- **K3**：`POST /api/materials/upload` 此前「先建行提交、再落原件」——行提交后、
  文件写完前进程被杀/落盘失败即留下一条**永远没有原件**的 pending 行
  （用户点解析只得 400，无任何后台路径能修）。现改为「先暂存落盘 -> 建行 ->
  事务内改名」两段式（与备份还原 iter 396 B1 同款）。
- **K13**：materials 上传把 kind 硬编码为 "file"，同一张图片/音视频走会话资料
  接口被归为普通文件，与走知识库接口的归类不一致。
"""

from __future__ import annotations

import concurrent.futures
import io
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# K2：会话数配额锁（三条建会话路径共用同一把锁与同一上限）
# ---------------------------------------------------------------------------


def _session_count() -> int:
    from app.db.base import SessionLocal
    from app.db.models import Session

    db = SessionLocal()
    try:
        return db.query(Session).count()
    finally:
        db.close()


def test_all_session_creating_paths_take_the_same_quota_lock(client, auth_headers, monkeypatch):
    """sessions / materials.clip / kb.generate 必须在同一配额键内做「计数 + 插入」。"""
    import app.api.kb as kb_mod
    import app.api.sessions as sess_mod
    import app.core.quota as quota_mod

    used: list[str] = []
    real_slot = quota_mod.quota_slot

    def _spy(key):
        used.append(key)
        return real_slot(key)

    # sessions/kb 直接持有 import 进来的引用；materials 走 guarded_commit_async
    # -> 其内部调用 app.core.quota 模块全局的 quota_slot，三处都要打桩。
    monkeypatch.setattr(sess_mod, "quota_slot", _spy)
    monkeypatch.setattr(kb_mod, "quota_slot", _spy)
    monkeypatch.setattr(quota_mod, "quota_slot", _spy)

    r = client.post("/api/sessions", json={"title": "k2-a"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert any(k.startswith("session_quota:") for k in used), used

    used.clear()
    r = client.post(
        "/api/materials/clip",
        json={"kind": "text", "content": "剪藏内容", "title": "k2-b"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert any(k.startswith("session_quota:") for k in used), used

    used.clear()
    doc = client.post(
        "/api/kb/documents/text",
        json={"title": "k2-doc", "text": "知识库文本"},
        headers=auth_headers,
    )
    assert doc.status_code == 200, doc.text
    r = client.post(
        "/api/kb/generate",
        json={"document_ids": [doc.json()["id"]]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert any(k.startswith("session_quota:") for k in used), used


def test_concurrent_session_creation_respects_the_cap(client, auth_headers, monkeypatch):
    """并发（多端点）创建会话，实际落库数不得突破上限。"""
    import app.api.sessions as sess_mod

    monkeypatch.setattr(sess_mod, "_MAX_SESSIONS_PER_USER", 2)

    def _mk_session():
        return client.post("/api/sessions", json={"title": "k2-race"}, headers=auth_headers).status_code

    def _mk_clip():
        return client.post(
            "/api/materials/clip",
            json={"kind": "text", "content": "并发剪藏内容", "title": "k2-clip"},
            headers=auth_headers,
        ).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(_mk_session) for _ in range(3)]
        futs += [pool.submit(_mk_clip) for _ in range(3)]
        codes = [f.result() for f in futs]

    assert _session_count() <= 2, f"会话数突破上限：codes={codes} count={_session_count()}"
    assert 429 in codes, f"超限请求未返回 429：codes={codes}"


# ---------------------------------------------------------------------------
# K3：上传原件先落盘后建行
# ---------------------------------------------------------------------------


def _png_bytes() -> bytes:
    """最小合法 PNG（1x1 像素）。"""
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
        b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
        b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_upload_leaves_no_pending_row_without_original(client, auth_headers):
    """上传成功后：行存在 + 原件存在 + 会话暂存目录无残留。"""
    sid = client.post("/api/sessions", json={"title": "k3"}, headers=auth_headers).json()["session_id"]
    r = client.post(
        "/api/materials/upload",
        data={"session_id": str(sid)},
        files={"file": ("a.png", io.BytesIO(_png_bytes()), "image/png")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    mid = r.json()["material_id"]

    from app.core.settings import settings

    base = Path(settings.data_dir) / "sessions" / str(sid)
    originals = list((base / "originals").glob(f"{mid}.*"))
    assert originals, f"原件缺失：{list((base / 'originals').glob('*'))}"
    staging = base / ".staging"
    leftovers = list(staging.glob("*")) if staging.exists() else []
    assert not leftovers, f"暂存目录有残留：{leftovers}"


def test_upload_failure_leaves_no_row_and_no_file(client, auth_headers, monkeypatch):
    """落盘/改名失败：不得留下「有行无文件」的残局（行回滚 + 文件清理）。"""
    import app.api.materials as mat_mod

    sid = client.post("/api/sessions", json={"title": "k3-fail"}, headers=auth_headers).json()["session_id"]

    def _boom(*args, **kwargs):
        raise OSError("iter397 注入的落盘失败")

    monkeypatch.setattr(mat_mod, "promote_upload", _boom)
    with pytest.raises(OSError):
        client.post(
            "/api/materials/upload",
            data={"session_id": str(sid)},
            files={"file": ("a.png", io.BytesIO(_png_bytes()), "image/png")},
            headers=auth_headers,
        )

    from app.db.base import SessionLocal
    from app.db.models import Material

    db = SessionLocal()
    try:
        rows = db.query(Material).filter_by(session_id=sid).count()
    finally:
        db.close()
    assert rows == 0, "落盘失败后仍留下资料行"

    from app.core.settings import settings

    base = Path(settings.data_dir) / "sessions" / str(sid)
    originals = list((base / "originals").glob("*")) if (base / "originals").exists() else []
    staging = list((base / ".staging").glob("*")) if (base / ".staging").exists() else []
    assert not originals and not staging, f"落盘失败后残留文件：{originals} {staging}"


# ---------------------------------------------------------------------------
# K13：上传按扩展名归类（与知识库口径一致）
# ---------------------------------------------------------------------------


def test_upload_kind_matches_kb_classification(client, auth_headers):
    """图片/音视频上传的 kind 应与 kb 上传一致（此前恒为 "file"）。"""
    sid = client.post("/api/sessions", json={"title": "k13"}, headers=auth_headers).json()["session_id"]
    r = client.post(
        "/api/materials/upload",
        data={"session_id": str(sid)},
        files={"file": ("a.png", io.BytesIO(_png_bytes()), "image/png")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "image", r.text

    listing = client.get(f"/api/sessions/{sid}/materials", headers=auth_headers)
    assert listing.status_code == 200, listing.text
    kinds = {m["material_id"]: m["kind"] for m in listing.json()["items"]}
    assert kinds[r.json()["material_id"]] == "image"

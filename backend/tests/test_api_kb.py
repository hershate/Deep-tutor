"""知识库文档库 API 测试（iter 213）：upload/text/url 创建、去重、配额、列表过滤、CRUD、级联。"""

from __future__ import annotations

from io import BytesIO

from app.db import models

BASE = "/api/kb/documents"


def _db():
    from app.db.base import SessionLocal

    return SessionLocal()


def _doc_count() -> int:
    db = _db()
    try:
        return db.query(models.KbDocument).count()
    finally:
        db.close()


def _doc_paths(doc_id: int) -> tuple[str | None, str | None]:
    db = _db()
    try:
        d = db.get(models.KbDocument, doc_id)
        return (d.original_path, d.parsed_text_path) if d else (None, None)
    finally:
        db.close()


def _first_user_id() -> int:
    db = _db()
    try:
        return db.query(models.User).first().id
    finally:
        db.close()


def _doc_owner(doc_id: int) -> int:
    db = _db()
    try:
        return db.get(models.KbDocument, doc_id).user_id
    finally:
        db.close()


def _upload(client, headers, name="a.md", body=None, mime="text/markdown"):
    if body is None:
        body = "# 标题\n\n知识库内容".encode("utf-8")
    return client.post(f"{BASE}/upload", files={"file": (name, BytesIO(body), mime)}, headers=headers)


def _mk_text_doc(client, headers, text="知识库笔记", title=None) -> dict:
    payload = {"text": text}
    if title is not None:
        payload["title"] = title
    r = client.post(f"{BASE}/text", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _stub_parse_ok(monkeypatch, text="抓取正文"):
    from app.api import kb as kb_mod
    from app.tools.base import ToolOutput

    async def _fake(inp, ctx):
        return ToolOutput(tool="parse_material", status="ok", data={"text": text, "meta": {}})

    monkeypatch.setattr(kb_mod, "parse_material", _fake)


# --- upload ---


def _confirm(client, headers, doc_id: int) -> dict:
    r = client.post(f"{BASE}/{doc_id}/confirm", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _parse(client, headers, doc_id: int, mode: str = "full") -> dict:
    r = client.post(f"{BASE}/{doc_id}/parse", json={"mode": mode}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_upload_md_pending_then_confirm_then_parse(client, auth_headers):
    """iter 225 三段式：上传（仅落原件 pending，不解析）-> 确认（confirmed）-> 解析（parsed）。"""
    from pathlib import Path

    r = _upload(client, auth_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kind"] == "file" and d["ext"] == "md"
    assert d["title"] == "a.md" and d["source"] == "a.md"
    # 上传不解析：pending、无解析文本、原件已落盘
    assert d["status"] == "pending"
    assert d["has_original"] is True and d["parsed_chars"] == 0
    original, parsed = _doc_paths(d["id"])
    assert original and Path(original).exists()
    assert parsed is None

    d = _confirm(client, auth_headers, d["id"])
    assert d["status"] == "confirmed" and d["parsed_chars"] == 0
    assert _doc_paths(d["id"])[1] is None  # 确认仍不解析

    d = _parse(client, auth_headers, d["id"])
    assert d["status"] == "parsed" and d["parsed_chars"] > 0
    original, parsed = _doc_paths(d["id"])
    assert parsed and Path(parsed).exists()
    assert "知识库内容" in Path(parsed).read_text(encoding="utf-8")


def test_upload_magic_mismatch_400(client, auth_headers):
    r = _upload(client, auth_headers, name="fake.pdf", body=b"not a pdf", mime="application/pdf")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_format"
    assert _doc_count() == 0


def test_upload_too_large_413(client, auth_headers, monkeypatch):
    from app.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "max_upload_mb", 1)
    big = b"x" * (2 * 1024 * 1024)  # 2MB > 1MB
    r = _upload(client, auth_headers, name="big.txt", body=big, mime="text/plain")
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"
    assert _doc_count() == 0


def test_upload_unsupported_ext_400(client, auth_headers):
    r = _upload(client, auth_headers, name="a.exe", body=b"MZ...", mime="application/octet-stream")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_format"


def test_parse_failure_keeps_doc_status(client, auth_headers, monkeypatch):
    """解析失败（iter 225）：行保留、status 不变、原件不删——用户可换档重试。"""
    from app.api import kb as kb_mod
    from app.tools.base import ToolError, ToolOutput

    async def _fail(inp, ctx):
        return ToolOutput(
            tool="parse_material",
            status="error",
            data={},
            errors=[ToolError(code="empty_content", message="解析文本为空")],
        )

    monkeypatch.setattr(kb_mod, "parse_material", _fail)
    d = _upload(client, auth_headers).json()
    r = client.post(f"{BASE}/{d['id']}/parse", json={"mode": "full"}, headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "empty_content"
    assert _doc_count() == 1  # 行保留
    r = client.get(f"{BASE}/{d['id']}", headers=auth_headers)
    assert r.json()["status"] == "pending"  # 状态不变，可换档重试


def test_upload_dedup_by_hash(client, auth_headers):
    """iter 225：dedup 命中任意状态行（pending 暂存亦复用，不重复建行占配额）。"""
    r1 = _upload(client, auth_headers)
    r2 = _upload(client, auth_headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.json()["status"] == "pending"
    assert r2.json()["meta"].get("deduped") is True
    assert _doc_count() == 1
    # 解析后重复上传同文件：仍命中（parsed 行），不重复解析
    _parse(client, auth_headers, r1.json()["id"])
    r3 = _upload(client, auth_headers)
    assert r3.json()["id"] == r1.json()["id"] and r3.json()["status"] == "parsed"
    assert _doc_count() == 1


def test_confirm_idempotent(client, auth_headers):
    """confirm 幂等：pending->confirmed；confirmed/parsed 再调不变仍 200。"""
    d = _upload(client, auth_headers).json()
    assert _confirm(client, auth_headers, d["id"])["status"] == "confirmed"
    assert _confirm(client, auth_headers, d["id"])["status"] == "confirmed"
    _parse(client, auth_headers, d["id"])
    assert _confirm(client, auth_headers, d["id"])["status"] == "parsed"


def test_parse_text_document_400(client, auth_headers):
    """text/url 类无原件（创建时已解析入库）：调 parse -> 400。"""
    d = _mk_text_doc(client, auth_headers)
    r = client.post(f"{BASE}/{d['id']}/parse", json={"mode": "full"}, headers=auth_headers)
    assert r.status_code == 400


def test_parse_reparse_switches_mode(client, auth_headers, monkeypatch):
    """已解析文档可重解析换档：mode 透传 parse_material，解析文本覆盖更新。"""
    from app.api import kb as kb_mod
    from app.tools.base import ToolOutput

    modes: list[str] = []

    async def _fake(inp, ctx):
        modes.append(inp.args.get("parse_mode") or "full")
        return ToolOutput(
            tool="parse_material",
            status="ok",
            data={"text": f"第{len(modes)}次解析", "meta": {"parse_mode": modes[-1]}},
        )

    monkeypatch.setattr(kb_mod, "parse_material", _fake)
    d = _upload(client, auth_headers).json()
    d = _parse(client, auth_headers, d["id"], "full")
    assert d["status"] == "parsed" and d["meta"]["parse_mode"] == "full"
    d = _parse(client, auth_headers, d["id"], "fast")
    assert d["meta"]["parse_mode"] == "fast"
    assert modes == ["full", "fast"]


def test_parse_invalid_mode_422(client, auth_headers):
    d = _upload(client, auth_headers).json()
    r = client.post(f"{BASE}/{d['id']}/parse", json={"mode": "turbo"}, headers=auth_headers)
    assert r.status_code == 422


def _stub_parse_slow(monkeypatch, started, release, text="正文"):
    """解析桩：file 类阻塞至 release（模拟大 PDF 分钟级解析），text/url 类立即返回。"""
    import asyncio

    from app.api import kb as kb_mod
    from app.tools.base import ToolOutput

    async def _fake(inp, ctx):
        if inp.args.get("kind") == "file":
            started.set()
            await asyncio.to_thread(release.wait, 10)
        return ToolOutput(tool="parse_material", status="ok", data={"text": text, "meta": {}})

    monkeypatch.setattr(kb_mod, "parse_material", _fake)


def test_parse_concurrent_same_doc_409(client, auth_headers, monkeypatch):
    """同文档并发解析 -> 第二个 409（per-doc 互斥锁；分钟级解析不重入）。"""
    import threading

    started, release = threading.Event(), threading.Event()
    _stub_parse_slow(monkeypatch, started, release)

    d = _upload(client, auth_headers).json()
    first: dict = {}

    def _parse_first():
        first["r"] = client.post(
            f"{BASE}/{d['id']}/parse", json={"mode": "full"}, headers=auth_headers
        )

    t = threading.Thread(target=_parse_first)
    t.start()
    try:
        assert started.wait(5), "第一个解析未开始"
        r2 = client.post(f"{BASE}/{d['id']}/parse", json={"mode": "fast"}, headers=auth_headers)
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == "conflict"
    finally:
        release.set()
        t.join(timeout=15)
    assert first["r"].status_code == 200, first["r"].text
    assert client.get(f"{BASE}/{d['id']}", headers=auth_headers).json()["status"] == "parsed"


def test_parse_during_other_write_not_locked(client, auth_headers, monkeypatch):
    """err.md B22 核心回归：解析（分钟级）期间其他写请求不再 database is locked 500。

    旧实现 flush INSERT 后在未提交写事务内 await parse_material，持写锁期间这里并发
    的 /documents/text INSERT 会等 busy_timeout(5s) 后 OperationalError -> 500。
    """
    import threading

    started, release = threading.Event(), threading.Event()
    _stub_parse_slow(monkeypatch, started, release)

    d = _upload(client, auth_headers).json()
    first: dict = {}

    def _parse_first():
        first["r"] = client.post(
            f"{BASE}/{d['id']}/parse", json={"mode": "full"}, headers=auth_headers
        )

    t = threading.Thread(target=_parse_first)
    t.start()
    try:
        assert started.wait(5), "第一个解析未开始"
        r = client.post(f"{BASE}/text", json={"text": "解析期间的笔记"}, headers=auth_headers)
        assert r.status_code == 200, r.text
    finally:
        release.set()
        t.join(timeout=15)
    assert first["r"].status_code == 200, first["r"].text
    assert _doc_count() == 2


def test_kind_classification_by_ext():
    from app.api.kb import _kind_for_ext

    assert _kind_for_ext(".png") is models.MaterialKind.image
    assert _kind_for_ext(".mp3") is models.MaterialKind.audio
    assert _kind_for_ext(".mp4") is models.MaterialKind.video
    assert _kind_for_ext(".pdf") is models.MaterialKind.file
    assert _kind_for_ext(".md") is models.MaterialKind.file


# --- text ---


def test_text_success_and_default_title(client, auth_headers):
    d = _mk_text_doc(client, auth_headers, "第一行标题\n\n正文内容")
    assert d["kind"] == "text" and d["ext"] == "txt"
    assert d["title"] == "第一行标题"  # 缺省标题取首行前 40 字
    assert d["has_original"] is False  # 文本无原件
    assert d["parsed_chars"] > 0
    # 显式标题优先
    d2 = _mk_text_doc(client, auth_headers, "另一份", title="自定义")
    assert d2["title"] == "自定义"


def test_text_empty_422(client, auth_headers):
    assert client.post(f"{BASE}/text", json={"text": "   "}, headers=auth_headers).status_code == 422
    assert _doc_count() == 0


def test_text_dedup_by_hash(client, auth_headers):
    d1 = _mk_text_doc(client, auth_headers, "同一份笔记")
    d2r = client.post(f"{BASE}/text", json={"text": "同一份笔记"}, headers=auth_headers)
    assert d2r.status_code == 200
    assert d2r.json()["id"] == d1["id"]
    assert d2r.json()["meta"].get("deduped") is True
    assert _doc_count() == 1


# --- 配额 ---


def test_kb_doc_quota_429(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.api.kb._MAX_KB_DOCS_PER_USER", 2)
    _mk_text_doc(client, auth_headers, "一")
    _mk_text_doc(client, auth_headers, "二")
    r = client.post(f"{BASE}/text", json={"text": "三"}, headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "quota_exceeded"
    # 去重命中不扣配额
    r2 = client.post(f"{BASE}/text", json={"text": "一"}, headers=auth_headers)
    assert r2.status_code == 200 and r2.json()["meta"].get("deduped") is True


# --- url ---


def test_url_ssrf_blocked_no_residue(client, auth_headers):
    """SSRF 拦截（真实 parse_material + verify_url）：400 且无行/文件残留。"""
    from app.core.storage import _kb_root

    r = client.post(f"{BASE}/url", json={"url": "http://127.0.0.1/"}, headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "ssrf_blocked"
    assert _doc_count() == 0
    assert not _kb_root(_first_user_id()).exists()


def test_url_success_mocked(client, auth_headers, monkeypatch):
    _stub_parse_ok(monkeypatch)
    r = client.post(f"{BASE}/url", json={"url": "https://example.com/page"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kind"] == "url" and d["ext"] == ""
    assert d["title"] == "https://example.com/page"[:80]
    assert d["has_original"] is False and d["parsed_chars"] > 0


def test_url_rate_limit(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.api.kb._URL_RATE_PER_MIN", 2)
    _stub_parse_ok(monkeypatch)
    for i in range(2):
        r = client.post(f"{BASE}/url", json={"url": f"https://example.com/{i}"}, headers=auth_headers)
        assert r.status_code == 200, r.text
    r = client.post(f"{BASE}/url", json={"url": "https://example.com/3"}, headers=auth_headers)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"


# --- 列表过滤 ---


def _seed_three(client, auth_headers):
    _mk_text_doc(client, auth_headers, "alpha 笔记")
    _mk_text_doc(client, auth_headers, "beta 笔记")
    _upload(client, auth_headers, name="gamma.md")


def test_list_filters(client, auth_headers):
    _seed_three(client, auth_headers)
    r = client.get(BASE, headers=auth_headers)
    assert r.status_code == 200 and len(r.json()["documents"]) == 3
    # 新→旧排序（id desc）
    ids = [d["id"] for d in r.json()["documents"]]
    assert ids == sorted(ids, reverse=True)

    r = client.get(BASE, params={"q": "alpha"}, headers=auth_headers)
    assert len(r.json()["documents"]) == 1
    r = client.get(BASE, params={"kind": "text"}, headers=auth_headers)
    assert len(r.json()["documents"]) == 2
    r = client.get(BASE, params={"kind": "file"}, headers=auth_headers)
    assert len(r.json()["documents"]) == 1
    r = client.get(BASE, params={"limit": 1}, headers=auth_headers)
    assert len(r.json()["documents"]) == 1
    r = client.get(BASE, params={"kind": "bogus"}, headers=auth_headers)
    assert r.status_code == 400


# --- CRUD 三件套（正常 / 他人 404 / 未认证 401） ---


def test_get_rename_delete_flow(client, auth_headers):
    d = _mk_text_doc(client, auth_headers, "原始内容")
    r = client.get(f"{BASE}/{d['id']}", headers=auth_headers)
    assert r.status_code == 200 and r.json()["id"] == d["id"]

    r = client.put(f"{BASE}/{d['id']}", json={"title": "新标题"}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["title"] == "新标题"

    r = client.delete(f"{BASE}/{d['id']}", headers=auth_headers)
    assert r.status_code == 200
    assert client.get(f"{BASE}/{d['id']}", headers=auth_headers).status_code == 404
    assert _doc_count() == 0


def test_rename_blank_400(client, auth_headers):
    d = _mk_text_doc(client, auth_headers, "x")
    r = client.put(f"{BASE}/{d['id']}", json={"title": "   "}, headers=auth_headers)
    assert r.status_code == 400


def test_other_user_404(client, auth_headers):
    """他人文档一律 404（防枚举）：get/put/delete 三件套。"""
    from app.core.security import hash_password

    d = _mk_text_doc(client, auth_headers, "私有文档")
    db = _db()
    try:
        db.add(models.User(name="bob", username="bob", password_hash=hash_password("pw1234")))
        db.commit()
    finally:
        db.close()
    r = client.post("/api/auth/login", json={"username": "bob", "password": "pw1234"})
    hb = {"Authorization": f"Bearer {r.json()['token']}"}

    assert client.get(f"{BASE}/{d['id']}", headers=hb).status_code == 404
    assert client.put(f"{BASE}/{d['id']}", json={"title": "y"}, headers=hb).status_code == 404
    assert client.delete(f"{BASE}/{d['id']}", headers=hb).status_code == 404
    # 列表隔离：bob 看不到他人文档
    assert client.get(BASE, headers=hb).json()["documents"] == []


def test_unauthenticated_401(client):
    assert client.get(BASE).status_code == 401
    assert client.post(f"{BASE}/text", json={"text": "x"}).status_code == 401
    assert client.post(f"{BASE}/upload", files={"file": ("a.md", BytesIO(b"x"), "text/markdown")}).status_code == 401


# --- 删除清理磁盘文件 ---


def test_delete_removes_files(client, auth_headers):
    from pathlib import Path

    d = _upload(client, auth_headers).json()
    _parse(client, auth_headers, d["id"])  # 解析后才有 parsed 文件（iter 225 三段式）
    original, parsed = _doc_paths(d["id"])
    assert client.delete(f"{BASE}/{d['id']}", headers=auth_headers).status_code == 200
    assert not Path(original).exists()
    assert not Path(parsed).exists()


def test_delete_pending_doc_removes_original(client, auth_headers):
    """待确认（pending，未解析）文档删除：原件清理（无 parsed 文件亦正常）。"""
    from pathlib import Path

    d = _upload(client, auth_headers).json()
    original, parsed = _doc_paths(d["id"])
    assert original and parsed is None
    assert client.delete(f"{BASE}/{d['id']}", headers=auth_headers).status_code == 200
    assert not Path(original).exists()
    assert _doc_count() == 0


# --- 删用户级联（行随 ORM 级联 + 磁盘目录清理） ---


def test_delete_user_cascades_kb(client, auth_headers):
    from app.core.security import hash_password
    from app.core.storage import _kb_root

    # 管理员（auth_headers  token 视角的首个用户即管理员回退目标）创建普通用户并登录
    db = _db()
    try:
        db.add(models.User(name="root", username="root", password_hash=hash_password("pw1234"), is_admin=True))
        db.add(models.User(name="kbu", username="kbu", password_hash=hash_password("pw1234")))
        db.commit()
    finally:
        db.close()
    ha = {"Authorization": f"Bearer {client.post('/api/auth/login', json={'username': 'root', 'password': 'pw1234'}).json()['token']}"}
    hu = {"Authorization": f"Bearer {client.post('/api/auth/login', json={'username': 'kbu', 'password': 'pw1234'}).json()['token']}"}

    d = _upload(client, hu).json()
    _mk_text_doc(client, hu, "kbu 的笔记")
    uid = _doc_owner(d["id"])
    assert _kb_root(uid).exists()

    r = client.delete(f"/api/users/{uid}", headers=ha)
    assert r.status_code == 200, r.text
    assert _doc_count() == 0
    assert not _kb_root(uid).exists()


def test_fresh_doc_id_collision_retry(monkeypatch):
    """uuid 截断撞名 -> 存在性检查重摇（iter 228 命名冲突防护，防静默覆盖既有原件）。"""
    from types import SimpleNamespace

    from app.api import kb as kb_mod

    originals = kb_mod.kb_originals_dir(1)
    (originals / f"{'a' * 12}.pdf").write_bytes(b"x")
    seq = iter([SimpleNamespace(hex="a" * 32), SimpleNamespace(hex="b" * 32)])
    monkeypatch.setattr(kb_mod.uuid, "uuid4", lambda: next(seq))
    assert kb_mod._fresh_doc_id(1) == "b" * 12


def test_kb_parse_lock_registry_bounded(client, auth_headers, monkeypatch):
    """解析锁台账有界（iter 232）：成功/失败解析后 _PARSE_LOCKS 不残留条目（防无界增长泄漏）。"""
    from app.api import kb as kb_mod
    from app.tools.base import ToolError, ToolOutput

    _stub_parse_ok(monkeypatch)
    doc_id = _upload(client, auth_headers, name="lock-a.md").json()["id"]
    _parse(client, auth_headers, doc_id)
    assert doc_id not in kb_mod._PARSE_LOCKS  # 成功路径清理

    async def _fail(inp, ctx):
        return ToolOutput(
            tool="parse_material", status="error", errors=[ToolError(code="parse_failed", message="坏文件")]
        )

    monkeypatch.setattr(kb_mod, "parse_material", _fail)
    doc_id2 = _upload(client, auth_headers, name="lock-b.md", body=b"distinct bad content").json()["id"]
    r = client.post(f"{BASE}/{doc_id2}/parse", json={"mode": "full"}, headers=auth_headers)
    assert r.status_code != 200
    assert doc_id2 not in kb_mod._PARSE_LOCKS  # 失败路径同样清理

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


def test_upload_md_success(client, auth_headers):
    r = _upload(client, auth_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kind"] == "file" and d["ext"] == "md"
    assert d["title"] == "a.md" and d["source"] == "a.md"
    assert d["has_original"] is True and d["parsed_chars"] > 0
    # 原件 + 解析文本落盘
    from pathlib import Path

    original, parsed = _doc_paths(d["id"])
    assert original and Path(original).exists()
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


def test_upload_parse_failure_rolls_back(client, auth_headers, monkeypatch):
    """解析失败：DB 行回滚 + 原件清理（防孤儿，err.md A15 同型）。"""
    from app.api import kb as kb_mod
    from app.core.storage import _kb_root
    from app.tools.base import ToolError, ToolOutput

    async def _fail(inp, ctx):
        return ToolOutput(
            tool="parse_material",
            status="error",
            data={},
            errors=[ToolError(code="empty_content", message="解析文本为空")],
        )

    monkeypatch.setattr(kb_mod, "parse_material", _fail)
    r = _upload(client, auth_headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "empty_content"
    assert _doc_count() == 0
    # 原件文件已清理（空目录壳可留，同 materials 会话目录行为；关键是无孤儿文件）
    originals = _kb_root(_first_user_id()) / "originals"
    assert not originals.exists() or list(originals.iterdir()) == []


def test_upload_dedup_by_hash(client, auth_headers):
    r1 = _upload(client, auth_headers)
    r2 = _upload(client, auth_headers)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] == r2.json()["id"]
    assert r2.json()["meta"].get("deduped") is True
    assert _doc_count() == 1


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


def test_upload_concurrent_same_user_409(client, auth_headers, monkeypatch):
    """err.md B22：同用户并发上传 -> 第二个 409（不再写-写互等 busy_timeout 后 500）。"""
    import threading

    started, release = threading.Event(), threading.Event()
    _stub_parse_slow(monkeypatch, started, release)

    first: dict = {}

    def _upload_first():
        first["r"] = client.post(
            f"{BASE}/upload",
            files={"file": ("a.md", BytesIO(b"# A"), "text/markdown")},
            headers=auth_headers,
        )

    t = threading.Thread(target=_upload_first)
    t.start()
    try:
        assert started.wait(5), "第一个上传未进入解析"
        r2 = client.post(
            f"{BASE}/upload",
            files={"file": ("b.md", BytesIO(b"# B"), "text/markdown")},
            headers=auth_headers,
        )
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == "conflict"
    finally:
        release.set()
        t.join(timeout=15)
    assert first["r"].status_code == 200, first["r"].text
    assert _doc_count() == 1


def test_upload_during_parse_other_write_not_locked(client, auth_headers, monkeypatch):
    """err.md B22 核心回归：上传解析（分钟级）期间其他写请求不再 database is locked 500。

    旧实现 flush INSERT 后在未提交写事务内 await parse_material，持写锁期间这里并发
    的 /documents/text INSERT 会等 busy_timeout(5s) 后 OperationalError -> 500。
    """
    import threading

    started, release = threading.Event(), threading.Event()
    _stub_parse_slow(monkeypatch, started, release)

    first: dict = {}

    def _upload_first():
        first["r"] = client.post(
            f"{BASE}/upload",
            files={"file": ("a.md", BytesIO(b"# A"), "text/markdown")},
            headers=auth_headers,
        )

    t = threading.Thread(target=_upload_first)
    t.start()
    try:
        assert started.wait(5), "第一个上传未进入解析"
        r = client.post(f"{BASE}/text", json={"text": "上传期间的笔记"}, headers=auth_headers)
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
    original, parsed = _doc_paths(d["id"])
    assert client.delete(f"{BASE}/{d['id']}", headers=auth_headers).status_code == 200
    assert not Path(original).exists()
    assert not Path(parsed).exists()


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

"""API 层测试：sessions + materials + 错误处理 + 鉴权 + 安全。"""

from __future__ import annotations

import asyncio
from io import BytesIO

import pytest

from app.api.errors import ApiError


def _session(client, auth_headers, title="t"):
    r = client.post("/api/sessions", json={"title": title}, headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


# --- sessions ---


def test_create_session(client, auth_headers):
    r = client.post("/api/sessions", json={"title": "学 React"}, headers=auth_headers)
    assert r.status_code == 200
    assert "session_id" in r.json()


def test_delete_session(client, auth_headers):
    sid = _session(client, auth_headers)
    r = client.delete(f"/api/sessions/{sid}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    # 再删 -> 404
    r2 = client.delete(f"/api/sessions/{sid}", headers=auth_headers)
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "not_found"


# --- materials: text ---


def test_text_material(client, auth_headers):
    sid = _session(client, auth_headers)
    r = client.post(
        "/api/materials/text",
        json={"session_id": sid, "text": "我想学 React，零基础，每天 1 小时"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "text"
    assert "material_id" in body


def test_text_empty(client, auth_headers):
    sid = _session(client, auth_headers)
    r = client.post(
        "/api/materials/text", json={"session_id": sid, "text": "   "}, headers=auth_headers
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "empty_content"


def test_material_session_not_found(client, auth_headers):
    r = client.post(
        "/api/materials/text", json={"session_id": 999999, "text": "x"}, headers=auth_headers
    )
    assert r.status_code == 404


# --- materials: upload ---


def test_upload_txt(client, auth_headers):
    sid = _session(client, auth_headers)
    r = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("note.txt", BytesIO(b"hello upload content"), "text/plain")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "file"


def test_upload_unsupported_ext(client, auth_headers):
    sid = _session(client, auth_headers)
    r = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("a.xlsx", BytesIO(b"fake"), "application/octet-stream")},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_format"


def test_upload_magic_mismatch(client, auth_headers):
    sid = _session(client, auth_headers)
    # 扩展名 .pdf 但内容非 %PDF
    r = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("fake.pdf", BytesIO(b"not a pdf"), "application/pdf")},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_format"


def test_upload_too_large(client, auth_headers, monkeypatch):
    from app.core import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "max_upload_mb", 1)
    sid = _session(client, auth_headers)
    big = b"x" * (2 * 1024 * 1024)  # 2MB > 1MB
    r = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("big.txt", BytesIO(big), "text/plain")},
        headers=auth_headers,
    )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"


def test_parse_concurrent_same_material_409_and_writes_not_locked(client, auth_headers, monkeypatch):
    """同资料并发解析 -> 第二个 409；解析（分钟级）期间其他写请求不再 locked 500。

    err.md B22：旧实现 flush INSERT 后在未提交写事务内 await parse_material，持写锁
    期间第二个写请求会等 busy_timeout(5s) 后 sqlite3.OperationalError -> 500。
    iter 225：上传不解析（秒级），解析独立端点 + per-material 互斥锁 + 事务外解析。
    """
    import threading

    from app.api import materials as materials_mod
    from app.tools.base import ToolOutput

    started, release = threading.Event(), threading.Event()

    async def _fake(inp, ctx):
        if inp.args.get("kind") == "file":
            started.set()
            await asyncio.to_thread(release.wait, 10)
        return ToolOutput(tool="parse_material", status="ok", data={"text": "解析文本", "meta": {}})

    monkeypatch.setattr(materials_mod, "parse_material", _fake)

    sid = _session(client, auth_headers)
    r = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("a.txt", BytesIO(b"aaa first content"), "text/plain")},
        headers=auth_headers,
    )
    assert r.status_code == 200 and r.json()["status"] == "pending"
    mid = r.json()["material_id"]

    first: dict = {}

    def _parse_first():
        first["r"] = client.post(
            f"/api/materials/{mid}/parse", json={"mode": "full"}, headers=auth_headers
        )

    t = threading.Thread(target=_parse_first)
    t.start()
    try:
        assert started.wait(5), "第一个解析未开始"
        # 同资料并发解析 -> 409（互斥锁）
        r2 = client.post(f"/api/materials/{mid}/parse", json={"mode": "fast"}, headers=auth_headers)
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == "conflict"
        # 解析期间其他写（/materials/text）不再被锁 -> 200
        r3 = client.post(
            "/api/materials/text",
            json={"session_id": sid, "text": "解析期间的文本资料"},
            headers=auth_headers,
        )
        assert r3.status_code == 200, r3.text
    finally:
        release.set()
        t.join(timeout=15)
    assert first["r"].status_code == 200, first["r"].text
    assert first["r"].json()["status"] == "parsed"


def test_material_upload_pending_confirm_parse_flow(client, auth_headers):
    """iter 225 三段式：上传（仅落原件 pending，不解析）-> 确认 -> 解析（parsed）。"""
    sid = _session(client, auth_headers)
    r = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("note.txt", BytesIO(b"hello upload content"), "text/plain")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "pending"
    mid = d["material_id"]
    # 清单可见 pending 状态
    items = client.get(f"/api/sessions/{sid}/materials", headers=auth_headers).json()["items"]
    assert items[0]["status"] == "pending" and items[0]["parsed_chars"] == 0

    r = client.post(f"/api/materials/{mid}/confirm", headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    # confirm 幂等
    assert client.post(f"/api/materials/{mid}/confirm", headers=auth_headers).json()["status"] == "confirmed"

    r = client.post(f"/api/materials/{mid}/parse", json={"mode": "full"}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "parsed"
    items = client.get(f"/api/sessions/{sid}/materials", headers=auth_headers).json()["items"]
    assert items[0]["status"] == "parsed" and items[0]["parsed_chars"] > 0


def test_material_upload_dedup_pending(client, auth_headers):
    """iter 225：dedup 命中任意状态行——重复上传同文件复用 pending 暂存，不重复建行。"""
    sid = _session(client, auth_headers)
    body = b"same content for dedup"
    r1 = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("a.txt", BytesIO(body), "text/plain")},
        headers=auth_headers,
    )
    r2 = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("a.txt", BytesIO(body), "text/plain")},
        headers=auth_headers,
    )
    assert r1.json()["material_id"] == r2.json()["material_id"]
    assert r2.json()["status"] == "pending"
    assert r2.json()["meta"].get("deduped") is True


def test_material_parse_text_material_400(client, auth_headers):
    """text/url 类资料无原件（创建时已解析入库）：调 parse -> 400。"""
    sid = _session(client, auth_headers)
    r = client.post(
        "/api/materials/text", json={"session_id": sid, "text": "文本资料"}, headers=auth_headers
    )
    mid = r.json()["material_id"]
    r = client.post(f"/api/materials/{mid}/parse", json={"mode": "full"}, headers=auth_headers)
    assert r.status_code == 400


def test_material_confirm_parse_isolation_404(client, auth_headers):
    """他人会话的资料：confirm/parse 均 404（不泄露存在性）。"""
    from app.db.base import SessionLocal

    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        # 先建管理员：AUTH_TOKEN(env token) 解析为「首个管理员/首个用户」，
        # 确保 auth_headers 身份是 admin 而非 other
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(admin)
        other = User(name="other", username="other", password_hash="x", is_admin=False)
        db.add(other)
        db.flush()
        sess = models.Session(user_id=other.id, title="他人会话")
        db.add(sess)
        db.flush()
        mat = models.Material(
            session_id=sess.id, kind="file", source="a.txt", parsed_chars=0, meta={},
            status="pending",
        )
        db.add(mat)
        db.commit()
        mid = mat.id
    finally:
        db.close()

    r = client.post(f"/api/materials/{mid}/confirm", headers=auth_headers)
    assert r.status_code == 404
    r = client.post(f"/api/materials/{mid}/parse", json={"mode": "full"}, headers=auth_headers)
    assert r.status_code == 404


def test_load_parsed_materials_excludes_unparsed(client, auth_headers):
    """iter 225：pending/confirmed 未解析资料不参与生成（_load_parsed_materials 跳过无文本条目）。"""
    from app.api.plans import _load_parsed_materials
    from app.core.storage import write_parsed_text
    from app.db.base import SessionLocal

    from app.db import models

    sid = _session(client, auth_headers)
    db = SessionLocal()
    try:
        m_pending = models.Material(
            session_id=sid, kind="file", source="p.txt", parsed_chars=0, meta={}, status="pending"
        )
        m_parsed = models.Material(
            session_id=sid, kind="file", source="ok.txt", parsed_chars=5, meta={}, status="parsed"
        )
        db.add_all([m_pending, m_parsed])
        db.flush()
        m_parsed.parsed_text_path = str(write_parsed_text(sid, m_parsed.id, "已解析文本"))
        db.commit()
        loaded = _load_parsed_materials(db, sid)
    finally:
        db.close()
    assert len(loaded) == 1
    assert loaded[0]["text"] == "已解析文本"


class _FakeUpload:
    """模拟 UploadFile：记录 read 调用的 n 上限，证明限量读取。"""

    def __init__(self, data: bytes, size: int | None = None) -> None:
        self._data = data
        self.size = size
        self.read_max_n = -1

    async def read(self, n: int = -1) -> bytes:
        if n > self.read_max_n:
            self.read_max_n = n
        if n < 0:
            return self._data
        return self._data[:n]


def test_read_capped_capped_read_rejects_over_limit():
    """size 未知（Content-Length 缺失/伪造）：限量读取 max+1 即拒，不全量读（防 OOM）。"""
    from app.api.materials import _read_capped

    fake = _FakeUpload(b"x" * 100, size=None)  # 100 字节，上限 10
    with pytest.raises(ApiError) as exc:
        asyncio.run(_read_capped(fake, 10))
    assert exc.value.code == "payload_too_large"
    assert fake.read_max_n == 11  # 仅读 max+1，未全量读 100


def test_read_capped_precheck_rejects_huge_size():
    """file.size 预检：超大文件直接拒，不读字节。"""
    from app.api.materials import _read_capped

    fake = _FakeUpload(b"x" * 100, size=999)  # size 远超上限 10
    with pytest.raises(ApiError) as exc:
        asyncio.run(_read_capped(fake, 10))
    assert exc.value.code == "payload_too_large"
    assert fake.read_max_n == -1  # 未触发 read


def test_read_capped_returns_within_limit():
    """限额内文件原样返回。"""
    from app.api.materials import _read_capped

    fake = _FakeUpload(b"hello", size=5)
    out = asyncio.run(_read_capped(fake, 10))
    assert out == b"hello"


def test_request_body_size_limit(client, auth_headers, monkeypatch):
    """超大请求体（Content-Length 超限）-> 413，路由解析前拒（防 OOM）。"""
    from app import main as main_mod

    monkeypatch.setattr(main_mod, "_MAX_BODY_BYTES", 10)
    r = client.post(
        "/api/materials/text",
        json={"session_id": 1, "text": "x" * 100},
        headers=auth_headers,
    )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"
    assert "details" in r.json()["error"]  # 与统一错误格式一致（含 details）


# --- materials: url (SSRF) ---


def test_url_ssrf_blocked(client, auth_headers):
    sid = _session(client, auth_headers)
    r = client.post(
        "/api/materials/url",
        json={"session_id": sid, "url": "http://127.0.0.1/"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "ssrf_blocked"


# --- 鉴权 ---


def test_auth_missing(client):
    r = client.post("/api/sessions", json={"title": "x"})
    assert r.status_code == 401


def test_auth_bearer_works(client, auth_headers):
    sid = _session(client, auth_headers)
    assert sid > 0


def test_auth_wrong_token(client):
    r = client.post("/api/sessions", json={"title": "x"}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_upload_image_png_passes_validation(client, auth_headers, monkeypatch):
    """P3：png 扩展名+magic 校验通过 -> 上传建 pending（iter 225 不解析）；
    解析层 dependency_missing（无 tesseract）-> parse 端点 400 清晰报错（R9）。"""
    sid = _session(client, auth_headers)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    r = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("p.png", BytesIO(png), "image/png")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    # 本环境无 tesseract -> dependency_missing（有 tesseract 的环境则应 200）
    r = client.post(
        f"/api/materials/{r.json()['material_id']}/parse",
        json={"mode": "full"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "dependency_missing"
    assert "tesseract" in r.json()["error"]["message"]


def test_upload_image_magic_mismatch(client, auth_headers):
    """扩展名 .png 但内容非 PNG magic -> 400 unsupported_format。"""
    sid = _session(client, auth_headers)
    r = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("fake.png", BytesIO(b"not a png"), "image/png")},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_format"


def test_material_parse_lock_registry_bounded(client, auth_headers, monkeypatch):
    """解析锁台账有界（iter 232）：成功/失败解析后 _PARSE_LOCKS 不残留条目（防无界增长泄漏）。"""
    from app.api import materials as materials_mod
    from app.tools.base import ToolError, ToolOutput

    async def _ok(inp, ctx):
        return ToolOutput(tool="parse_material", status="ok", data={"text": "解析文本", "meta": {}})

    monkeypatch.setattr(materials_mod, "parse_material", _ok)
    sid = _session(client, auth_headers)
    r = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("lk1.txt", BytesIO(b"lock registry content one"), "text/plain")},
        headers=auth_headers,
    )
    mid = r.json()["material_id"]
    r = client.post(f"/api/materials/{mid}/parse", json={"mode": "full"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert mid not in materials_mod._PARSE_LOCKS  # 成功路径清理

    async def _fail(inp, ctx):
        return ToolOutput(
            tool="parse_material", status="error", errors=[ToolError(code="parse_failed", message="坏文件")]
        )

    monkeypatch.setattr(materials_mod, "parse_material", _fail)
    r = client.post(
        "/api/materials/upload",
        data={"session_id": sid},
        files={"file": ("lk2.txt", BytesIO(b"lock registry content two"), "text/plain")},
        headers=auth_headers,
    )
    mid2 = r.json()["material_id"]
    r = client.post(f"/api/materials/{mid2}/parse", json={"mode": "full"}, headers=auth_headers)
    assert r.status_code != 200
    assert mid2 not in materials_mod._PARSE_LOCKS  # 失败路径同样清理

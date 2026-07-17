"""API 层测试：sessions + materials + 错误处理 + 鉴权 + 安全。"""

from __future__ import annotations

from io import BytesIO


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

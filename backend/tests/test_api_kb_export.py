"""知识库原件/解析文本/ZIP 导出测试（iter 214）。"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

from app.db import models

BASE = "/api/kb/documents"
EXPORT = "/api/kb/export"


def _db():
    from app.db.base import SessionLocal

    return SessionLocal()


def _doc_row(doc_id: int):
    db = _db()
    try:
        d = db.get(models.KbDocument, doc_id)
        # 取出需要的字段，避免 detached 访问
        return (d.original_path, d.parsed_text_path, d.user_id) if d else (None, None, None)
    finally:
        db.close()


def _upload(client, headers, name="a.md", body=None, mime="text/markdown") -> dict:
    if body is None:
        body = "# 标题\n\n知识库内容".encode("utf-8")
    r = client.post(f"{BASE}/upload", files={"file": (name, BytesIO(body), mime)}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _mk_text_doc(client, headers, text="知识库笔记", title=None) -> dict:
    payload = {"text": text}
    if title is not None:
        payload["title"] = title
    r = client.post(f"{BASE}/text", json=payload, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _other_user_headers(client) -> dict:
    from app.core.security import hash_password

    db = _db()
    try:
        db.add(models.User(name="bob", username="bob", password_hash=hash_password("pw1234")))
        db.commit()
    finally:
        db.close()
    r = client.post("/api/auth/login", json={"username": "bob", "password": "pw1234"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# --- raw：双模式 + media_type + 三件套 ---


def test_raw_inline(client, auth_headers):
    d = _upload(client, auth_headers)
    r = client.get(f"{BASE}/{d['id']}/raw", headers=auth_headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "inline" in r.headers["content-disposition"]
    assert "知识库内容" in r.text


def test_raw_download_attachment(client, auth_headers):
    d = _upload(client, auth_headers, name="笔记.md")
    r = client.get(f"{BASE}/{d['id']}/raw", params={"download": 1}, headers=auth_headers)
    assert r.status_code == 200
    disp = r.headers["content-disposition"]
    assert "attachment" in disp
    assert "filename*=utf-8''" in disp  # 中文文件名 RFC 5987 编码


def test_raw_pdf_media_type(client, auth_headers, monkeypatch):
    """media_type 静态映射：pdf -> application/pdf（解析层 stub，与本测试关注点无关）。"""
    from app.api import kb as kb_mod
    from app.tools.base import ToolOutput

    async def _fake(inp, ctx):
        return ToolOutput(tool="parse_material", status="ok", data={"text": "PDF 解析文本", "meta": {}})

    monkeypatch.setattr(kb_mod, "parse_material", _fake)
    d = _upload(client, auth_headers, name="x.pdf", body=b"%PDF-1.4 fake", mime="application/pdf")
    r = client.get(f"{BASE}/{d['id']}/raw", headers=auth_headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"


def test_raw_no_original_404(client, auth_headers):
    """text 类文档无原件 -> raw 404（查看器走 parsed 视图）。"""
    d = _mk_text_doc(client, auth_headers)
    assert client.get(f"{BASE}/{d['id']}/raw", headers=auth_headers).status_code == 404


def test_raw_missing_file_404(client, auth_headers):
    d = _upload(client, auth_headers)
    original, _, _ = _doc_row(d["id"])
    Path(original).unlink()
    assert client.get(f"{BASE}/{d['id']}/raw", headers=auth_headers).status_code == 404


def test_raw_other_user_404(client, auth_headers):
    d = _upload(client, auth_headers)
    hb = _other_user_headers(client)
    assert client.get(f"{BASE}/{d['id']}/raw", headers=hb).status_code == 404
    assert client.get(f"{BASE}/{d['id']}/parsed", headers=hb).status_code == 404


def test_raw_unauthenticated_401(client):
    assert client.get(f"{BASE}/1/raw").status_code == 401


# --- parsed ---


def test_parsed_text_doc(client, auth_headers):
    d = _mk_text_doc(client, auth_headers, "解析前的原文", title="我的笔记")
    r = client.get(f"{BASE}/{d['id']}/parsed", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["document_id"] == d["id"] and body["title"] == "我的笔记"
    assert "解析前的原文" in body["text"]
    assert body["parsed_chars"] > 0 and body["truncated"] is False


def test_parsed_missing_file_404(client, auth_headers):
    d = _mk_text_doc(client, auth_headers)
    _, parsed, _ = _doc_row(d["id"])
    Path(parsed).unlink()
    r = client.get(f"{BASE}/{d['id']}/parsed", headers=auth_headers)
    assert r.status_code == 404
    assert parsed not in r.text  # 不回显服务器路径（R35）


# --- ZIP 导出 ---


def _read_zip(content: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(BytesIO(content))


def test_export_zip_entries(client, auth_headers):
    d1 = _upload(client, auth_headers, name="指南.md")
    d2 = _mk_text_doc(client, auth_headers, "纯文本笔记内容", title="笔记A")
    r = client.get(EXPORT, headers=auth_headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]

    zf = _read_zip(r.content)
    names = zf.namelist()
    assert "index.md" in names
    # 上传文档：原件 + 解析文本；text 文档：仅解析文本（无原件，三路径一致）
    assert f"originals/{d1['id']}-指南.md" in names
    assert f"parsed/{d1['id']}-指南.txt" in names
    assert f"parsed/{d2['id']}-笔记A.txt" in names
    assert not any(n.startswith(f"originals/{d2['id']}-") for n in names)
    # index 清单内容
    index = zf.read("index.md").decode("utf-8")
    assert "文档数：2" in index and "指南" in index and "笔记A" in index
    # 解析文本内容完整
    assert "纯文本笔记内容" in zf.read(f"parsed/{d2['id']}-笔记A.txt").decode("utf-8")


def test_export_zip_original_bytes_integrity(client, auth_headers):
    """1MB 原件经流式写入 zip 后字节完整（Spooled+copyfileobj 路径）。"""
    payload = ("x" * 1024 * 1024).encode("utf-8")  # 1MB
    d = _upload(client, auth_headers, name="big.txt", body=payload, mime="text/plain")
    r = client.get(EXPORT, headers=auth_headers)
    assert r.status_code == 200
    zf = _read_zip(r.content)
    entry = f"originals/{d['id']}-big.txt"
    assert zf.read(entry) == payload


def test_export_ids_filter(client, auth_headers):
    d1 = _mk_text_doc(client, auth_headers, "第一篇")
    d2 = _mk_text_doc(client, auth_headers, "第二篇")
    r = client.get(EXPORT, params={"ids": str(d1["id"])}, headers=auth_headers)
    assert r.status_code == 200
    names = _read_zip(r.content).namelist()
    assert any(n.startswith(f"parsed/{d1['id']}-") for n in names)
    assert not any(n.startswith(f"parsed/{d2['id']}-") for n in names)


def test_export_excludes_other_users_ids(client, auth_headers):
    """ids 指向他人文档 -> 静默排除；过滤后为空 -> 404。"""
    d = _mk_text_doc(client, auth_headers, "私有")
    hb = _other_user_headers(client)
    r = client.get(EXPORT, params={"ids": str(d["id"])}, headers=hb)
    assert r.status_code == 404


def test_export_empty_library_404(client, auth_headers):
    assert client.get(EXPORT, headers=auth_headers).status_code == 404


def test_export_unauthenticated_401(client):
    assert client.get(EXPORT).status_code == 401

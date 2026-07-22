"""自由笔记导入测试（iter 292）：.md/.zip 导入 / 标题拆分 / 非 .md 与坏 zip 跳过 / 401。"""

from __future__ import annotations

import zipfile
from io import BytesIO


def _md(title: str, body: str = "") -> bytes:
    return f"# {title}\n\n{body}".encode("utf-8")


def test_import_md_files(client, auth_headers):
    files = [
        ("files", ("a.md", BytesIO(_md("标题甲", "正文甲")), "text/markdown")),
        ("files", ("b.md", BytesIO(_md("标题乙", "正文乙")), "text/markdown")),
    ]
    r = client.post("/api/notes/import", files=files, headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["imported"] == 2
    assert body["skipped"] == 0
    titles = {n["title"] for n in client.get("/api/notes", headers=auth_headers).json()["items"]}
    assert {"标题甲", "标题乙"} <= titles


def test_import_title_fallback(client, auth_headers):
    # 无 '# ' 行 -> 标题取文件名 stem，正文为全文
    files = [("files", ("无标题笔记.md", BytesIO("只有正文内容".encode("utf-8")), "text/markdown"))]
    r = client.post("/api/notes/import", files=files, headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["imported"] == 1
    n = client.get("/api/notes", headers=auth_headers).json()["items"][0]
    assert n["title"] == "无标题笔记"
    assert n["content"] == "只有正文内容"


def test_import_zip_ignores_non_md(client, auth_headers):
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.md", _md("压缩甲", "内容甲"))
        zf.writestr("sub/y.md", _md("压缩乙", "内容乙"))
        zf.writestr("ignore.txt", b"not markdown")  # 非 .md 静默忽略
    buf.seek(0)
    r = client.post("/api/notes/import", files=[("files", ("notes.zip", buf, "application/zip"))], headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["imported"] == 2  # ignore.txt 不计入
    titles = {n["title"] for n in client.get("/api/notes", headers=auth_headers).json()["items"]}
    assert {"压缩甲", "压缩乙"} <= titles


def test_import_bad_zip_skipped(client, auth_headers):
    r = client.post(
        "/api/notes/import",
        files=[("files", ("bad.zip", BytesIO(b"not a zip"), "application/zip"))],
        headers=auth_headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["imported"] == 0
    assert body["skipped"] == 1


def test_import_requires_auth(client):
    assert (
        client.post(
            "/api/notes/import",
            files=[("files", ("a.md", BytesIO(b"# x"), "text/markdown"))],
        ).status_code
        == 401
    )

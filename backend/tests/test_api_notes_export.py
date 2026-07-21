"""自由笔记 ZIP 导出测试（iter 286）：内容/清单/隔离/空态 404/401。"""

from __future__ import annotations

import zipfile
from datetime import datetime
from io import BytesIO

from app.db.base import SessionLocal


def _seed():
    from app.db import models
    from app.db.models import User

    db = SessionLocal()
    try:
        admin = User(name="admin", username="admin", password_hash="", is_admin=True)
        other = User(name="other", username="other", password_hash="x", is_admin=False)
        db.add_all([admin, other])
        db.flush()
        now = datetime.now()
        db.add_all(
            [
                models.Note(user_id=admin.id, title="笔记一", content="笔记一内容\n第二行", updated_at=now),
                models.Note(user_id=admin.id, title="笔记二", content="笔记二内容", updated_at=now),
                models.Note(user_id=other.id, title="他人笔记", content="他人内容", updated_at=now),  # 隔离
            ]
        )
        db.commit()
    finally:
        db.close()


def test_notes_export_zip_content_and_isolation(client, auth_headers):
    _seed()
    r = client.get("/api/notes/export", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "application/zip" in r.headers.get("content-type", "")
    zf = zipfile.ZipFile(BytesIO(r.content))
    names = zf.namelist()
    assert "index.md" in names
    md_names = [n for n in names if n.endswith(".md") and n != "index.md"]
    assert len(md_names) == 2  # 他人笔记不导出
    blob = "\n".join(zf.read(n).decode("utf-8") for n in md_names)
    assert "笔记一内容" in blob and "笔记二内容" in blob
    assert "他人" not in blob  # 隔离
    # 每篇 .md 以一级标题开头
    for n in md_names:
        assert zf.read(n).decode("utf-8").lstrip().startswith("# ")


def test_notes_export_index_counts(client, auth_headers):
    _seed()
    zf = zipfile.ZipFile(BytesIO(client.get("/api/notes/export", headers=auth_headers).content))
    idx = zf.read("index.md").decode("utf-8")
    assert "笔记数：2" in idx


def test_notes_export_empty_404(client, auth_headers):
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="admin", username="admin", password_hash="", is_admin=True))
        db.commit()
    finally:
        db.close()
    r = client.get("/api/notes/export", headers=auth_headers)
    assert r.status_code == 404


def test_notes_export_requires_auth(client):
    assert client.get("/api/notes/export").status_code == 401

"""备份含独立自由笔记测试（iter 227）：导出带 notes / 导入追加还原 /
双空条目跳过 / 旧备份无 notes 字段兼容。"""

from __future__ import annotations

import io
import json

from app.db import models
from app.db.base import SessionLocal
from app.db.models import User


def _seed_admin() -> None:
    db = SessionLocal()
    try:
        if db.query(User).filter_by(username="admin").first() is None:
            db.add(User(name="admin", username="admin", password_hash="", is_admin=True))
            db.commit()
    finally:
        db.close()


def _export(client, headers) -> dict:
    r = client.get("/api/backup/export", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _import(client, payload: dict, headers):
    blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return client.post(
        "/api/backup/import",
        files={"file": ("backup.json", io.BytesIO(blob), "application/json")},
        headers=headers,
    )


def _notes_of(user_id: int) -> list[tuple[str, str]]:
    db = SessionLocal()
    try:
        return [
            (n.title, n.content)
            for n in db.query(models.Note).filter_by(user_id=user_id).order_by(models.Note.id).all()
        ]
    finally:
        db.close()


def _my_id(client, headers) -> int:
    return client.get("/api/auth/me", headers=headers).json()["id"]


def test_backup_export_includes_notes(client, auth_headers):
    _seed_admin()
    client.post("/api/notes", json={"title": "笔记甲", "content": "内容甲"}, headers=auth_headers)
    client.post("/api/notes", json={"title": "", "content": "无题笔记"}, headers=auth_headers)
    data = _export(client, auth_headers)
    assert [(n["title"], n["content"]) for n in data["notes"]] == [("笔记甲", "内容甲"), ("", "无题笔记")]


def test_backup_import_restores_notes(client, auth_headers):
    _seed_admin()
    payload = {
        "format": "deep-tutor-backup",
        "version": 1,
        "exported_at": "2026-07-21T10:00:00",
        "user": {"username": "demo", "name": "Demo"},
        "provider_profiles": [],
        "notes": [
            {"title": "还原笔记", "content": "还原内容", "updated_at": "2026-07-20T08:00:00"},
            {"title": "", "content": ""},  # 双空跳过
            "garbage",  # 非 dict 跳过
        ],
        "sessions": [],
    }
    r = _import(client, payload, auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == 1
    assert _notes_of(_my_id(client, auth_headers)) == [("还原笔记", "还原内容")]


def test_backup_import_legacy_without_notes_key(client, auth_headers):
    """旧备份（iter 227 前）无 notes 字段：兼容还原，notes 计数为 0。"""
    _seed_admin()
    payload = {
        "format": "deep-tutor-backup",
        "version": 1,
        "exported_at": "2026-07-19T10:00:00",
        "user": {"username": "demo", "name": "Demo"},
        "provider_profiles": [],
        "sessions": [],
    }
    r = _import(client, payload, auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == 0


def test_backup_preview_counts_notes(client, auth_headers):
    _seed_admin()
    client.post("/api/notes", json={"title": "预检笔记", "content": "x"}, headers=auth_headers)
    data = _export(client, auth_headers)
    blob = json.dumps(data, ensure_ascii=False).encode("utf-8")
    r = client.post(
        "/api/backup/preview",
        files={"file": ("backup.json", io.BytesIO(blob), "application/json")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == 1

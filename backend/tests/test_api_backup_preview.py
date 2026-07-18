"""备份导入预检测试（iter 159）：计数正确 / 不落库 / 非法文件 400 / 401。"""

from __future__ import annotations

import io
import json

from app.db.base import SessionLocal

_VALID = {
    "format": "deep-tutor-backup",
    "version": 1,
    "exported_at": "2026-07-19T10:00:00",
    "user": {"username": "demo", "name": "Demo"},
    "provider_profiles": [{"name": "p1", "base_url": "https://x", "model": "m", "is_active": True}],
    "sessions": [
        {
            "title": "S1",
            "created_at": None,
            "materials": [{"kind": "text", "source": "t", "parsed_text": "x", "parsed_chars": 1, "meta": {}, "content_hash": None, "created_at": None}],
            "plans": [
                {
                    "version": 1,
                    "created_at": None,
                    "content": {"intent": {"topic": "T"}},
                    "knowledge_nodes": [
                        {"sid": "a", "name": "A", "difficulty": 1, "must_learn": True, "parent_sid": None, "note": "笔记"},
                        {"sid": "b", "name": "B", "difficulty": 2, "must_learn": False, "parent_sid": "a"},
                    ],
                    "resources": [],
                    "progress": [{"node_sid": "a", "status": "done", "mastery": 0.9}],
                    "learning_logs": [{"date": "2026-07-19", "minutes": 30, "note": None}],
                    "quizzes": [{"node_sid": "a", "type": "choice", "payload": {"question": "?", "answer": "!"}, "attempts": []}],
                    "study_units": [],
                }
            ],
        },
        {"title": "S2", "created_at": None, "materials": [], "plans": []},
    ],
}


def _upload(client, payload, headers):
    blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return client.post(
        "/api/backup/preview",
        files={"file": ("backup.json", io.BytesIO(blob), "application/json")},
        headers=headers,
    )


def test_backup_preview_counts(client, auth_headers):
    r = _upload(client, _VALID, auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1
    assert body["exported_at"] == "2026-07-19T10:00:00"
    assert body["username"] == "demo"
    assert body["sessions"] == 2
    assert body["plans"] == 1
    assert body["materials"] == 1
    assert body["knowledge_nodes"] == 2
    assert body["node_notes"] == 1  # 仅节点 A 有 note
    assert body["progress"] == 1
    assert body["learning_logs"] == 1
    assert body["quizzes"] == 1
    assert body["study_units"] == 0
    assert body["provider_profiles"] == 1


def test_backup_preview_does_not_write_db(client, auth_headers):
    from app.db import models

    _upload(client, _VALID, auth_headers)
    db = SessionLocal()
    try:
        assert db.query(models.Session).count() == 0
        assert db.query(models.Plan).count() == 0
        assert db.query(models.LlmProviderProfile).count() == 0
    finally:
        db.close()


def test_backup_preview_invalid(client, auth_headers):
    # 非 JSON
    r = client.post(
        "/api/backup/preview",
        files={"file": ("b.json", io.BytesIO(b"not json"), "application/json")},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_backup"
    # format 不匹配
    r = _upload(client, {"format": "other", "version": 1, "sessions": []}, auth_headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_backup"
    # 版本过新
    r = _upload(client, {"format": "deep-tutor-backup", "version": 99, "sessions": []}, auth_headers)
    assert r.status_code == 400


def test_backup_preview_requires_auth(client):
    r = client.post(
        "/api/backup/preview",
        files={"file": ("b.json", io.BytesIO(b"{}"), "application/json")},
    )
    assert r.status_code == 401

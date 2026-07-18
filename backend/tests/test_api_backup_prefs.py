"""备份含用户偏好测试（iter 177）：导出带 prefs / 导入还原本机没有的键 /
本机已有键跳过不覆盖 / 旧备份无 prefs 字段兼容还原。"""

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


def test_backup_export_includes_prefs(client, auth_headers):
    _seed_admin()
    client.put("/api/settings/prefs", json={"daily_budget_usd": 3.5}, headers=auth_headers)
    data = _export(client, auth_headers)
    assert data["prefs"] == {"daily_budget_usd": "3.5"}


def test_backup_import_restores_prefs(client, auth_headers):
    _seed_admin()
    payload = {
        "format": "deep-tutor-backup",
        "version": 1,
        "exported_at": "2026-07-19T10:00:00",
        "user": {"username": "demo", "name": "Demo"},
        "provider_profiles": [],
        "prefs": {"daily_budget_usd": "7.25"},
        "sessions": [],
    }
    r = _import(client, payload, auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["prefs"] == 1
    # 有效日预算贯通（user_prefs 行已还原）
    u = client.get("/api/usage", headers=auth_headers).json()
    assert u["daily_budget_usd"] == 7.25


def test_backup_import_skips_existing_pref_key(client, auth_headers):
    _seed_admin()
    client.put("/api/settings/prefs", json={"daily_budget_usd": 9.0}, headers=auth_headers)
    payload = {
        "format": "deep-tutor-backup",
        "version": 1,
        "exported_at": "2026-07-19T10:00:00",
        "user": {"username": "demo", "name": "Demo"},
        "provider_profiles": [],
        "prefs": {"daily_budget_usd": "1.0", "future_key": "v"},
        "sessions": [],
    }
    r = _import(client, payload, auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prefs"] == 1  # 仅 future_key 入；已有 daily_budget_usd 不覆盖
    assert any("daily_budget_usd" in s for s in body["skipped"])
    # 本机值未被备份值覆盖
    u = client.get("/api/usage", headers=auth_headers).json()
    assert u["daily_budget_usd"] == 9.0
    db = SessionLocal()
    try:
        assert db.query(models.UserPref).filter_by(key="future_key").count() == 1
    finally:
        db.close()


def test_backup_import_legacy_without_prefs(client, auth_headers):
    """旧版备份（无 prefs 字段）照常还原，prefs 计数为 0。"""
    _seed_admin()
    payload = {
        "format": "deep-tutor-backup",
        "version": 1,
        "exported_at": "2026-07-18T10:00:00",
        "user": {"username": "demo", "name": "Demo"},
        "provider_profiles": [],
        "sessions": [],
    }
    r = _import(client, payload, auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["prefs"] == 0

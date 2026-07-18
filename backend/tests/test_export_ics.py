"""P5 导出测试：ics 日历（build_ics + /export format=ics）。"""

from __future__ import annotations

import pytest

from app.api.errors import ApiError
from app.tools.build_ics import build_ics

PLAN = {
    "title": "Python 入门",
    "stages": [{"index": 0, "name": "基础语法"}, {"index": 1, "name": "进阶, 实战"}],
    "schedule": [
        {"date": "2026-07-20", "stage_index": 0, "tasks": ["学变量; 类型", "写第一行, 代码"], "est_minutes": 60},
        {"date": "2026-07-21", "stage_index": 1, "tasks": ["项目\n实战"], "est_minutes": 90},
    ],
}


def _unfold(ics: str) -> list[str]:
    """还原折叠行（续行以空格开头），返回逻辑行。"""
    out: list[str] = []
    for line in ics.split("\r\n"):
        if line.startswith(" ") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def test_build_ics_basic():
    ics, events, skipped = build_ics(7, PLAN)
    assert events == 2 and skipped == 0
    lines = _unfold(ics)
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-2] == "END:VCALENDAR"  # 末尾 CRLF 产生空尾行
    assert any(l == "DTSTART;VALUE=DATE:20260720" for l in lines)
    assert any(l.startswith("UID:dt-7-0-2026-07-20@deep-tutor") for l in lines)
    assert any("阶段1 基础语法" in l and l.startswith("SUMMARY:") for l in lines)
    assert any("预计时长：60 分钟" in l for l in lines)
    assert any(l.startswith("X-WR-CALNAME:") and "Python 入门" in l for l in lines)


def test_build_ics_escapes_special_chars():
    ics, _, _ = build_ics(7, PLAN)
    lines = _unfold(ics)
    # 逗号/分号/换行按 RFC 转义（阶段名「进阶, 实战」、任务「学变量; 类型」「项目\n实战」）
    assert any("进阶\\, 实战" in l for l in lines)
    assert any("学变量\\; 类型" in l for l in lines)
    assert any("项目\\n实战" in l.replace("\\\\n", "\\n") or "项目\\n实战" in l for l in lines)


def test_build_ics_crlf_and_folding():
    long_tasks = [{"date": "2026-07-20", "stage_index": 0, "tasks": ["很" * 100], "est_minutes": 1}]
    ics, _, _ = build_ics(1, {"title": "t", "schedule": long_tasks})
    assert "\r\n" in ics and "\n" not in ics.replace("\r\n", "")
    for raw_line in ics.split("\r\n"):
        assert len(raw_line.encode("utf-8")) <= 75


def test_build_ics_skips_bad_dates():
    plan = {"schedule": [{"date": "not-a-date", "tasks": ["x"]}, {"date": "2026-07-20", "tasks": ["y"]}]}
    ics, events, skipped = build_ics(1, plan)
    assert events == 1 and skipped == 1
    assert "20260720" in ics


def test_build_ics_empty_schedule_raises():
    with pytest.raises(ValueError, match="无有效日程"):
        build_ics(1, {"schedule": []})
    with pytest.raises(ValueError):
        build_ics(1, {"schedule": [{"date": "bad"}]})


# ---------- 端点 ----------


def _mk_plan(client, auth_headers, content) -> int:
    from app.db.base import SessionLocal
    from app.db.models import Plan, Session as Sess

    me = client.get("/api/auth/me", headers=auth_headers).json()
    db = SessionLocal()
    try:
        s = Sess(user_id=me["id"])
        db.add(s)
        db.flush()
        p = Plan(session_id=s.id, content=content)
        db.add(p)
        db.commit()
        return p.id
    finally:
        db.close()


def _second_user_headers(client) -> dict:
    from app.core.security import hash_password
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        db.add(User(name="u2", username="u2", password_hash=hash_password("pw12345678")))
        db.commit()
    finally:
        db.close()
    r = client.post("/api/auth/login", json={"username": "u2", "password": "pw12345678"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_export_ics_endpoint(client, auth_headers):
    pid = _mk_plan(client, auth_headers, PLAN)
    r = client.post(f"/api/export/{pid}", json={"format": "ics"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "ics" and body["event_count"] == 2
    assert body["filename"].endswith(".ics")
    assert body["ics"].startswith("BEGIN:VCALENDAR")


def test_export_ics_no_schedule_422(client, auth_headers):
    pid = _mk_plan(client, auth_headers, {"title": "x", "schedule": []})
    r = client.post(f"/api/export/{pid}", json={"format": "ics"}, headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "no_schedule"


def test_export_ics_skip_warning(client, auth_headers):
    plan = {"schedule": [{"date": "bad"}, {"date": "2026-07-20", "tasks": ["y"]}]}
    pid = _mk_plan(client, auth_headers, plan)
    r = client.post(f"/api/export/{pid}", json={"format": "ics"}, headers=auth_headers)
    assert r.status_code == 200
    assert "warning" in r.json() and r.json()["event_count"] == 1


def test_export_ics_cross_user_404(client, auth_headers):
    pid = _mk_plan(client, auth_headers, PLAN)
    r2 = client.post(f"/api/export/{pid}", json={"format": "ics"}, headers=_second_user_headers(client))
    assert r2.status_code == 404


def test_export_ics_unauthenticated_401(client):
    assert client.post("/api/export/1", json={"format": "ics"}).status_code == 401


def test_api_error_no_schedule_registered():
    assert ApiError("no_schedule", "x").status == 422
    assert ApiError("no_quizzes", "x").status == 422

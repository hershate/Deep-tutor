"""iter 241 安全加固回归测试：

- 登录限流账号维度聚合桶（防多 IP 分布式爆破绕过 ip+username 桶）
- test_provider 错误不回显异常原文（自定义 base_url 内网探测回读通道）
- 解析错误统一清洗服务器绝对路径（R35/A10 不回显路径规则补全）
- export_plan 工具路径清洗（插件/外部字符串调用点纵深防御）
- quiz 作答内容大小上限（防 DB 膨胀）
- 备份导入配额（会话/资料/笔记）与 learning_logs 分钟数钳制
"""

from __future__ import annotations

import asyncio
import json

from app.db.base import SessionLocal


def _mk_admin():
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(name="admin", username="admin", password_hash="", is_admin=True)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


# --- 登录账号维度限流桶 ---


def test_login_checks_account_bucket(client, monkeypatch):
    from app.core.ratelimit import limiter

    keys: list[str] = []
    orig = limiter.check

    def spy(key, n):
        keys.append(key)
        return orig(key, n)

    monkeypatch.setattr(limiter, "check", spy)
    r = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    assert r.status_code == 401
    assert any(k.startswith("login:acct:") for k in keys), f"缺账号维度限流桶：{keys}"


# --- test_provider 错误不回显响应正文 ---


def test_test_provider_error_no_body_echo(client, auth_headers, monkeypatch):
    r = client.post(
        "/api/providers",
        json={"name": "内网", "base_url": "http://192.168.1.1:8080", "api_key": "sk-x"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    class _FakeProvider:
        def __init__(self, **kw):
            pass

        async def chat(self, *a, **kw):
            raise Exception("HTTP 500 from internal: SENSITIVE_INTERNAL_BODY")

    monkeypatch.setattr("app.api.providers.AnthropicProvider", _FakeProvider)
    r = client.post(f"/api/providers/{pid}/test", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert "SENSITIVE_INTERNAL_BODY" not in r.text  # 异常正文不回显
    assert body["error"]  # 仍有可用排障信息（异常类型名）


# --- 解析错误路径清洗 ---


def test_parse_error_scrubs_server_paths():
    from app.core.settings import settings
    from app.tools.parse_material import _ParseError, _scrub_server_paths

    msg = f"marker 失败: {settings.data_dir}/sessions/1/originals/3.pdf corrupt"
    out = _scrub_server_paths(msg)
    assert settings.data_dir not in out
    assert "<服务器路径>" in out
    # _ParseError 构造处集中清洗（覆盖全部解析错误出口）
    e = _ParseError("degraded", msg)
    assert settings.data_dir not in e.message


# --- export_plan 路径清洗 ---


def test_export_plan_path_traversal_safe():
    from app.agent.context import ExecContext
    from app.tools.base import ToolInput
    from app.tools.export_plan import export_plan

    from app.core.settings import settings as st

    inp = ToolInput(
        tool="export_plan",
        session_id="../../evil",
        args={"plan_id": "../../pwn", "plan": {"intent": {"topic": "t"}}},
    )
    ctx = ExecContext(session_id="x", trace_id="t", data_dir=st.data_dir)
    out = asyncio.run(export_plan(inp, ctx))
    assert out.status == "ok"
    path = out.data["file_url"]
    assert ".." not in path
    from pathlib import Path

    assert str(Path(path).resolve()).startswith(str(Path(st.data_dir).resolve()))


# --- quiz 作答大小上限 ---


def _seed_quiz() -> int:
    from app.db import models

    uid = _mk_admin()
    db = SessionLocal()
    try:
        sess = models.Session(user_id=uid, title="s")
        db.add(sess)
        db.flush()
        plan = models.Plan(session_id=sess.id, version=1, content={"intent": {"topic": "t"}})
        db.add(plan)
        db.flush()
        q = models.Quiz(plan_id=plan.id, type=models.QuizType.short, payload={"question": "题"})
        db.add(q)
        db.commit()
        db.refresh(q)
        return q.id
    finally:
        db.close()


def test_quiz_submit_oversized_answer_422(client, auth_headers):
    qid = _seed_quiz()
    r = client.post(
        f"/api/quizzes/{qid}/submit",
        json={"user_answer": {"text": "x" * 20_000}},
        headers=auth_headers,
    )
    assert r.status_code == 422, r.text
    # 正常大小仍可提交
    r = client.post(f"/api/quizzes/{qid}/submit", json={"user_answer": {"text": "正常作答"}}, headers=auth_headers)
    assert r.status_code == 200, r.text


# --- 备份导入配额与分钟钳制 ---


def _import(client, headers, payload: dict):
    blob = json.dumps(payload).encode("utf-8")
    return client.post("/api/backup/import", files={"file": ("backup.json", blob, "application/json")}, headers=headers)


def test_backup_import_materials_quota(client, auth_headers):
    _mk_admin()
    payload = {
        "format": "deep-tutor-backup",
        "version": 1,
        "sessions": [
            {
                "title": "s",
                "materials": [
                    {"kind": "text", "source": f"资料{i}", "parsed_chars": 1, "parsed_text": "t"}
                    for i in range(25)
                ],
                "plans": [],
            }
        ],
    }
    r = _import(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["materials"] == 20  # 每会话 20 份上限，超出跳过
    assert any("上限" in s for s in body["skipped"])


def test_backup_import_learning_log_minutes_clamped(client, auth_headers):
    _mk_admin()
    payload = {
        "format": "deep-tutor-backup",
        "version": 1,
        "sessions": [
            {
                "title": "s",
                "materials": [],
                "plans": [
                    {
                        "content": {"intent": {"topic": "t"}},
                        "learning_logs": [
                            {"date": "2026-07-01", "minutes": -5},
                            {"date": "2026-07-02", "minutes": 99999},
                        ],
                    }
                ],
            }
        ],
    }
    r = _import(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    assert r.json()["learning_logs"] == 2
    from app.db import models

    db = SessionLocal()
    try:
        minutes = sorted(lg.minutes for lg in db.query(models.LearningLog).all())
        assert minutes == [1, 1440]  # 钳制到 API 的 1-1440
    finally:
        db.close()


def test_backup_import_notes_quota(client, auth_headers):
    from app.db import models

    uid = _mk_admin()
    db = SessionLocal()
    try:
        for i in range(495):
            db.add(models.Note(user_id=uid, title=f"已有{i}", content="c"))
        db.commit()
    finally:
        db.close()
    payload = {
        "format": "deep-tutor-backup",
        "version": 1,
        "sessions": [],
        "notes": [{"title": f"导入{i}", "content": "c"} for i in range(10)],
    }
    r = _import(client, auth_headers, payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["notes"] == 5  # 495 已有 + 500 上限 -> 仅 5 条入库
    assert any("上限" in s for s in body["skipped"])

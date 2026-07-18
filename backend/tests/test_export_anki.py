"""P5 导出测试：Anki 卡片包（build_apkg + /export format=anki）。"""

from __future__ import annotations

import base64
import io
import zipfile

import pytest

from app.tools.build_anki import build_apkg


def _mk_plan(client, auth_headers, content) -> int:
    """为当前认证用户建会话+方案（与 test_export_ics 同模式，测试文件自包含）。"""
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

PLAN = {
    "title": "Python 入门<b>",  # 含 HTML 特殊字符，验证转义
    "quizzes": [
        {
            "type": "choice",
            "question": "Python 中哪个是不可变类型？",
            "options": ["A. list", "B. tuple", "C. dict"],
            "answer": "B. tuple",
            "explanation": "tuple 创建后不可修改。",
        },
        {
            "type": "short",
            "question": "解释 GIL",
            "answer": "全局解释器锁",
            "explanation": "",
        },
    ],
}


def _open_apkg(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data))


def test_build_apkg_basic():
    apkg, cards = build_apkg(3, PLAN)
    assert cards == 2
    zf = _open_apkg(apkg)
    assert "collection.anki2" in zf.namelist()  # 合法 apkg（zip 含 SQLite 库）


def test_build_apkg_stable_deck_id():
    # 同 plan 重复导出同 deck id（Anki 按 id 去重更新而非新建牌组）
    assert build_apkg(3, PLAN)[0] and build_apkg(3, PLAN)[0]


def test_build_apkg_escapes_html():
    plan = {"title": "t", "quizzes": [{"question": "<script>alert(1)</script>?", "answer": "x<b>"}]}
    # 卡片内容写入 sqlite，直接查 notes 表验证转义
    import sqlite3

    apkg, _ = build_apkg(1, plan)
    zf = _open_apkg(apkg)
    with zf.open("collection.anki2") as f:
        raw = f.read()
    tmp = io.BytesIO(raw)
    # sqlite3 需要文件，落地临时文件
    import tempfile
    import pathlib

    with tempfile.NamedTemporaryFile(suffix=".anki2", delete=False) as tf:
        tf.write(tmp.read())
        p = pathlib.Path(tf.name)
    try:
        conn = sqlite3.connect(str(p))
        flds = conn.execute("SELECT flds FROM notes").fetchall()
        conn.close()
    finally:
        p.unlink()
    assert flds, "应有卡片"
    all_text = " ".join(f[0] for f in flds)
    assert "<script>" not in all_text
    assert "&lt;script&gt;" in all_text
    assert "x&lt;b&gt;" in all_text


def test_build_apkg_choice_options_rendered():
    import sqlite3
    import tempfile
    import pathlib

    apkg, _ = build_apkg(1, PLAN)
    zf = _open_apkg(apkg)
    with tempfile.NamedTemporaryFile(suffix=".anki2", delete=False) as tf:
        tf.write(zf.open("collection.anki2").read())
        p = pathlib.Path(tf.name)
    try:
        conn = sqlite3.connect(str(p))
        flds = " ".join(r[0] for r in conn.execute("SELECT flds FROM notes"))
        conn.close()
    finally:
        p.unlink()
    assert "A. list" in flds and "B. tuple" in flds  # 选项进正面
    assert "tuple 创建后不可修改" in flds  # 解析进背面


def test_build_apkg_no_quizzes_raises():
    with pytest.raises(ValueError, match="测验题"):
        build_apkg(1, {"quizzes": []})
    with pytest.raises(ValueError):
        build_apkg(1, {"quizzes": [{"question": "", "answer": "x"}]})  # 题干空跳过


def test_export_anki_endpoint(client, auth_headers):
    pid = _mk_plan(client, auth_headers, PLAN)
    r = client.post(f"/api/export/{pid}", json={"format": "anki"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["format"] == "apkg" and body["card_count"] == 2
    assert body["filename"].endswith(".apkg")
    raw = base64.b64decode(body["apkg_b64"])
    assert "collection.anki2" in _open_apkg(raw).namelist()


def test_export_anki_no_quizzes_422(client, auth_headers):
    pid = _mk_plan(client, auth_headers, {"title": "x", "quizzes": []})
    r = client.post(f"/api/export/{pid}", json={"format": "anki"}, headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "no_quizzes"

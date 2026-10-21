"""iter 397-B5：契约一致性回归（E3 / K9 / K12 / K14 / K16）。

- **E3**：`DELETE /api/users/{id}` 此前只取消在途 chat run、**不等收敛**（等的是
  方案生成，chat run 在 `_RUN_STREAMS` 按 run_id，根本不在等待范围）：agent 循环
  随后写 `api_usage` -> user_id FK 违约被吞 -> 本轮 LLM 费用无法入账（日预算
  系统性少计）；`messages`/`run_steps` 插入与删除事务交错 -> 删除事务提交失败 ->
  **HTTP 500 且用户根本没被删掉**。
- **K9**：解析管线的错误码（degraded / parse_failed / dependency_missing）此前
  **未登记**进 `CODE_STATUS` -> 一律落默认 400。此处**显式**登记为 400（HTTP 语义
  不变；语义升级属契约变更，须先改 API.md 与前端分支，另行裁决）。
- **K12**：两条知识库入库路径的文本口径不同 -> 同一段文本经 API 提交与经自动
  沉淀入库得到**不同的 content_hash**，产生两篇文档（重复占配额/磁盘）；且
  `create_text_document` 的 docstring 承诺「500k 截断护栏」而实现根本没有。
- **K14**：`get_document_parsed` / 导出打包 / generate 读解析文本 / 单元内容读取
  缺 containment 校验（`get_document_raw` 有 R20 防线）-> DB 路径被篡改即退化为
  任意文件读。
- **K16**：解析失败守卫写成 `status == "error" and out.errors` -> status 为 error
  而 errors 为空时**继续往下走**，用**空文本覆盖既有解析文本**并把文档标成
  parsed（与「解析失败行保留状态不变」相反）。
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# E3：删用户必须等在途 chat run 收敛
# ---------------------------------------------------------------------------


def _mk_user(username: str, password: str = "Passw0rd!", is_admin: bool = False) -> int:
    from app.core.security import hash_password
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(
            name=username,
            username=username,
            password_hash=hash_password(password),
            is_admin=is_admin,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _login(client, username: str, password: str = "Passw0rd!") -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _admin_headers(client) -> dict:
    """管理员会话（删用户端点仅管理员可调）。"""
    _mk_user("e3root", is_admin=True)
    return _login(client, "e3root")


def test_delete_user_waits_for_running_chat_runs(client, auth_headers, monkeypatch):
    """删用户前必须等在途 chat run 收敛（否则费用黑洞 + 删不掉）。"""
    from app.api import memory as mem_mod
    from app.db.base import SessionLocal
    from app.db.models import Run, RunStatus, Thread

    admin_id = _mk_user("e3target")
    hdr = _admin_headers(client)
    db = SessionLocal()
    try:
        t = Thread(user_id=admin_id, title="e3-thread")
        db.add(t)
        db.flush()
        run = Run(thread_id=t.id, user_id=admin_id, kind="chat", status=RunStatus.running)
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id
    finally:
        db.close()

    seen: list[list[int]] = []
    real_wait = mem_mod._wait_runs_settled

    def _spy(run_ids, timeout=10.0):
        seen.append(list(run_ids))
        return real_wait(run_ids, timeout)

    monkeypatch.setattr(mem_mod, "_wait_runs_settled", _spy)
    r = client.delete(f"/api/users/{admin_id}", headers=hdr)
    assert r.status_code == 200, r.text
    assert any(run_id in ids for ids in seen for ids in [ids]), f"未等待在途 run：{seen}"


# ---------------------------------------------------------------------------
# K9：解析错误码登记
# ---------------------------------------------------------------------------


def test_parse_error_codes_registered():
    """解析管线的错误码必须在统一错误码表里**显式**登记。

    登记本身不改变 HTTP 语义——note/11 定「v1 契约冻结，只改勘误不改语义」：
    这三个码此前未登记、落默认 400；语义升级（dependency_missing->503、
    parse_failed/degraded->422）属契约变更，须先改 API.md 与前端分支，另行裁决
    （tests/test_api.py::test_upload_image_png_passes_validation 钉住 400）。
    """
    from app.api.errors import CODE_STATUS

    for code in ("degraded", "parse_failed", "dependency_missing"):
        assert code in CODE_STATUS, f"{code} 未登记"
        assert CODE_STATUS[code] == 400, f"{code} 状态码被改动（违反契约冻结）"


# ---------------------------------------------------------------------------
# K12：两条入库路径的文本口径一致（同内容同哈希）
# ---------------------------------------------------------------------------


def test_same_text_via_api_and_auto_sink_dedups(client, auth_headers):
    """同一段文本经 API 提交与经自动沉淀入库 -> 同一篇文档（同 content_hash）。"""
    from app.api.kb import add_text_document_sync
    from app.db.base import SessionLocal

    # 先用认证请求物化默认用户（.env token 回退 -> get_default_user 建本地用户）
    me = client.get("/api/auth/me", headers=auth_headers)
    assert me.status_code == 200, me.text
    user_id = me.json()["id"]

    text = "同一段知识库文本：用于验证两条入库路径的哈希口径一致。"
    db = SessionLocal()
    try:
        doc = add_text_document_sync(db, user_id, "自动沉淀", text)
        assert doc is not None
        auto_id = doc.id
    finally:
        db.close()

    r = client.post("/api/kb/documents/text", json={"title": "手动提交", "text": text}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == auto_id, f"两条路径未去重：{body} vs {auto_id}"


def test_text_document_respects_500k_truncation(client, auth_headers):
    """端点 docstring 承诺「经 500k 截断护栏」——实现此前根本没有。"""
    from app.db.base import SessionLocal
    from app.db.models import KbDocument

    big = "x" * 600_000
    r = client.post("/api/kb/documents/text", json={"title": "大文本", "text": big}, headers=auth_headers)
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    db = SessionLocal()
    try:
        doc = db.get(KbDocument, doc_id)
        assert doc.parsed_chars <= 500_000 + 32, doc.parsed_chars
    finally:
        db.close()


# ---------------------------------------------------------------------------
# K14：路径 containment 校验
# ---------------------------------------------------------------------------


def test_parsed_text_path_tampering_is_blocked(client, auth_headers):
    """DB 里的解析文本路径被改到库外 -> 404（不得读出库外文件）。"""
    from app.db.base import SessionLocal
    from app.db.models import KbDocument

    r = client.post(
        "/api/kb/documents/text", json={"title": "越界用例", "text": "正文"}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]

    secret = r"C:\Windows\win.ini"
    db = SessionLocal()
    try:
        doc = db.get(KbDocument, doc_id)
        doc.parsed_text_path = secret
        db.commit()
    finally:
        db.close()

    got = client.get(f"/api/kb/documents/{doc_id}/parsed", headers=auth_headers)
    assert got.status_code == 404, got.text
    assert "win.ini" not in got.text


def test_raw_endpoint_still_guards_containment(client, auth_headers):
    """原件读取的 R20 防线保持有效（回归护栏）。"""
    from app.db.base import SessionLocal
    from app.db.models import KbDocument

    r = client.post(
        "/api/kb/documents/text", json={"title": "原件越界", "text": "正文"}, headers=auth_headers
    )
    doc_id = r.json()["id"]
    db = SessionLocal()
    try:
        doc = db.get(KbDocument, doc_id)
        doc.original_path = r"C:\Windows\win.ini"
        db.commit()
    finally:
        db.close()
    got = client.get(f"/api/kb/documents/{doc_id}/raw", headers=auth_headers)
    assert got.status_code == 404, got.text


def test_unit_content_path_outside_session_dir_is_blocked(client, auth_headers):
    """单元内容路径被改到会话目录外 -> 404（不得读出库外文件）。"""
    from app.db.base import SessionLocal
    from app.db.models import Plan, Session, StudyMaterial, StudyUnit

    db = SessionLocal()
    try:
        me = client.get("/api/auth/me", headers=auth_headers)
        assert me.status_code == 200, me.text
        uid = me.json()["id"]
        sess = Session(user_id=uid, title="k14-session")
        db.add(sess)
        db.flush()
        plan = Plan(session_id=sess.id, content={"intent": {"topic": "x"}}, version=1)
        db.add(plan)
        db.flush()
        unit = StudyUnit(plan_id=plan.id, title="u", order=0)
        db.add(unit)
        db.flush()
        db.add(
            StudyMaterial(
                unit_id=unit.id, content_md_path=r"C:\Windows\win.ini", size=3
            )
        )
        db.commit()
        uid_unit = unit.id
    finally:
        db.close()

    got = client.get(f"/api/materials/{uid_unit}", headers=auth_headers)
    assert got.status_code == 404, got.text
    assert "win.ini" not in got.text


# ---------------------------------------------------------------------------
# K16：解析失败不得覆盖既有解析文本
# ---------------------------------------------------------------------------


def test_parse_failure_with_empty_errors_keeps_state(client, auth_headers, monkeypatch):
    """status=error 且 errors=[] -> 报错且**不覆盖**既有解析文本、不改状态。"""
    from app.api import kb as kb_mod
    from app.db.base import SessionLocal
    from app.db.models import KbDocument
    from app.tools.base import ToolOutput

    r = client.post(
        "/api/kb/documents/text", json={"title": "空错误解析", "text": "既有正文"}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]

    db = SessionLocal()
    try:
        before = db.get(KbDocument, doc_id)
        before_path = before.parsed_text_path
        before_chars = before.parsed_chars
    finally:
        db.close()

    async def _fake_parse(*args, **kwargs):
        # status=error 但 errors 为空——此前守卫放行 -> 空文本覆盖既有解析文本
        return ToolOutput(tool="parse_material", status="error", data={"text": ""}, errors=[])

    monkeypatch.setattr(kb_mod, "parse_material", _fake_parse)
    got = client.post(f"/api/kb/documents/{doc_id}/parse", json={"mode": "fast"}, headers=auth_headers)
    assert got.status_code in (400, 422), got.text

    db = SessionLocal()
    try:
        after = db.get(KbDocument, doc_id)
        assert after.parsed_text_path == before_path, "既有解析文本路径被改写"
        assert after.parsed_chars == before_chars, "既有解析字数被清零"
    finally:
        db.close()


def test_parse_failure_guard_helper_raises_on_error_without_errors():
    """raise_parse_failure：status=error 且 errors=[] -> 仍抛错（不静默放行）。"""
    from app.api.errors import ApiError, raise_parse_failure
    from app.tools.base import ToolOutput

    ok = ToolOutput(tool="t", status="ok", data={"text": "x"})
    raise_parse_failure(ok)  # 不抛

    bad = ToolOutput(tool="t", status="error", data={"text": ""}, errors=[])
    with pytest.raises(ApiError) as ei:
        raise_parse_failure(bad)
    assert ei.value.code == "parse_failed"

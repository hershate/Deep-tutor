"""iter 367 批次五（安全）回归测试。

覆盖：
- zip 伪造头第二层防护：声明值检查可被伪造 file_size 绕过（实验验证），实际流
  有界解压计数须拦截伪造头/超限
- slides content_html 白名单：svg/form 等黑名单外标签、action/xlink:href 等
  href/src 外 URL 属性均被剥除；正常排版标签/属性保留
- require_auth JWT 业务校验：用户已删 / 改密吊销后的旧 JWT 不得通过
- /setup/dismiss 门禁：认证模式下未认证 401、admin 200；开放模式仍可用（懒 admin）
- 限流补齐：sessions 创建 / materials text / parse 超限 429
- export file_url 不回显服务器绝对路径
"""

from __future__ import annotations

import io
import re
import struct
import zipfile


# --- zip 伪造头防护 ---


def _make_forged_zip(actual_bytes: int, declared: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a.bin", b"\x00" * actual_bytes)
    raw = bytearray(buf.getvalue())
    i_local = raw.find(b"PK\x03\x04")
    i_cd = raw.find(b"PK\x01\x02")
    for base, u_off in ((i_local, 22), (i_cd, 24)):
        raw[base + u_off : base + u_off + 4] = struct.pack("<I", declared)
    return bytes(raw)


def test_zip_forged_header_rejected(tmp_path):
    """伪造 file_size（声明 1KB、实际 3MB）：声明值检查通过，实际流校验须拒。"""
    import sys

    sys.path.insert(0, ".")
    from app.tools.parse_material import _check_zip_safety
    from app.tools.parse_material import _ParseError

    p = tmp_path / "forged.docx"
    p.write_bytes(_make_forged_zip(actual_bytes=3 * 1024 * 1024, declared=1000))
    try:
        _check_zip_safety(p)
        raise AssertionError("伪造头 zip 未被拦截")
    except _ParseError as exc:
        assert "伪造" in str(exc) or "不一致" in str(exc)


def test_zip_normal_passes(tmp_path):
    from app.tools.parse_material import _check_zip_safety

    p = tmp_path / "normal.docx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<w/>" * 1000)
        z.writestr("dir/", "")
    _check_zip_safety(p)  # 不抛即通过


def test_zip_declared_oversize_rejected(tmp_path, monkeypatch):
    import app.tools.parse_material as pm

    monkeypatch.setattr(pm, "_ZIP_MAX_UNCOMPRESSED", 1024)
    p = tmp_path / "big.docx"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a.bin", b"\x00" * (100 * 1024))
    from app.tools.parse_material import _ParseError

    try:
        pm._check_zip_safety(p)
        raise AssertionError("声明超限未拦截")
    except _ParseError:
        pass


# --- slides 白名单 ---


def test_slides_whitelist_strips_unknown_tags_and_url_attrs():
    from app.tools.build_plan_slides import _sanitize_content_html as san

    # svg + xlink:href（SMIL/命名空间 XSS 面）
    assert "<svg" not in san('<svg><a xlink:href="javascript:alert(1)">x</a></svg>')
    assert "xlink" not in san('<svg><a xlink:href="javascript:alert(1)">x</a></svg>')
    # 表单钓鱼
    out = san('<form action="http://evil.example"><input type="password"></form>')
    assert "<form" not in out and "<input" not in out
    # href/src 外的 URL 承载属性
    assert "poster" not in san('<video poster="javascript:alert(1)"><source src="x"></video>')
    assert "formaction" not in san('<button formaction="javascript:alert(1)">go</button>')
    # 白名单标签的非白名单属性（style 注入面）
    assert "style" not in san('<div style="position:fixed;top:0">x</div>')


def test_slides_whitelist_keeps_normal_markup():
    from app.tools.build_plan_slides import _sanitize_content_html as san

    src = '<h2>标题</h2><ul class="pts" start="2"><li><strong>要点</strong></li></ul><a href="https://ok.example" title="t">链接</a><img src="i.png" alt="图" width="100">'
    out = san(src)
    assert '<h2>标题</h2>' in out
    assert 'class="pts"' in out and 'start="2"' in out
    assert '<strong>要点</strong>' in out
    assert 'href="https://ok.example"' in out and 'title="t"' in out
    assert 'src="i.png"' in out and 'alt="图"' in out and 'width="100"' in out


# --- require_auth JWT 业务校验 ---


def test_jwt_payload_valid_rejects_revoked_and_deleted():
    from app.api.deps import _jwt_payload_valid
    from app.core.security import create_token, decode_token
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    u = models.User(name="u367c", username="u367c", password_hash="x", is_admin=True)
    db.add(u)
    db.commit()
    db.refresh(u)
    token = create_token(u.id, u.username or "")
    payload = decode_token(token)
    assert payload is not None

    assert _jwt_payload_valid(db, payload) is True

    # 改密吊销：tokens_valid_after 后于签发时间 -> 旧 JWT 拒
    u.tokens_valid_after = float("inf")
    db.commit()
    assert _jwt_payload_valid(db, payload) is False

    # 用户已删 -> 拒
    u.tokens_valid_after = 0.0
    db.delete(u)
    db.commit()
    assert _jwt_payload_valid(db, payload) is False
    db.close()


def test_require_auth_rejects_revoked_jwt_client(client, auth_headers):
    """集成：改密吊销后，旧 JWT 经 require_auth 即 401（此前仅密码学校验）。"""
    from app.core.security import create_token
    from app.db import models
    from app.db.base import SessionLocal

    me = client.get("/api/auth/me", headers=auth_headers).json()
    db = SessionLocal()
    u = db.get(models.User, me["id"])
    u.tokens_valid_after = 1e12  # 远未来：一切既有 JWT 均视为已吊销
    db.commit()
    db.close()
    stale = {"Authorization": f"Bearer {create_token(me['id'], me.get('username') or '')}"}
    # /api/sessions 列表端点（require_auth + get_current_user）
    assert client.get("/api/sessions", headers=stale).status_code == 401
    # 恢复：auth_headers 的开放模式语义不受影响（本测试库无密码 -> 开放放行）
    db = SessionLocal()
    db.get(models.User, me["id"]).tokens_valid_after = 0.0
    db.commit()
    db.close()


# --- /setup/dismiss 门禁 ---


def test_setup_dismiss_authorized_ok(client, auth_headers):
    """认证管理员可 dismiss（回归：加门禁不误伤正常使用）。

    测试环境 ADMIN_PASSWORD 未配置 -> 库中无 is_admin 行（env token 回退到首个
    非管理员用户），直接造 admin 再验。
    """
    from app.db import models
    from app.db.base import SessionLocal

    db = SessionLocal()
    if db.query(models.User).filter_by(is_admin=True).first() is None:
        db.add(models.User(name="admin367", username="admin367", password_hash="x", is_admin=True))
        db.commit()
    db.close()
    r = client.post("/api/setup/dismiss", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_setup_dismiss_unauthorized_401(client):
    """测试环境 AUTH_TOKEN 已配置（非开放模式）：未认证 dismiss -> 401（此前完全公开）。"""
    r = client.post("/api/setup/dismiss")
    assert r.status_code == 401, "加门禁前任何未认证调用方可翻全局 dismiss 状态"


# --- 限流补齐 ---


def test_session_create_rate_limited(client, auth_headers):
    from app.api.sessions import _CREATE_RATE_PER_MIN

    last = None
    for i in range(_CREATE_RATE_PER_MIN + 1):
        last = client.post("/api/sessions", json={"title": f"rl-{i}"}, headers=auth_headers)
        if last.status_code == 429:
            break
    assert last is not None and last.status_code == 429, f"创建限流未触发（最后状态 {last.status_code if last else None}）"


def test_materials_text_rate_limited(client, auth_headers):
    from app.api.materials import _TEXT_RATE_PER_MIN

    sid = client.post("/api/sessions", json={"title": "rl-text"}, headers=auth_headers).json()["session_id"]
    last = None
    for i in range(_TEXT_RATE_PER_MIN + 1):
        last = client.post(
            "/api/materials/text",
            json={"session_id": sid, "text": f"内容 {i}", "title": f"t{i}"},
            headers=auth_headers,
        )
        if last.status_code == 429:
            break
    assert last is not None and last.status_code == 429


def test_parse_rate_limited(client, auth_headers):
    """parse 限流：无需真实文件——直接打满限额后任意 parse 调用 429（先于文件检查）。"""
    from app.api.materials import _PARSE_RATE_PER_MIN

    code = None
    for _ in range(_PARSE_RATE_PER_MIN + 1):
        r = client.post("/api/materials/999999/parse", json={}, headers=auth_headers)
        code = r.status_code
        if code == 429:
            break
    assert code == 429, f"解析限流未触发（状态 {code}；预期先于 404 物料检查触发）"


# --- export file_url ---


def test_export_file_url_blank():
    """unit：file_url 恒 ""（不回显服务器绝对路径）。"""
    from app.agent.context import ExecContext
    from app.tools.export_plan import export_plan
    from app.tools.base import ToolInput
    import asyncio
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        inp = ToolInput(
            tool="export_plan", session_id="s367", args={"plan_id": "p", "format": "md", "plan": {"title": "T"}}
        )
        out = asyncio.run(export_plan(inp, ExecContext(session_id="s367", trace_id="t", data_dir=d)))
        assert out.status == "ok"
        assert out.data["file_url"] == ""
        assert not re.search(r"[A-Za-z]:[\\\\/]", out.data["file_url"])

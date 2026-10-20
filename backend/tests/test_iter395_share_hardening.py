"""iter 395：分享端点缺陷修复回归（S1 标题 / S2 配套资源 / S3 并发轮换 / S4 限流分桶 / S5 列表分页）。

- S1：分享页标题恒为「学习方案」——方案内容顶层无 title/topic（主题在 intent.topic），
  此前读 content.get("title")/content.get("topic") 恒空。改走 _plan_topic(plan)。
- S2：配套资源从不显示——前端渲染的是 stage 级 resources（design_stages schema 无此
  字段，是死代码），真实资源在顶层 content.resources（search_resources 产出）。
- S3：过期链接并发轮换互相覆盖——两方都读到同一条过期行时先写者拿到的 token 立刻 404。
  改 CAS 条件更新（WHERE id=? AND token=<旧 token>），后到者返回库里现存的 token。
- S4：公开读限流按 IP 分桶——经反代时全站共用一个桶（30 次/分钟即全站 429）。
  改双桶：按 token（保护单个链接）+ 按 IP（粗粒度护栏，阈值放宽）。
- S5：/shares 列表无分页、无限流——补 limit 与读限流。
"""

from __future__ import annotations

from datetime import datetime, timedelta


def _mk_plan(client, auth_headers, content) -> int:
    from app.db.base import SessionLocal
    from app.db.models import Plan
    from app.db.models import Session as Sess

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


def _share(client, auth_headers, content) -> tuple[int, str]:
    """建方案 + 分享链接，返回 (plan_id, token)。"""
    pid = _mk_plan(client, auth_headers, content)
    r = client.post(f"/api/plans/{pid}/share", headers=auth_headers)
    assert r.status_code == 201, r.text
    return pid, r.json()["token"]


# --- S1：标题取方案主题 ---

def test_shared_title_is_plan_topic(client, auth_headers):
    """S1：title 必须是方案主题（intent.topic），而非恒定的「学习方案」。"""
    _pid, token = _share(client, auth_headers, {"intent": {"topic": "Python 并发编程"}})
    r = client.get(f"/api/shared/{token}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Python 并发编程", body["title"]
    assert body["title"] != "学习方案"


def test_shared_title_falls_back_without_topic(client, auth_headers):
    """S1 边界：无主题信息时仍回落到「学习方案」（响应字段名 title 不变，契约不回退）。"""
    _pid, token = _share(client, auth_headers, {"stages": []})
    body = client.get(f"/api/shared/{token}").json()
    assert body["title"] == "学习方案"


# --- S2：顶层配套资源 ---

def test_shared_content_carries_top_level_resources(client, auth_headers):
    """S2：顶层 content.resources 随分享响应下发（前端改渲染它）——含 title/url/type/verified，
    且非 http(s) 的 url 已被后端裁剪（前端只把 http(s) 渲染成可点击链接）。"""
    content = {
        "intent": {"topic": "带资源的方案"},
        "stages": [{"index": 0, "name": "基础"}],
        "resources": [
            {"title": "官方文档", "url": "https://docs.example/x", "type": "doc", "verified": "ok"},
            {"title": "恶意链接", "url": "javascript:alert(1)", "type": "doc", "verified": "uncertain"},
        ],
    }
    _pid, token = _share(client, auth_headers, content)
    body = client.get(f"/api/shared/{token}").json()
    res = body["content"]["resources"]
    assert [r["title"] for r in res] == ["官方文档", "恶意链接"]
    assert res[0]["url"] == "https://docs.example/x"
    assert res[0]["verified"] == "ok"
    assert res[1]["url"] == "", "非 http(s) 的 url 须被后端裁剪（分享页不渲染为链接）"


# --- S3：并发轮换 ---

def test_concurrent_rotation_returns_live_token(client, auth_headers):
    """S3：两个调用方都读到同一条过期行时，CAS 保证库里只有一个 token——后到者必须拿到
    库里现存的那个（此前无条件覆盖，先到者拿到的链接立刻 404）。

    以两个独立 DB 会话复现并发交错：s2 在 s1 写入前已读到过期行（快照陈旧）。
    """
    from app.api.share import _rotate_expired_link
    from app.db.base import SessionLocal
    from app.db.models import ShareLink

    _pid, token = _share(client, auth_headers, {"intent": {"topic": "并发"}})
    db = SessionLocal()
    try:
        db.query(ShareLink).filter_by(token=token).update(
            {"created_at": datetime.utcnow() - timedelta(days=91)}
        )
        db.commit()
    finally:
        db.close()

    s1, s2 = SessionLocal(), SessionLocal()
    try:
        link1 = s1.query(ShareLink).filter_by(token=token).first()
        link2 = s2.query(ShareLink).filter_by(token=token).first()
        assert link1 is not None and link2 is not None
        rot1 = _rotate_expired_link(s1, link1)
        rot2 = _rotate_expired_link(s2, link2)
        assert rot1 is not None and rot2 is not None
        tok1, tok2 = rot1.token, rot2.token
    finally:
        s1.close()
        s2.close()

    assert tok1 == tok2, "并发轮换后两方须拿到同一个（库里现存的）token"
    assert tok1 != token
    # 两方拿到的链接都能公开打开
    assert client.get(f"/api/shared/{tok1}").status_code == 200
    assert client.get(f"/api/shared/{tok2}").status_code == 200
    # 旧 token 保持失效
    assert client.get(f"/api/shared/{token}").status_code == 404


# --- S4：公开读限流分桶 ---

def test_shared_read_rate_limit_is_per_link(client, auth_headers):
    """S4：按 token 分桶——刷爆一个链接不得牵连其它链接（此前按 IP 分桶，反代后全站共桶）。"""
    from app.api.share import _SHARED_READ_LINK_RATE_PER_MIN

    _pid1, tok1 = _share(client, auth_headers, {"intent": {"topic": "甲"}})
    _pid2, tok2 = _share(client, auth_headers, {"intent": {"topic": "乙"}})
    for _ in range(_SHARED_READ_LINK_RATE_PER_MIN):
        assert client.get(f"/api/shared/{tok1}").status_code == 200
    # 同一链接超限 -> 429 rate_limited
    r = client.get(f"/api/shared/{tok1}")
    assert r.status_code == 429, r.text
    assert r.json()["error"]["code"] == "rate_limited"
    # 另一个链接不受牵连（不同 token 不同桶）
    assert client.get(f"/api/shared/{tok2}").status_code == 200


# --- S5：/shares 列表分页与限流 ---

def _bulk_links(client, auth_headers, n: int) -> None:
    """直接落库 n 条分享链接（绕过 API 的写限流，构造大列表）。"""
    from app.db.base import SessionLocal
    from app.db.models import Plan
    from app.db.models import Session as Sess
    from app.db.models import ShareLink

    me = client.get("/api/auth/me", headers=auth_headers).json()
    db = SessionLocal()
    try:
        for i in range(n):
            s = Sess(user_id=me["id"])
            db.add(s)
            db.flush()
            p = Plan(session_id=s.id, content={"intent": {"topic": f"批量{i}"}})
            db.add(p)
            db.flush()
            db.add(ShareLink(plan_id=p.id, token=f"bulk_tok_{i}"))
        db.commit()
    finally:
        db.close()


def test_list_shares_respects_limit(client, auth_headers):
    """S5：/shares 受 limit 约束（默认 100，越界 422），响应形状仍是 {"shares": [...]}。"""
    _bulk_links(client, auth_headers, 120)
    r = client.get("/api/shares", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"shares"}, body.keys()
    assert len(body["shares"]) == 100, "默认 limit=100"
    # 显式 limit 生效
    r = client.get("/api/shares?limit=3", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()["shares"]) == 3
    # 越界参数 -> 422
    assert client.get("/api/shares?limit=0", headers=auth_headers).status_code == 422
    assert client.get("/api/shares?limit=501", headers=auth_headers).status_code == 422


def test_list_shares_rate_limited(client, auth_headers):
    """S5：/shares 补读限流——超限 429（此前无限流，可被反复触发全表扫描）。"""
    from app.api.share import _SHARES_LIST_RATE_PER_MIN

    for _ in range(_SHARES_LIST_RATE_PER_MIN):
        assert client.get("/api/shares", headers=auth_headers).status_code == 200
    r = client.get("/api/shares", headers=auth_headers)
    assert r.status_code == 429, r.text
    assert r.json()["error"]["code"] == "rate_limited"

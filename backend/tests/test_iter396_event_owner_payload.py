"""iter 396 回归：核心事件载荷直归属 user_id（plan_created / materials_completed）。

背景（iter 396 发现）：

1. **每订阅者一次查库**——`plan_created` / `materials_completed` 此前仅带 `session_id`，
   SSE 端须经 `resolve_owner` 查库解析 `session.user_id`（iter 381 起直查，无缓存）。
   成本 = 每事件 × 每订阅者 × 一次主键读 + 一个连接池槽位。
2. **会话已删则事件丢失**——解析依赖 `sessions` 行存活；行已删（或 rowid 被复用）时
   解析不出归属 -> 事件按 safe-by-default **丢弃**，正主收不到自己的「方案/材料已就绪」
   通知（前端 toast 与实时刷新静默失效）。

修复：两事件直传 `user_id`（后端插件 API v1.22.0），归属判定**零查库**、不依赖会话行
存活；插件自定义事件仍可仅带 `session_id`（回落路径保持）。

覆盖：载荷键存在性与取值 / 零查库 / 会话已删仍转发 / 回落路径 / API.md 漂移守卫。
"""

from __future__ import annotations

from pathlib import Path


def _first_user_id() -> int:
    """AUTH_TOKEN 回退映射到首个管理员（无则首个用户）——取该用户的 id。"""
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = db.query(User).order_by(User.id).first()
        assert u is not None, "测试库应至少有一个用户"
        return u.id
    finally:
        db.close()


# --- 载荷直归属 ---


def test_plan_created_payload_carries_user_id(client, auth_headers):
    """方案落库 -> plan_created 载荷带 user_id（归属零查库的前提）。"""
    from test_api_plans_materials import _make_plan

    from app.plugins import bus

    captured: list[dict] = []
    bus.on("plan_created", captured.append)

    _make_plan(client, auth_headers)

    assert len(captured) == 1
    assert captured[0]["user_id"] == _first_user_id()


def test_materials_completed_sse_payload_carries_user_id(client, auth_headers, monkeypatch):
    """材料 SSE 流完成 -> materials_completed 载荷带 user_id。"""
    from test_api_plans_materials import _make_plan
    from test_api_plans_materials_sse import _UnitProvider, _unit_resp

    from app.api import plans as plans_mod
    from app.plugins import bus

    async def _no_search(query, ctx, limit=3):  # 防真实联网（同 iter395 套件）
        return []

    monkeypatch.setattr(plans_mod, "search_unit_resources", _no_search)

    captured: list[dict] = []
    bus.on("materials_completed", captured.append)

    plan_id = _make_plan(client, auth_headers)
    provider = _UnitProvider([_unit_resp("### 一"), _unit_resp("### 二")])
    monkeypatch.setattr(plans_mod, "get_provider", lambda *a, **k: provider)

    headers = {**auth_headers, "Accept": "text/event-stream"}
    r = client.post(f"/api/plans/{plan_id}/materials", json={"depth": "full"}, headers=headers)
    assert r.status_code == 200, r.text

    assert len(captured) == 1
    assert captured[0]["user_id"] == _first_user_id()


def test_materials_completed_sync_payload_carries_user_id(client, auth_headers, monkeypatch):
    """材料同步 JSON 路径（无 SSE Accept 头）-> materials_completed 载荷同样带 user_id。"""
    from test_api_plans_materials import _make_plan

    from app.api import plans as plans_mod
    from app.plugins import bus

    captured: list[dict] = []
    bus.on("materials_completed", captured.append)

    plan_id = _make_plan(client, auth_headers)

    def _raise(*a, **k):
        raise RuntimeError("ANTHROPIC_API_KEY 未配置")

    monkeypatch.setattr(plans_mod, "get_provider", _raise)
    r = client.post(f"/api/plans/{plan_id}/materials", json={}, headers=auth_headers)
    assert r.status_code == 200, r.text

    assert len(captured) == 1
    assert captured[0]["user_id"] == _first_user_id()


def test_owner_payload_rejects_non_positive_int():
    """非法归属键不写入载荷：None / bool / 0 / 负数 -> 不带 user_id。

    bool 尤其危险：`resolve_owner` 用 `type(uid) is int` 判别（iter 395 防线），
    若把 `True` 写进载荷会被判给 user 1（白拿他人事件）。
    """
    from app.api.plans import _owner_payload

    assert "user_id" not in _owner_payload({}, None)
    assert "user_id" not in _owner_payload({}, True)
    assert "user_id" not in _owner_payload({}, 0)
    assert "user_id" not in _owner_payload({}, -1)
    assert "user_id" not in _owner_payload({}, "7")  # 字符串不认
    assert _owner_payload({}, 7)["user_id"] == 7


# --- 归属判定：零查库 / 会话已删仍转发 / 回落路径 ---


class _BoomDB:
    """任何查询即抛——用于断言「归属判定不查库」。"""

    def get(self, *args, **kwargs):  # pragma: no cover - 命中即失败
        raise AssertionError("resolve_owner 不应查库（user_id 直归属）")


def test_resolve_owner_with_user_id_never_touches_db():
    """payload 带 user_id -> 归属判定零查库（不打开连接、不读会话行）。"""
    from app.api.events import resolve_owner

    assert resolve_owner({"user_id": 7, "session_id": 999999}, db=_BoomDB()) == 7


def test_event_forwarded_even_after_session_deleted(client, auth_headers):
    """会话行已删 -> 事件仍转发给正主（此前按「不可归属」丢弃，正主收不到）。

    这是 iter 396 修复的核心用户可见收益：生成完成通知不再依赖会话行存活。
    """
    from app.api.events import format_frame

    uid = 4242  # format_frame 只做归属比对，不校验用户行存在
    payload = {"plan_id": 1, "session_id": 999999, "version": 1, "topic": "React", "user_id": uid}

    frame = format_frame("plan_created", payload, user_id=uid)
    assert frame is not None, "带 user_id 的事件不应因会话行缺失被丢弃"
    assert f'"user_id": {uid}' in frame
    # 他人仍收不到（隔离不因补键而放松）
    assert format_frame("plan_created", payload, user_id=uid + 999) is None


def test_session_id_only_payload_still_resolves():
    """回落路径不变：仅带 session_id 的事件（插件自定义事件）仍经 DB 解析归属。"""
    from app.api.events import resolve_owner
    from app.db import models
    from app.db.models import User

    from app.db.base import SessionLocal

    db = SessionLocal()
    try:
        u = User(name="o2", username="o2", password_hash="", is_admin=False)
        db.add(u)
        db.flush()
        sess = models.Session(user_id=u.id, title="t")
        db.add(sess)
        db.flush()
        uid, sid = u.id, sess.id
        db.commit()
    finally:
        db.close()

    assert resolve_owner({"session_id": sid}) == uid


# --- 文档漂移守卫 ---


def test_api_doc_core_events_document_user_id():
    """API.md §8.5 核心事件清单须声明 plan_created/materials_completed 的 user_id 键
    （载荷加键后忘同步文档 -> 红）。"""
    root = Path(__file__).resolve().parents[2]
    doc = (root / "API.md").read_text(encoding="utf-8")
    i = doc.index("### 8.5")
    section = doc[i : doc.index("### 8.6", i)]
    for event in ("plan_created", "materials_completed"):
        row = next(
            (ln for ln in section.splitlines() if ln.startswith(f"| `{event}`")),
            None,
        )
        assert row, f"API.md §8.5 缺 {event} 行"
        assert "user_id" in row, f"API.md §8.5 {event} 行未声明 user_id 键"

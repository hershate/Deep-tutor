"""iter 395 回归：并发写面 500、事件流连接数上限、错题族读限流。

覆盖本轮落地的三处修复：

1. **用户偏好并发首写 -> 500**（``PUT /api/settings/prefs``、``PUT /api/settings/pricing``）：
   原实现「先查后插」——并发首写同一格时双方都读到「无行」-> 都 INSERT -> 后者撞
   ``uq_user_prefs_user_key`` -> ``IntegrityError`` 未捕获 -> 500。
   实测（探针 dt_probe_prefs.py）：8 并发 prefs -> 4 个 500；8 并发 pricing -> 7 个 500。
   修复：SQLite 原生 upsert（``INSERT ... ON CONFLICT DO UPDATE``）单条 DML，冲突交数据库解决。
2. **事件流连接数无上限**（``GET /api/events/stream``）：每条连接持 100 槽队列 + 一个
   bus sink，且每次 emit 遍历全部 sink -> 单客户端可开满连接耗内存/放大 CPU。
   修复：单用户 16 条 / 全局 256 条上限，超限 429。
3. **错题族读端点无限流**（``GET /api/quizzes/wrong-overview``、``/wrong-queue``）：
   每次都对本用户全部作答历史跑窗口函数 -> 单客户端可连发打满 DB CPU。修复：30/min。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest


def _concurrent(client, fn, n: int = 8) -> list[int]:
    with ThreadPoolExecutor(max_workers=n) as ex:
        return [r.status_code for r in ex.map(lambda _: fn(), range(n))]


# --- 1. 用户偏好并发首写不再 500 ---------------------------------------------


def test_concurrent_prefs_put_no_500(client, auth_headers):
    """并发首写同一偏好格：此前撞唯一约束 -> 500（实测 8 并发 4 个 500）。"""
    from app.db import models
    from app.db.base import SessionLocal

    def _put():
        return client.put(
            "/api/settings/prefs", json={"daily_budget_usd": 5.0}, headers=auth_headers
        )

    codes = _concurrent(client, _put)
    assert not [c for c in codes if c >= 500], f"并发写偏好出现 5xx：{codes}"
    assert all(c == 200 for c in codes), codes
    db = SessionLocal()
    try:
        rows = db.query(models.UserPref).filter_by(key="daily_budget_usd").all()
        assert len(rows) == 1, f"偏好行应唯一，实际 {len(rows)} 行"
        assert rows[0].value == "5.0"
    finally:
        db.close()


def test_concurrent_pricing_put_no_500(client, auth_headers):
    """计费表并发首写同口径（此前 8 并发 7 个 500）。"""
    from app.db import models
    from app.db.base import SessionLocal
    from app.agent.cost import MODEL_PRICING_PREF_KEY

    def _put():
        return client.put(
            "/api/settings/pricing",
            json={"pricing": {"m1": {"input_per_mtok": 1.0}}},
            headers=auth_headers,
        )

    codes = _concurrent(client, _put)
    assert not [c for c in codes if c >= 500], f"并发写计费表出现 5xx：{codes}"
    assert all(c == 200 for c in codes), codes
    db = SessionLocal()
    try:
        rows = db.query(models.UserPref).filter_by(key=MODEL_PRICING_PREF_KEY).all()
        assert len(rows) == 1, f"计费表行应唯一，实际 {len(rows)} 行"
    finally:
        db.close()


def test_prefs_put_is_rate_limited(client, auth_headers):
    """偏好写加限流（与计费表同口径 30/min）——防并发写风暴打满写锁。"""
    codes = [
        client.put(
            "/api/settings/prefs", json={"daily_budget_usd": 1.0}, headers=auth_headers
        ).status_code
        for _ in range(31)
    ]
    assert codes[-1] == 429, codes
    assert codes[:30] == [200] * 30, codes


# --- 2. 事件流连接数上限 -----------------------------------------------------


def test_event_stream_slot_cap_enforced(monkeypatch):
    """槽位计数：超单用户上限抛 429 语义错误；释放后可再占。"""
    from app.api import events as events_mod
    from app.api.errors import ApiError

    monkeypatch.setattr(events_mod, "_MAX_STREAMS_PER_USER", 2)
    monkeypatch.setattr(events_mod, "_MAX_STREAMS_TOTAL", 10)
    events_mod._streams_by_user.clear()
    events_mod._streams_total = 0
    try:
        events_mod._acquire_stream_slot(7)
        events_mod._acquire_stream_slot(7)
        with pytest.raises(ApiError) as exc:
            events_mod._acquire_stream_slot(7)
        assert exc.value.status == 429
        events_mod._release_stream_slot(7)
        events_mod._acquire_stream_slot(7)  # 释放后可再占
        # 另一用户不受本用户占用影响
        events_mod._acquire_stream_slot(8)
    finally:
        events_mod._streams_by_user.clear()
        events_mod._streams_total = 0


def test_event_stream_rejects_over_limit(client, auth_headers, monkeypatch):
    """超限连接直接 429（不订阅事件总线）。"""
    from app.api import events as events_mod
    from app.api.errors import ApiError

    def _boom(user_id: int) -> None:
        raise ApiError("rate_limited", "事件流连接数达上限", status=429)

    monkeypatch.setattr(events_mod, "_acquire_stream_slot", _boom)
    r = client.get("/api/events/stream", headers=auth_headers)
    assert r.status_code == 429, r.text


def test_event_stream_slot_released_on_disconnect(client, auth_headers, monkeypatch):
    """连接断开后槽位必须归还（否则长跑后误触上限）。"""
    from app.api import events as events_mod

    monkeypatch.setattr(events_mod, "_MAX_STREAMS_PER_USER", 1)
    events_mod._streams_by_user.clear()
    events_mod._streams_total = 0
    try:
        events_mod._acquire_stream_slot(1)
        events_mod._release_stream_slot(1)
        # 归还后可再占（不断言网络层，只锁计数语义）
        events_mod._acquire_stream_slot(1)
        assert events_mod._streams_total == 1
    finally:
        events_mod._streams_by_user.clear()
        events_mod._streams_total = 0


# --- 3. 错题族读限流 --------------------------------------------------------


def test_wrong_overview_is_rate_limited(client, auth_headers):
    """错题总览每次扫本用户全部作答历史 -> 限流 30/min。"""
    codes = [client.get("/api/quizzes/wrong-overview", headers=auth_headers).status_code for _ in range(31)]
    assert codes[-1] == 429, codes
    assert codes[:30] == [200] * 30, codes


def test_wrong_queue_is_rate_limited(client, auth_headers):
    """错题复习队列同口径限流。"""
    codes = [client.get("/api/quizzes/wrong-queue", headers=auth_headers).status_code for _ in range(31)]
    assert codes[-1] == 429, codes
    assert codes[:30] == [200] * 30, codes

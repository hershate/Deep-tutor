"""iter 379 R22：全量覆盖型写面收尾——模型计费表乐观并发回归。

背景：PUT /settings/pricing 是整表读改写（设置页载入 -> 编辑 -> 全量 PUT，UserPref
单行 JSON，至多 200 模型 × 4 单价）。双标签后保存者静默吞掉先保存者的条目——与
iter 378（R21）笔记盲覆盖同类。修复：可选 expected_updated_at（GET 返回的基线原样
回传），不匹配（含他窗口已清空删行 -> cur=None）-> 409；不传保持旧行为（兼容）。

其余 PUT/PATCH 写面已核无同类缺口（字段级合并/单字段/管理员面）：
prefs（exclude_unset 键级）、providers/credentials（None=保留的标量合并 + 重名 409）、
kb documents（仅 title 单字段）、threads patch（title/model 标量）、users（admin 面）。

测试用 DB 回拨 updated_at 模拟「他窗口先保存」，避开秒粒度等待（同 iter 378 法）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.agent.cost import MODEL_PRICING_PREF_KEY
from app.core.security import create_token
from app.db import models
from app.db.base import SessionLocal
from app.main import app

client = TestClient(app)

_V1 = {"deepseek-chat": {"input_per_mtok": 2, "output_per_mtok": 8, "cache_read_per_mtok": 0, "cache_write_per_mtok": 0}}
_V2 = {"qwen-max": {"input_per_mtok": 1, "output_per_mtok": 4, "cache_read_per_mtok": 0, "cache_write_per_mtok": 0}}


def _mk_user(name: str) -> tuple[int, dict]:
    db = SessionLocal()
    try:
        u = models.User(name=name, username=name, password_hash="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, {"Authorization": f"Bearer {create_token(u.id, name)}"}
    finally:
        db.close()


def _backdate(uid: int, seconds: float) -> None:
    db = SessionLocal()
    try:
        db.query(models.UserPref).filter_by(user_id=uid, key=MODEL_PRICING_PREF_KEY).update(
            {"updated_at": datetime.utcnow() - timedelta(seconds=seconds)}
        )
        db.commit()
    finally:
        db.close()


def test_pricing_stale_expected_409_not_swallowed():
    uid, h = _mk_user("price_cc_a")
    # 窗口 A 装载（无表 -> 基线 null）；他窗口先保存 v1
    assert client.get("/api/settings/pricing", headers=h).json()["updated_at"] is None
    r = client.put("/api/settings/pricing", json={"pricing": _V1}, headers=h)
    assert r.status_code == 200, r.text
    ts0 = r.json()["updated_at"]
    assert ts0 is not None
    # 窗口 B 装载 ts0 后又保存了 v2（回拨模拟「v2 早于窗口 A 的重存」版本序）
    r = client.put("/api/settings/pricing", json={"pricing": _V2}, headers=h)
    assert r.status_code == 200
    _backdate(uid, 5)
    # 窗口 A 持 ts0 基线全量覆盖 -> 409，v2 条目不被吞
    r = client.put(
        "/api/settings/pricing",
        json={"pricing": _V1, "expected_updated_at": ts0},
        headers=h,
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "conflict"
    table = client.get("/api/settings/pricing", headers=h).json()["table"]
    assert set(table) == set(_V2), table


def test_pricing_fresh_expected_ok_and_compat_absent():
    _, h = _mk_user("price_cc_b")
    ts = client.put("/api/settings/pricing", json={"pricing": _V1}, headers=h).json()["updated_at"]
    # GET 基线与保存回执一致（前后无写）
    assert client.get("/api/settings/pricing", headers=h).json()["updated_at"] == ts
    # 基线一致 -> 放行，回执给新基线
    r = client.put(
        "/api/settings/pricing",
        json={"pricing": _V2, "expected_updated_at": ts},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert set(r.json()["table"]) == set(_V2)
    assert r.json()["updated_at"] is not None
    # 不传 = 旧行为（LWW 兼容：插件/外部调用方）
    r = client.put("/api/settings/pricing", json={"pricing": _V1}, headers=h)
    assert r.status_code == 200 and set(r.json()["table"]) == set(_V1)


def test_pricing_other_window_cleared_conflict():
    uid, h = _mk_user("price_cc_c")
    ts = client.put("/api/settings/pricing", json={"pricing": _V1}, headers=h).json()["updated_at"]
    _backdate(uid, 5)
    # 他窗口清空（pricing 空 -> 删行回退默认），本窗口仍持旧基线 -> 409（cur=None != ts）
    r = client.put("/api/settings/pricing", json={"pricing": {}}, headers=h)
    assert r.status_code == 200 and r.json()["table"] == {}
    r = client.put(
        "/api/settings/pricing",
        json={"pricing": _V1, "expected_updated_at": ts},
        headers=h,
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "conflict"
    # 清空后 GET 基线为 null（窗口 A 重装载走「无表」路径）
    assert client.get("/api/settings/pricing", headers=h).json()["updated_at"] is None

"""模型计费表可配置测试（iter 356）。

iter 356 计费改为「设置页维护的模型计费表」（模型名 -> 4 项费用，¥/1M tokens），
取代旧的族级覆盖（iter 353）与每卡定价（iter 354）。

覆盖：resolve_pricing/estimate_cost 按模型名精确匹配 + 族兜底；GET/PUT /settings/pricing
（存取 / 清洗非法字段负值空模型名 / 清空 / 跨用户隔离）；record_metadata_usage 与
_persist_tool_calls 按用户计费表重算 api_usage.cost（用量页 / 日预算即用户实际单价）。
"""

from __future__ import annotations

import json

from app.core.security import hash_password

BASE = "/api/settings"


def _mk_user(username: str, password: str = "pw123") -> int:
    from app.db.base import SessionLocal
    from app.db.models import User

    db = SessionLocal()
    try:
        u = User(name=username, username=username, password_hash=hash_password(password))
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id
    finally:
        db.close()


def _login(client, username: str, password: str = "pw123") -> dict:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_resolve_pricing_exact_then_family():
    """resolve_pricing：精确模型名优先 -> 族兜底 -> None。"""
    from app.llm.pricing import get_pricing, resolve_pricing

    table = {"deepseek-chat": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}}
    # 1. 精确匹配用户表
    p = resolve_pricing("deepseek-chat", table)
    assert p is not None and p.input_per_mtok == 1.0
    # 2. 表里没有但属 Anthropic 族 -> 族兜底
    assert resolve_pricing("claude-sonnet-5", table) == get_pricing("claude-sonnet-5")
    # 3. 表里没有且非族 -> None
    assert resolve_pricing("gpt-unknown", table) is None
    # 4. 大小写不敏感
    assert resolve_pricing("DeepSeek-Chat", table) is not None


def test_estimate_cost_with_model_pricing():
    """estimate_cost 用 model_pricing 精确匹配重算（deepseek 非族，无表记 0）。"""
    from app.llm.base import LLMUsage
    from app.llm.pricing import estimate_cost

    usage = LLMUsage(input_tokens=1_000_000, output_tokens=0, model="deepseek-chat")
    assert estimate_cost(usage) == 0.0  # 无表 + 非族 -> 记 0（告警）
    assert estimate_cost(usage, model_pricing={"deepseek-chat": {"input_per_mtok": 2.0}}) == 2.0
    # 表里无该模型 + 非族 -> 仍 0
    assert estimate_cost(usage, model_pricing={"other-model": {"input_per_mtok": 99.0}}) == 0.0


def test_get_pricing_returns_defaults(client):
    """GET /pricing 返回内置国产模型默认表 + 空 table（未配时）。"""
    _mk_user("u1")
    h = _login(client, "u1")
    r = client.get(f"{BASE}/pricing", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    # 默认表为国产模型（DeepSeek/Qwen/GLM/Kimi）
    assert "deepseek-chat" in data["defaults"]
    assert "input_per_mtok" in data["defaults"]["deepseek-chat"]
    assert data["table"] == {}


def test_update_and_read_pricing(client):
    """PUT 模型计费表后 GET 读回；重复 PUT 幂等。"""
    _mk_user("u2")
    h = _login(client, "u2")
    body = {"pricing": {"deepseek-chat": {"input_per_mtok": 1.5, "output_per_mtok": 3.0}}}
    r = client.put(f"{BASE}/pricing", json=body, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["table"]["deepseek-chat"]["input_per_mtok"] == 1.5
    assert client.get(f"{BASE}/pricing", headers=h).json()["table"]["deepseek-chat"]["output_per_mtok"] == 3.0
    # 重复 PUT 幂等
    client.put(f"{BASE}/pricing", json=body, headers=h)
    assert client.get(f"{BASE}/pricing", headers=h).json()["table"] == r.json()["table"]


def test_pricing_clean_invalid_fields(client):
    """PUT 空模型名 / 未知字段 / 负值被清洗；缺失字段补 0。"""
    _mk_user("u3")
    h = _login(client, "u3")
    body = {
        "pricing": {
            "   ": {"input_per_mtok": 1.0},  # 空模型名丢弃
            "deepseek-chat": {"input_per_mtok": 1.0, "bad_field": 9.0, "output_per_mtok": -5.0},
        }
    }
    r = client.put(f"{BASE}/pricing", json=body, headers=h)
    assert r.status_code == 200
    tbl = r.json()["table"]
    assert len(tbl) == 1 and "deepseek-chat" in tbl
    entry = tbl["deepseek-chat"]
    assert "bad_field" not in entry  # 未知字段滤除
    assert "output_per_mtok" not in entry or entry["output_per_mtok"] == 0.0  # 负值被滤后补 0
    assert entry["input_per_mtok"] == 1.0
    # 缺失的 cache_read/cache_write 补 0（4 字段齐全）
    assert entry["cache_read_per_mtok"] == 0.0
    assert entry["cache_write_per_mtok"] == 0.0


def test_pricing_clear(client):
    """PUT 空 pricing 清空计费表回退默认。"""
    _mk_user("u4")
    h = _login(client, "u4")
    client.put(f"{BASE}/pricing", json={"pricing": {"deepseek-chat": {"input_per_mtok": 1.0}}}, headers=h)
    r = client.put(f"{BASE}/pricing", json={"pricing": {}}, headers=h)
    assert r.json()["table"] == {}


def test_pricing_cross_user_isolation(client):
    """用户 A 的计费表不影响 B（UserPref 按用户隔离）。"""
    _mk_user("a")
    ha = _login(client, "a")
    _mk_user("b")
    hb = _login(client, "b")
    client.put(f"{BASE}/pricing", json={"pricing": {"deepseek-chat": {"input_per_mtok": 1.0}}}, headers=ha)
    assert client.get(f"{BASE}/pricing", headers=hb).json()["table"] == {}
    assert client.get(f"{BASE}/pricing", headers=ha).json()["table"]["deepseek-chat"]["input_per_mtok"] == 1.0


def test_record_metadata_usage_uses_model_pricing():
    """iter 356：用户计费表配了模型价 -> record_metadata_usage 写的 api_usage.cost 按表单价。"""
    from app.agent.cost import MODEL_PRICING_PREF_KEY, record_metadata_usage
    from app.db.base import SessionLocal
    from app.db.models import ApiUsage, UserPref
    from app.tools.base import ToolMetadata

    db = SessionLocal()
    try:
        uid = _mk_user("ru")
        db.add(UserPref(user_id=uid, key=MODEL_PRICING_PREF_KEY,
               value=json.dumps({"claude-sonnet-5": {"input_per_mtok": 2.0}})))
        db.commit()
        # metadata.cost=99 是占位；record 应按用户表重算为 2.0（1M input × 2/M）
        meta = ToolMetadata(tokens_in=1_000_000, tokens_out=0, cache_read=0, cache_write=0, cost=99.0, model="claude-sonnet-5")
        record_metadata_usage(db, meta, uid)
        row = db.query(ApiUsage).filter_by(user_id=uid).one()
        assert row.cost == 2.0
    finally:
        db.close()


def test_record_metadata_usage_default_when_no_table():
    """无用户计费表时 record 用 metadata.cost（向后兼容）。"""
    from app.agent.cost import record_metadata_usage
    from app.db.base import SessionLocal
    from app.db.models import ApiUsage
    from app.tools.base import ToolMetadata

    db = SessionLocal()
    try:
        uid = _mk_user("ru2")
        # model 为非族未知模型 -> resolve 返回 None -> 用 metadata.cost
        meta = ToolMetadata(tokens_in=1_000_000, tokens_out=0, cost=7.5, model="some-unknown-model")
        record_metadata_usage(db, meta, uid)
        assert db.query(ApiUsage).filter_by(user_id=uid).one().cost == 7.5
    finally:
        db.close()


def test_persist_tool_calls_uses_model_pricing():
    """iter 356：生成管线落账（_persist_tool_calls）按用户计费表计 api_usage.cost。"""
    from app.api.plans import _persist_tool_calls
    from app.agent.cost import MODEL_PRICING_PREF_KEY
    from app.db.base import SessionLocal
    from app.db.models import ApiUsage, Session, User, UserPref
    from app.tools.base import ToolMetadata, ToolOutput

    db = SessionLocal()
    try:
        u = User(name="pu", username="pu", password_hash=hash_password("pw123"))
        db.add(u)
        db.flush()
        db.add(UserPref(user_id=u.id, key=MODEL_PRICING_PREF_KEY,
               value=json.dumps({"claude-sonnet-5": {"input_per_mtok": 2.0}})))
        sess = Session(user_id=u.id, title="t")
        db.add(sess)
        db.commit()
        out = ToolOutput(
            tool="understand_intent",
            data={},
            metadata=ToolMetadata(
                tokens_in=1_000_000, tokens_out=0, cache_read=0, cache_write=0, cost=99.0, model="claude-sonnet-5"
            ),
        )
        _persist_tool_calls(db, sess.id, "trace", executed=[out])
        assert db.query(ApiUsage).filter_by(user_id=u.id).one().cost == 2.0  # 用户表重算
    finally:
        db.close()

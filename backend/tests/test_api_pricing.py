"""价目表可配置测试（iter 353 问题2）。

覆盖：estimate_cost/get_pricing 用户级 overrides 生效；GET/PUT /settings/pricing
（存取 / 清洗非法族字段负值 / 清除 / 跨用户隔离）；record_metadata_usage 与
_persist_tool_calls 按用户覆盖重算 api_usage.cost（用量页 / 日预算即用户实际单价）。
"""

from __future__ import annotations

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


def test_estimate_cost_with_overrides():
    """estimate_cost 用 overrides 重算（默认 sonnet input $3/M → 覆盖 $2/M 后 cost 降低）。"""
    from app.llm.base import LLMUsage
    from app.llm.pricing import estimate_cost

    usage = LLMUsage(input_tokens=1_000_000, output_tokens=0, model="claude-sonnet-5")
    assert estimate_cost(usage) == 3.0  # 1M input × $3/M（默认）
    assert estimate_cost(usage, overrides={"sonnet": {"input_per_mtok": 2.0}}) == 2.0
    # 未知族覆盖不影响（仍用默认）
    assert estimate_cost(usage, overrides={"unknown": {"input_per_mtok": 99.0}}) == 3.0


def test_get_pricing_returns_defaults(client):
    """GET /pricing 返回默认价目（三族）+ 空 overrides（未配时）。"""
    _mk_user("u1")
    h = _login(client, "u1")
    r = client.get(f"{BASE}/pricing", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data["defaults"]) == {"opus", "sonnet", "haiku"}
    for fam in ("opus", "sonnet", "haiku"):
        assert "input_per_mtok" in data["defaults"][fam]
    assert data["overrides"] == {}


def test_update_and_read_pricing(client):
    """PUT 覆盖后 GET 读回；重复 PUT 幂等。"""
    _mk_user("u2")
    h = _login(client, "u2")
    body = {"pricing": {"sonnet": {"input_per_mtok": 2.0, "output_per_mtok": 10.0}}}
    r = client.put(f"{BASE}/pricing", json=body, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["overrides"]["sonnet"]["input_per_mtok"] == 2.0
    assert client.get(f"{BASE}/pricing", headers=h).json()["overrides"]["sonnet"]["output_per_mtok"] == 10.0
    # 重复 PUT 幂等
    client.put(f"{BASE}/pricing", json=body, headers=h)
    assert client.get(f"{BASE}/pricing", headers=h).json()["overrides"] == body["pricing"]


def test_pricing_clean_invalid_fields(client):
    """PUT 非法族 / 未知字段 / 负值被清洗过滤（仅保留已知族已知非负数值字段）。"""
    _mk_user("u3")
    h = _login(client, "u3")
    body = {
        "pricing": {
            "unknown_family": {"input_per_mtok": 1.0},  # 未知族丢弃
            "sonnet": {"input_per_mtok": 2.0, "bad_field": 9.0, "output_per_mtok": -5.0},
        }
    }
    r = client.put(f"{BASE}/pricing", json=body, headers=h)
    assert r.status_code == 200
    ov = r.json()["overrides"]
    assert "unknown_family" not in ov
    assert "bad_field" not in ov["sonnet"]
    assert "output_per_mtok" not in ov["sonnet"]  # 负值被滤
    assert ov["sonnet"]["input_per_mtok"] == 2.0


def test_pricing_clear(client):
    """PUT 空 pricing 清除覆盖回退默认。"""
    _mk_user("u4")
    h = _login(client, "u4")
    client.put(f"{BASE}/pricing", json={"pricing": {"sonnet": {"input_per_mtok": 2.0}}}, headers=h)
    r = client.put(f"{BASE}/pricing", json={"pricing": {}}, headers=h)
    assert r.json()["overrides"] == {}


def test_pricing_cross_user_isolation(client):
    """用户 A 的覆盖不影响 B（UserPref 按用户隔离）。"""
    _mk_user("a")
    ha = _login(client, "a")
    _mk_user("b")
    hb = _login(client, "b")
    client.put(f"{BASE}/pricing", json={"pricing": {"sonnet": {"input_per_mtok": 2.0}}}, headers=ha)
    assert client.get(f"{BASE}/pricing", headers=hb).json()["overrides"] == {}
    assert client.get(f"{BASE}/pricing", headers=ha).json()["overrides"]["sonnet"]["input_per_mtok"] == 2.0


def test_record_metadata_usage_uses_profile_pricing():
    """iter 354 BUG2：启用供应商档案设了价目 -> record_metadata_usage 写的 api_usage.cost 按档案单价。"""
    from app.agent.cost import record_metadata_usage
    from app.db.base import SessionLocal
    from app.db.models import ApiUsage, LlmProviderProfile
    from app.tools.base import ToolMetadata

    db = SessionLocal()
    try:
        uid = _mk_user("ru")
        db.add(LlmProviderProfile(
            user_id=uid, name="P", api_key="sk-x", model="claude-sonnet-5",
            price_input_per_mtok=2.0, is_active=True,  # 覆盖 sonnet 默认 input $3/M
        ))
        db.commit()
        # metadata.cost=3.0 是默认价算的；record 应按档案价重算为 2.0
        meta = ToolMetadata(
            tokens_in=1_000_000, tokens_out=0, cache_read=0, cache_write=0, cost=3.0, model="claude-sonnet-5"
        )
        record_metadata_usage(db, meta, uid)
        row = db.query(ApiUsage).filter_by(user_id=uid).one()
        assert row.cost == 2.0  # 按档案价（input $2/M）
    finally:
        db.close()


def test_record_metadata_usage_default_when_no_profile():
    """无启用档案时 record 用 metadata.cost（默认价，向后兼容）。"""
    from app.agent.cost import record_metadata_usage
    from app.db.base import SessionLocal
    from app.db.models import ApiUsage
    from app.tools.base import ToolMetadata

    db = SessionLocal()
    try:
        uid = _mk_user("ru2")
        meta = ToolMetadata(tokens_in=1_000_000, tokens_out=0, cost=3.0, model="claude-sonnet-5")
        record_metadata_usage(db, meta, uid)
        assert db.query(ApiUsage).filter_by(user_id=uid).one().cost == 3.0
    finally:
        db.close()


def test_persist_tool_calls_uses_profile_pricing():
    """iter 354 BUG2：生成管线落账（_persist_tool_calls）按启用档案价目计 api_usage.cost。"""
    from app.api.plans import _persist_tool_calls
    from app.db.base import SessionLocal
    from app.db.models import ApiUsage, LlmProviderProfile, Session, User
    from app.tools.base import ToolMetadata, ToolOutput

    db = SessionLocal()
    try:
        u = User(name="pu", username="pu", password_hash=hash_password("pw123"))
        db.add(u)
        db.flush()
        db.add(LlmProviderProfile(
            user_id=u.id, name="P", api_key="sk-x", model="claude-sonnet-5",
            price_input_per_mtok=2.0, is_active=True,
        ))
        sess = Session(user_id=u.id, title="t")
        db.add(sess)
        db.commit()
        out = ToolOutput(
            tool="understand_intent",
            data={},
            metadata=ToolMetadata(
                tokens_in=1_000_000, tokens_out=0, cache_read=0, cache_write=0, cost=3.0, model="claude-sonnet-5"
            ),
        )
        _persist_tool_calls(db, sess.id, "trace", executed=[out])
        assert db.query(ApiUsage).filter_by(user_id=u.id).one().cost == 2.0  # 档案价重算
    finally:
        db.close()

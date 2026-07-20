"""/api/settings 只读配置测试：不泄密，Key 以 bool 返回。"""

from __future__ import annotations


def test_settings_returns_no_secrets(client, auth_headers):
    r = client.get("/api/settings", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    # 非敏感配置字段
    assert "anthropic_model" in data
    assert "budget_per_session_usd" in data
    assert "daily_budget_usd" in data
    assert "max_tool_calls" in data
    assert "max_upload_mb" in data
    assert "search_engine" in data
    # iter 222：实际生效引擎（auto 语义解析结果，供设置页展示）
    assert data["search_engine_effective"] in ("bing_api", "bing_web", "none")
    # Key 仅返回是否已设（bool），不返回明文
    assert isinstance(data["anthropic_api_key_set"], bool)
    assert isinstance(data["bing_api_key_set"], bool)
    assert isinstance(data["auth_token_set"], bool)
    # 不含任何明文密钥字段
    assert "anthropic_api_key" not in data
    assert "bing_api_key" not in data
    assert "auth_token" not in data
    # 测试环境 AUTH_TOKEN=test-token 已设
    assert data["auth_token_set"] is True


def test_settings_requires_auth(client):
    r = client.get("/api/settings")
    assert r.status_code == 401

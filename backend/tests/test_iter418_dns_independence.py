"""iter 418：测试判定不得依赖真实 DNS（供应商档案建档随机 422 的根因）。

现象
----
tests/test_api_providers.py::test_provider_test_dispatches_plugin_kind 在**全量**
pytest 里间歇性红（3 次全量跑红 2 次），单跑该用例或只跑前 39 个文件必绿——
看起来像「插件注册表竞态」，实则失败点在**建档**这一步：HTTP 422。

根因
----
写时 SSRF 校验（app/api/providers.py 的 _validate_base_url ->
app/core/urlguard.py 的 check_outbound_url）会解析 base_url 的域名；解析到
私网/保留地址即拒绝。该用例用 base_url="https://api.openai.com"，而本机 DNS
对被网络拦截的域名**间歇性**返回 sinkhole(0.0.0.0)：
0.0.0.0 落在私网/保留段 -> 判定「域名解析到私网」-> 建档 422。

判定性实验（见 stage.md iter 418）：把该域名的解析结果换成 0.0.0.0，
POST /api/providers 立刻返回
422 {"code":"validation_error","details":[{"loc":["body","base_url"],
"msg":"Value error, base_url 被安全策略拒绝：域名解析到私网 0.0.0.0"}]}。

修法
----
测试环境的 DNS 判定改为确定答案（tests/conftest.py 的 _deterministic_dns 夹具
替换 urlguard 的解析缝 _resolve）：所有域名解析为公网地址 -> 域名判定恒放行。
字面量 IP 的判定不经 DNS，SSRF 拦截未被削弱；「域名解析到私网」这一支由本文件
显式替换解析结果覆盖。

本文件即回归守卫：若有人摘掉那层隔离、或改动判定语义，这里会红。
"""

from __future__ import annotations

import socket

from app.core import urlguard

BASE = "/api/providers"


def _resolve_to(addr: str):
    """把 urlguard 的解析缝换成固定答案（构造某种 DNS 事实）。"""

    def _fake(host: str) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (addr, 0))]

    return _fake


def test_the_suite_does_not_depend_on_live_dns():
    """被网络拦截的域名在本机 DNS 上会间歇性返回 sinkhole -> 判定必须与真实 DNS 无关。"""
    ok, reason = urlguard.check_outbound_url("https://api.openai.com")
    assert ok is True, f"判定依赖了真实 DNS：{reason}"


def test_a_provider_profile_with_a_public_base_url_is_accepted(client, auth_headers):
    """回归：正是这一条在全量跑里随机 422（建档被 SSRF 写时校验误拒）。"""
    r = client.post(
        BASE,
        json={
            "name": "OAI",
            "api_key": "sk-oai-test",
            "base_url": "https://api.openai.com",
            "model": "gpt-4o",
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text


def test_a_domain_that_resolves_to_a_private_address_is_still_refused(monkeypatch):
    """隔离不能削弱 SSRF：域名解析到内网地址仍须拒绝（这一支不能被「全放行」吃掉）。"""
    monkeypatch.setattr(urlguard, "_resolve", _resolve_to("192.168.1.50"))
    urlguard.reset_cache()
    ok, reason = urlguard.check_outbound_url("https://relay.example.com")
    assert ok is False
    assert "私网" in reason


def test_a_domain_resolving_to_the_blackhole_address_says_so(monkeypatch):
    """0.0.0.0 是 DNS 拦截/黑洞，不是内网主机——措辞须指向 DNS 与出口（iter 418 勘误）。"""
    monkeypatch.setattr(urlguard, "_resolve", _resolve_to("0.0.0.0"))
    urlguard.reset_cache()
    ok, reason = urlguard.check_outbound_url("https://api.openai.com")
    assert ok is False
    assert "0.0.0.0" in reason
    assert "私网" not in reason


def test_a_literal_private_base_url_is_still_refused(client, auth_headers):
    """字面量私网地址不经 DNS，判定必须原样生效。"""
    r = client.post(
        BASE,
        json={
            "name": "local",
            "api_key": "k",
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "m",
        },
        headers=auth_headers,
    )
    assert r.status_code == 422, r.text
    assert "base_url" in r.text


def test_the_cloud_metadata_endpoint_is_still_refused(client, auth_headers):
    """云元数据端点照旧拒绝（iter 394 的基线，不得被本轮隔离放松）。"""
    r = client.post(
        BASE,
        json={
            "name": "meta",
            "api_key": "k",
            "base_url": "http://169.254.169.254/latest/meta-data/",
            "model": "m",
        },
        headers=auth_headers,
    )
    assert r.status_code == 422, r.text

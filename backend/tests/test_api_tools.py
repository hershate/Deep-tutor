"""GET /api/tools 工具发现端点测试（后端插件 API v1.8.0，iter 317）。

发现端点（外部客户端/替代前端用，与 api.tools.list 对称的 REST 面）：列出全部已注册工具
元数据 name/llm/requires/description。覆盖：核心工具齐全 + 元数据结构 + 认证 + 插件工具随装载出现。
"""

from __future__ import annotations


def test_list_tools_returns_core(client, auth_headers):
    r = client.get("/api/tools", headers=auth_headers)
    assert r.status_code == 200, r.text
    tools = r.json()["tools"]
    names = [t["name"] for t in tools]
    # 核心工具齐全（可组合工作流的积木）
    for core in [
        "parse_material",
        "understand_intent",
        "build_knowledge_map",
        "design_stages",
        "search_resources",
        "schedule_plan",
        "validate_plan",
        "export_plan",
        "generate_quiz",
    ]:
        assert core in names, f"缺核心工具 {core}"
    # 元数据结构 + 人类描述
    pm = next(t for t in tools if t["name"] == "parse_material")
    assert set(pm) == {"name", "llm", "requires", "description"}
    assert pm["description"]


def test_list_tools_requires_auth(client):
    """非开放模式（conftest AUTH_TOKEN=test-token）-> 无认证 401。"""
    r = client.get("/api/tools")
    assert r.status_code == 401


def test_list_tools_includes_plugin_tools(client, auth_headers):
    """插件注册的工具随装载出现在发现端点（isolate 夹具每测试 reset_plugins，故本测试内注册）。"""
    from app.agent.registry import Tool
    from app.plugins.runtime import add_tool

    add_tool(Tool(name="myplugin.custom_tool", llm=False, func=lambda inp, ctx: {}))
    # 注：跨测试清理由 conftest isolate 夹具的 reset_plugins 负责（autouse，每测试前）
    r = client.get("/api/tools", headers=auth_headers)
    assert r.status_code == 200
    names = [t["name"] for t in r.json()["tools"]]
    assert "myplugin.custom_tool" in names

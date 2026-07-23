# Deep-tutor 后端插件目录

本目录是**后端插件的装载点**（契约与开发规范的权威文档见仓库根 [API.md](../API.md)「后端插件 API」）。

## 装载方式（三种形态）

| 形态 | 例子 | 说明 |
|---|---|---|
| 单文件插件 | `plugins/hello.py` | 最简单，一个文件一个插件 |
| 包插件 | `plugins/hello/plugin.py`（+ 任意辅助模块） | 多文件插件；包内用**相对导入**（`from . import helper`） |
| 临时停用 | `plugins/_hello.py` 或 `plugins/_hello/` | 名字加 `_` 前缀即跳过加载 |

放置文件后**重启后端**生效（当前版本不支持热重载）。加载结果（成功/失败及原因）见
管理员端点 `GET /api/plugins`，或启动日志中的 `插件加载完成：loaded=N failed=M`。

## 最小插件

```python
# plugins/hello.py
from fastapi import APIRouter

PLUGIN_META = {"id": "hello", "name": "Hello", "version": "0.1.0"}

def register(api):
    router = APIRouter()

    @router.get("/ping")
    def ping():
        return {"pong": True}

    api.routes.mount(router)  # 挂载到 /api/plugins/hello/ping（默认带宿主认证）
```

## 官方实用插件（示例，开箱即用）

以下插件默认装载启用，既提供实用功能，也作为各插件 API 的**复合使用示例**供用户与其他开发者参考（配套装载测试见 `backend/tests/test_real_plugin_<name>.py`——真实源码经完整 loader 链路装载 + HTTP 端到端 + 边界验证）：

| 插件 | 用途 | 复合的 API |
|---|---|---|
| [study_export_pack.py](study_export_pack.py) | csv / txt / opml 导出 + 导出统计 | `exporters` + `events` + `storage` + `routes` |
| [study_stats.py](study_stats.py) | 跨方案学习活动统计仪表盘（今日/本周/连续天数） | `events` + `storage` + `scheduler` + `routes` |
| [epub_reader.py](epub_reader.py) | 解析 .epub 电子书为文本（上传自动注入学习方案） | `parsers` + `tools` + `routes` |
| [ai_tutor.py](ai_tutor.py) | AI 解释难点 + tools.call 文本分块组合 | `llm` + `tools`(call) + `routes` |
| [security_guard.py](security_guard.py) | 全站安全响应头 + 请求统计中间件 | `middleware` + `events` + `storage` + `routes` |
| [webhook_notify.py](webhook_notify.py) | 学习事件推送到外部 URL（Slack/Discord） | `webhooks` + `routes` + `storage` |
| [provider_bridge.py](provider_bridge.py) | 接入 OpenAI 兼容协议 LLM（OpenAI/Ollama/DeepSeek） | `providers` + `llm` + `routes` + `storage` |
| [mcp_bridge.py](mcp_bridge.py) | 接入外部 MCP server 工具（filesystem/github 等） | `mcp` + `tools` + `routes` |

停用任一：文件名加 `_` 前缀重启（如 `_study_stats.py`）。

## 本目录文件

- `example_hello.py.disabled`——完整示例插件（路由 + 事件 + 工具 + 数据目录）。
  去掉 `.disabled` 后缀（改名为 `example_hello.py`）即可启用体验。

## 安全提示

后端插件与宿主**同进程、同权限**运行（可读写数据库、可发网络请求）。只放置你信任的插件；
公开端点（`auth=False`）请自行验签，详见 API.md 开发规范 §安全。

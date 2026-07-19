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

## 本目录文件

- `example_hello.py.disabled`——完整示例插件（路由 + 事件 + 工具 + 数据目录）。
  去掉 `.disabled` 后缀（改名为 `example_hello.py`）即可启用体验。

## 安全提示

后端插件与宿主**同进程、同权限**运行（可读写数据库、可发网络请求）。只放置你信任的插件；
公开端点（`auth=False`）请自行验签，详见 API.md 开发规范 §安全。

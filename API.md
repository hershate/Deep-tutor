# API.md - Deep-tutor 插件 API（前端 + 后端）

> **本文件用途**：Deep-tutor 为第三方插件预留的**前端与后端 API 的权威契约文档**。
> 所有预留 API（前端：小组件插槽、皮肤令牌、事件、插件加载、REST 代理；
> 后端：插件装载、api 门面、核心事件、工具注册、路由命名空间）都必须记录在本文件中，
> 以便后续参考与其他开发者的插件开发。**新增/变更任何预留 API 时，必须同步更新本文件。**
>
> 实现源码：前端 [frontend/src/lib/plugins.ts](frontend/src/lib/plugins.ts)；
> 后端 [backend/app/plugins/](backend/app/plugins/)（loader/api/bus/runtime）

**两条独立版本线**（各自语义化版本、独立演进，均承诺 minor/patch 向后兼容、major 记录迁移说明）：

| 版本线 | 当前版本 | 暴露位置 |
|---|---|---|
| 前端插件 API | `1.3.0` | 浏览器全局 `window.DeepTutor.version`（React 树渲染前挂载） |
| 后端插件 API | `1.2.0` | 插件 `register(api)` 收到的 `api.version`；状态端点 `GET /api/plugins` 的 `api_version` |

---

## 一、快速开始（5 分钟写一个插件）

插件是一个普通 JS 文件，通过 URL 加载，执行时用 `window.DeepTutor` 完成注册：

```js
// hello-plugin.js —— 一个最小插件：注册一个侧栏小组件 + 一套皮肤
(function () {
  const DT = window.DeepTutor
  if (!DT) return console.warn('[hello-plugin] DeepTutor API 不可用')

  const h = DT.React.createElement

  // 1) 注册小组件到侧栏底部插槽
  DT.widgets.register({
    id: 'hello.clock',               // 全局唯一，建议带插件名前缀
    slot: 'sidebar.footer',          // 目标插槽（见 §二）
    order: 100,                      // 排序权重，小的在前；0-99 为内置保留
    component: function Clock() {    // 无 props 的 React 组件
      return h('div', { style: { fontSize: 12, color: '#64748b', padding: '4px 8px' } },
        'Hello from plugin · ' + new Date().toLocaleDateString())
    },
  })

  // 2) 注册并应用一套皮肤（CSS 变量令牌）
  DT.skins.register({
    name: 'hello.rounded',           // 唯一标识；'default' 保留
    label: '大圆角',
    tokens: { '--dt-card-radius': '1.25rem' },
    darkTokens: { '--dt-card-radius': '1.25rem' }, // 可选：暗色模式覆盖
  })
  DT.skins.apply('hello.rounded')

  // 3) 监听 API 事件
  DT.events.on('skinchange', ({ name }) => console.log('皮肤已切换:', name))
})()
```

加载方式（二选一）：

```js
// 方式 A：浏览器控制台临时加载（不持久化）
const s = document.createElement('script'); s.src = 'https://example.com/hello-plugin.js'; document.head.appendChild(s)

// 方式 B：经 API 加载并持久化（刷新后自动加载）
await DeepTutor.plugins.load('https://example.com/hello-plugin.js')
```

> **安全提示**：插件脚本以页面完整权限运行（可读 localStorage、可带登录态发请求）。
> 只加载你信任的插件 URL；`plugins.unload()` 移除后需刷新页面才彻底清除其副作用。

---

## 二、小组件 API（`DeepTutor.widgets`）

向命名插槽注册 React 组件。组件在应用 React 树内渲染，**可以使用 hooks**；
数据上下文可用 [TanStack Query](https://tanstack.com/query)（`useQuery`）与 React Router（`Link`/`useNavigate`）。

### 插槽清单（WIDGET_SLOTS）

| 插槽名 | 位置 | 排布 | 说明 |
|---|---|---|---|
| `home.dashboard` | 首页标题区下方、表单卡上方 | 响应式宫格（2 列，宽屏 4 列） | 学习概览小组件；内置 4 个（连续打卡/本周已学/今日待复习/今日费用），无数据自行隐藏 |
| `sidebar.footer` | 桌面端侧栏导航下方、收起按钮上方 | 纵向堆叠 | 仅在侧栏展开态渲染（折叠为图标态时不渲染） |
| `plan.detail`（v1.3.0） | 方案详情页头部操作区下方、学习意图卡上方 | 响应式宫格（2 列，宽屏 4 列） | 方案级插件面板；组件内可经 React Router `useParams()` 取 `planId` 做方案级数据展示/操作 |

> 插槽只增不改：新版本可能追加插槽，已有插槽名不会改名或删除。

### 方法

#### `widgets.register(def: WidgetDef): void`

```ts
interface WidgetDef {
  id: string              // 全局唯一；重复注册同 id 视为覆盖更新
  slot: WidgetSlot        // 'home.dashboard' | 'sidebar.footer' | 'plan.detail'
  component: ComponentType // React 组件，无 props
  order?: number          // 排序权重，小的在前；缺省 100；0-99 内置保留
}
```

参数非法（缺 id / 未知插槽 / component 非函数）抛出 `Error`。

#### `widgets.unregister(id: string): boolean`

幂等移除；返回是否确实移除了一个组件。组件随下次渲染消失。

#### `widgets.list(slot: WidgetSlot): WidgetDef[]`

某插槽当前全部组件（按 order 升序）。主要用于调试。

#### `widgets.subscribe(fn: () => void): () => void`

订阅注册表变化，返回退订函数。一般无需使用（插槽渲染器已自动响应）。

#### `widgets.slots(): readonly string[]`（v1.2.0）

返回当前宿主支持的全部插槽名（只读）。用于**能力发现**：插件在旧宿主上可跳过新插槽降级，
在新宿主上自动可用，无需解析/比较版本号：

```js
if (DeepTutor.widgets.slots().includes('sidebar.footer')) {
  DeepTutor.widgets.register({ id: 'my.x', slot: 'sidebar.footer', component: X })
}
```

### 小组件开发建议

- **无数据时返回 `null` 自行隐藏**（内置组件均如此），避免给新用户渲染空卡片。
- 视觉风格请复用共享卡片范式：圆角 `var(--dt-card-radius, 0.75rem)` + 边框 + 白底 + 浅阴影
  （内置实现见 [frontend/src/components/Card.tsx](frontend/src/components/Card.tsx)），
  这样皮肤令牌（§三）能覆盖到你的组件。
- 暗色模式：使用 Tailwind `dark:` 变体或 CSS 变量，两种外观都要可读。
- 数字使用 `tabular-nums`（等宽数字），避免刷新时跳动。
- 组件渲染异常会被**单组件级错误边界**隔离（v1.2.0）：抛异常的组件自身隐藏、
  错误输出到控制台，不影响应用与其余小组件；同 id 重新注册新组件后该组件获得重试机会。
  即便如此，仍请保证组件不抛异常——被隔离的组件对用户不可见。

---

## 三、皮肤 API（`DeepTutor.skins`）

皮肤 = 一组 CSS 变量令牌，应用到 `<html>` 元素上，亮/暗模式可分别给值。
皮肤选择持久化于 `localStorage('dt_skin')`，刷新后自动恢复；
应用内亮/暗切换（顶栏 🌙/☀️）后会自动重放当前皮肤的暗色令牌。

### v1 受支持令牌

| 令牌 | 默认值 | 消费方 |
|---|---|---|
| `--dt-card-radius` | `0.75rem` | 共享卡片（Card）与全部内置小组件的圆角 |
| `--dt-font-sans` | 系统字体栈 | `body` 全局正文字体族 |

> 只允许设置 `--dt-*` 命名空间的变量（其余键会被忽略）。
> 后续版本将追加更多令牌（品牌色、阴影、密度等），已列令牌不会改名或删除。

### 方法

#### `skins.register(def: SkinDef): void`

```ts
interface SkinDef {
  name: string                      // 唯一标识；'default' 保留给内置默认外观
  label: string                     // 展示名
  tokens?: Record<string, string>   // 亮色模式令牌
  darkTokens?: Record<string, string> // 暗色模式追加/覆盖令牌
}
```

`name` 缺失或为 `'default'` 时抛出 `Error`。

#### `skins.apply(name: string): boolean`

应用皮肤；未知名称返回 `false`。应用结果持久化，刷新后保持。
`skins.apply('default')` 清除全部自定义令牌，回到默认外观。

#### `skins.active(): string`

当前皮肤名。

#### `skins.list(): SkinDef[]`

全部已注册皮肤（含内置 `default`）。

#### `skins.subscribe(fn: () => void): () => void`

订阅皮肤切换，返回退订函数。

---

## 四、事件 API（`DeepTutor.events`）

### 事件清单

| 事件名 | payload | 触发时机 |
|---|---|---|
| `skinchange` | `{ name: string }` | 皮肤切换后（含亮/暗切换引起的令牌重放） |
| `taskchange`（v1.1.0） | `{ id: string; kind: 'plan' \| 'materials'; phase: string; title: string }` | 方案/材料生成任务的每次相位迁移（`phase`：`running` / `clarify` / `done` / `error` / `cancelled` / `interrupted`；`id` 形如 `plan:<sessionId>`、`materials:<planId>`） |

> 事件只增不改：新版本可能追加事件，已有事件名与 payload 结构向后兼容。
>
> **前向兼容**：订阅宿主尚不存在的事件名不会报错——监听器只是永不触发。
> 插件因此可以放心订阅新版本事件，在旧宿主上自然降级为无操作。
>
> `taskchange` 仅通知相位变化这一事实；任务的全量状态（进度步骤、费用、错误详情等）
> 可经 `api.get('/sessions')` / `api.get('/sessions/<id>')` 主动查询（含 `status`、
> `live`（running 时实时进度）、`error`、`latest_plan` 字段）。

### 方法

#### `events.on(event, fn): () => void`

```ts
const off = DeepTutor.events.on('skinchange', ({ name }) => { /* ... */ })
off() // 退订
```

监听器异常被捕获并输出到控制台，不影响其他监听器与应用。

---

## 五、REST 代理（`DeepTutor.api`）

以**当前登录态**（JWT 或 AUTH_TOKEN，与前端自身请求同一机制）调用后端 REST API。

#### `api.get<T = unknown>(path: string): Promise<T>`

```ts
const stats = await DeepTutor.api.get('/progress/stats/summary')
// => { total_minutes, week_minutes, streak_days, total_logs }

const usage = await DeepTutor.api.get('/usage')
// => { total_tokens, total_cost, cache_hit_rate, calls, today_cost, daily_budget_usd }

const due = await DeepTutor.api.get('/progress/review/due')
// => { items: [...], total }
```

- `path` 以 `/` 开头，相对 `/api` 前缀。
- 失败抛出 `ApiError`（含 HTTP 状态码）；401 时若为过期的 JWT 会话，前端会自动跳转登录页（与自身请求行为一致）。
- 后端 REST 端点全集见 [plan/03-api-frontend.md](plan/03-api-frontend.md) §一
  （REST 端点为内部契约，可能随版本演进；插件应做好兼容）。

#### `api.request<T = unknown>(path: string, init?: ApiRequestInit): Promise<T>`（v1.2.0）

全方法 REST 代理（`api.get` 即 `api.request` 的 GET 便捷形式）：

```ts
interface ApiRequestInit {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' // 缺省 GET
  body?: unknown        // 任意可 JSON 序列化值，自动 stringify
  timeoutMs?: number    // 缺省 120s；长任务端点（导出/生成类）建议 300_000
  signal?: AbortSignal  // 组件卸载时中断请求
}
```

```js
// 例：插件为用户打卡 25 分钟学习记录
await DeepTutor.api.request('/progress/logs', {
  method: 'POST',
  body: { plan_id: 1, minutes: 25, note: '番茄钟插件自动打卡' },
})
```

错误语义（`ApiError`、401 跳登录）与 `api.get` 完全一致。

---

## 六、插件加载管理（`DeepTutor.plugins`）

#### `plugins.load(url: string): Promise<void>`

加载外部插件脚本并**持久化**到 `localStorage('dt_plugins')`（刷新/重开自动按序加载）。
URL 必须 `http(s)://` 开头；脚本加载失败 reject。

#### `plugins.unload(url: string): void`

从持久化列表移除。**注意**：已执行脚本的副作用（已注册的小组件/皮肤、定时器等）
需刷新页面才消除；小组件可在卸载前先 `widgets.unregister(id)` 主动清理。

#### `plugins.list(): string[]`

当前持久化的插件 URL 列表。

---

## 七、完整示例：番茄钟侧栏小组件

```js
// pomodoro-plugin.js
(function () {
  const DT = window.DeepTutor
  const h = DT.React.createElement
  const { useState, useEffect } = DT.React

  function Pomodoro() {
    const [sec, setSec] = useState(25 * 60)
    useEffect(() => {
      const t = setInterval(() => setSec((s) => (s > 0 ? s - 1 : 0)), 1000)
      return () => clearInterval(t)
    }, [])
    const mm = String(Math.floor(sec / 60)).padStart(2, '0')
    const ss = String(sec % 60).padStart(2, '0')
    return h('div', {
      style: {
        borderRadius: 'var(--dt-card-radius, 0.75rem)',
        border: '1px solid #e2e8f0', padding: '8px 12px', background: '#fff',
        fontVariantNumeric: 'tabular-nums', fontSize: 14,
      },
    }, `🍅 ${mm}:${ss}`)
  }

  DT.widgets.register({ id: 'pomodoro.timer', slot: 'sidebar.footer', order: 100, component: Pomodoro })
})()
```

加载：`await DeepTutor.plugins.load('https://你的域名/pomodoro-plugin.js')`

---

## 八、后端插件 API（`plugins/` 目录）

后端插件 = 一个 Python 文件（或包），放入插件目录后**重启后端自动装载**，通过
`register(api)` 门面获得扩展能力：挂 REST 路由、订阅核心事件、注册 Agent 工具。

- **API 版本**：`1.2.0`（`api.version`，与前端 API 独立版本线）
- **兼容性承诺**：minor/patch 向后兼容（只增不改）；插件只依赖 `api` 门面即受本承诺保护——
  **直接 import 宿主内部模块不在兼容性承诺范围内**（内部重构不另行通知）。
- **运行环境**：后端进程内（与宿主同权限，见 §九 安全规范）。
- **插件目录**：`PLUGINS_DIR`（默认 `<仓库根>/plugins`，相对路径锚定仓库根）。

### 8.1 快速开始（3 分钟写一个后端插件）

```python
# plugins/hello.py —— 最小后端插件：一个 REST 端点 + 一个事件订阅
from fastapi import APIRouter, Depends

PLUGIN_META = {"id": "hello", "name": "Hello", "version": "0.1.0"}

def register(api):
    router = APIRouter()

    @router.get("/hello")
    def hello(user=Depends(api.deps.get_current_user)):
        return {"message": f"你好，{user.name}！", "plugin": api.id}

    api.routes.mount(router)                      # -> /api/plugins/hello/hello（默认带宿主认证）
    api.events.on("plan_created", lambda p: api.log.info("新方案: %s", p.get("topic")))
```

装载与验证：

```bash
# 1. 重启后端（当前版本不支持热重载）
# 2. 验证端点（带你的认证头）
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/plugins/hello/hello
# 3. 查看插件加载状态（管理员）
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/plugins
```

### 8.2 装载方式

| 形态 | 约定 | 说明 |
|---|---|---|
| 单文件插件 | `plugins/foo.py` | 一个文件一个插件 |
| 包插件 | `plugins/foo/plugin.py` + 任意辅助模块 | 包内用**相对导入**（`from . import helper`） |
| 临时停用 | 文件/目录名加 `_` 前缀（`_foo.py` / `_foo/`） | 跳过加载，无需删除 |

- 发现顺序按文件名排序（确定性加载）；加载结果逐插件记录（见 §8.6 状态端点与启动日志）。
- **重启生效**：放置/修改/停用插件后需重启后端。
- 每个插件获得私有数据目录 `<DATA_DIR>/plugins/<插件id>/`（自动创建，`api.data_dir` 取得）。
- 单个插件导入失败、`register` 抛异常、缺 `register`、id 非法/重复、`min_api` 不兼容
  均只记该插件 `failed`，**不阻塞其余插件与宿主启动**。

### 8.3 PLUGIN_META 与版本协商

```python
PLUGIN_META = {
    "id": "my_plugin",        # 可选，缺省取文件名/目录名。路由命名空间用，
                              # 须匹配 [a-z0-9][a-z0-9._-]{0,63}（小写字母开头）
    "name": "我的插件",        # 可选，展示名
    "version": "0.1.0",       # 可选，插件自身版本（状态端点展示）
    "description": "...",     # 可选
    "min_api": "1.0",         # 可选，要求的最低后端插件 API 版本
}
```

- `min_api` 协商语义：主版本号与宿主不同、或次版本号高于宿主 -> 拒绝加载（记 failed）。
  未声明则不阻拦。
- 更细粒度的能力判断由插件自决：`api.version` 可读；订阅不存在的事件静默无效（前向兼容）。
- 宿主承诺：新增命名空间/方法/事件只增不改；插件面向 `api` 门面编程即可跨版本生存。

### 8.4 `api` 门面参考

| 成员 | 类型 | 说明 |
|---|---|---|
| `api.version` | `str` | 后端插件 API 语义化版本 |
| `api.id` | `str` | 本插件 id（路由命名空间/日志名/数据目录同名） |
| `api.log` | `logging.Logger` | 插件专属 logger（`deep_tutor.plugin.<id>`） |
| `api.data_dir` | `pathlib.Path` | 插件私有数据目录（已创建），持久化插件自身状态 |

#### `api.routes` —— REST 路由（命名空间隔离）

```python
api.routes.mount(router, *, auth: bool = True, prefix: str = "") -> str
```

- 将 FastAPI `APIRouter` 挂载到 `/api/plugins/<插件id>[prefix]`，返回实际前缀。
- 路由自动带 OpenAPI 标签 `plugin:<插件id>`（`/docs` 中按插件分组，便于发现与调试）。
- `auth=True`（默认）：整个路由器套宿主同一认证（JWT / AUTH_TOKEN / 开放本机模式），
  未认证 401 与宿主端点语义一致。`auth=False` 用于公开 webhook 等场景——**必须自行验签**（§九）。
- `api.routes.base_path`：本插件命名空间前缀（拼绝对路径用）。
- 路由内可正常使用 FastAPI 全部能力（DI、后台任务、校验模型等）。

#### `api.deps` —— 宿主依赖的稳定引用（路由 DI 用）

| 成员 | 用途 |
|---|---|
| `get_db` | 数据库会话（`Depends(api.deps.get_db)`） |
| `get_current_user` | 当前登录用户（401 未认证）；数据隔离端点以此取归属 |
| `require_auth` | 仅校验认证（不取用户） |
| `require_admin` | 管理员守卫 |
| `get_owned_session` / `get_owned_plan` | 归属守卫：他人资源统一 404（防枚举） |

#### `api.events` —— 事件订阅/发布

```python
off = api.events.on("progress_updated", handler)   # handler 可为同步函数或协程函数
off()                                              # 退订
api.events.emit("myplugin.custom", {"k": "v"})     # 发布自定义事件（插件间协作）
```

- 监听器异常**逐人隔离**：一个监听器抛异常不影响其余监听器与宿主流程（记日志）。
- 订阅未知事件名不报错、静默无效（前向兼容：订阅新版本事件在旧宿主上自然降级）。
- 自定义事件请以插件 id 作命名空间（`"myplugin.xxx"`），避免与未来核心事件撞名。

#### `api.tools` —— Agent 工具注册

```python
api.tools.register_python("myplugin.word_count", async_func)  # 便捷：Python 工具
api.tools.register(tool)                                      # 完整 Tool（LLM 工具亦可）
```

- 插件工具汇入**每次请求新建**的工具注册表（`default_registry()`），插件路由/其他插件
  可经注册表显式调用；Python 工具签名 `async (inp, ctx) -> dict`（data 字典，
  契约同宿主工具）。
- **注意**：内置生成 PIPELINE 步骤固定，不会自动调用新工具；与核心工具同名会覆盖核心
  实现（注册表告警）——替换核心工具属高级用法，请充分测试（§九）。

#### `api.settings` —— 只读配置视图

```python
api.settings.get("anthropic_model", default=None)   # 按名读取非机密配置
api.settings.data_dir / api.settings.plugins_dir    # 常用路径属性
```

名称含 `key/secret/password/token` 的字段一律返回 default（防顺手误读机密外发；
这不是安全边界——插件与宿主同进程，本身即受信代码）。

### 8.5 核心事件清单

| 事件名 | payload | 触发时机 |
|---|---|---|
| `startup` | `{}` | 后端启动完成、全部插件加载后（await 逐个监听器） |
| `shutdown` | `{}` | 后端开始关闭时（await 逐个监听器） |
| `plan_created` | `{ plan_id: int, session_id: int, version: int, topic: str }` | 方案新版本落库后（生成/重生成/重试统一收口） |
| `progress_updated` | `{ user_id: int, plan_id: int, node_id: str, status: str, mastery: float }` | 单节点进度更新提交后 |
| `quiz_submitted` | `{ user_id: int, quiz_id: int, plan_id: int, type: str \| null, correct: bool \| null }` | 测验作答提交后 |
| `materials_completed`（v1.1.0） | `{ plan_id: int, session_id: int, unit_count: int, degraded_count: int, partial: bool }` | 自学材料生成完成（SSE 逐单元流与同步 JSON 生成两路径均广播；`partial: true` 表示 scope/unit_ids 局部重生成；幂等重渲染不广播——无新生成） |
| `learning_log_added`（v1.2.0） | `{ user_id: int, plan_id: int, log_id: int, date: str, minutes: int, note: str \| null }` | 学习打卡提交后（`date` 为 ISO 日期） |

> 事件只增不改：新版本可能追加事件与 payload 键；已有事件名与既有键向后兼容。
> 请求路径上的事件（后五个）以「不阻塞响应」方式广播：同步监听器就地执行，
> 协程监听器调度为后台任务。**监听器务必轻量**，重活（网络调用/大计算）请自行后台化。

### 8.6 可观测性：`GET /api/plugins`（管理员）

```json
{
  "api_version": "1.0.0",
  "plugins": [
    {
      "id": "hello", "name": "Hello", "version": "0.1.0",
      "state": "loaded", "error": "", "file": ".../plugins/hello.py",
      "routes": ["/api/plugins/hello"],
      "events": ["plan_created"],
      "tools": []
    }
  ],
  "subscribed_events": ["plan_created", "shutdown", "startup"]
}
```

`state: "failed"` 时 `error` 给出失败原因（import/register/id 非法/重复/min_api 不兼容）。

### 8.7 完整示例

仓库自带可启用示例：[plugins/example_hello.py.disabled](plugins/example_hello.py.disabled)
（路由 + 事件 + 工具 + 数据目录 + 配置读取）。去掉 `.disabled` 后缀重启即可体验。

---

## 九、插件开发规范

面向所有插件作者（前后端通用 + 后端专项）。遵守本规范可保证插件在宿主未来版本中
持续可用，且不影响宿主与其他插件的稳定性。

### 9.1 稳定性

1. **不要让异常逃出你的代码**：`register`、事件监听器、路由处理器、工具函数都应自行
   捕获可预期异常。宿主虽有隔离（后端逐监听器隔离、前端组件错误边界），但异常意味着
   你的功能中断。
2. **`register` 保持幂等、快速**：只做注册，不做网络请求/重计算（启动路径，慢了拖垮启动；
   需要初始化请挂到 `startup` 事件）。
3. **只依赖门面契约**：后端插件只用 `api` 门面与本文档列明的内容；前端插件只用
   `window.DeepTutor`。直接 import/引用宿主内部实现不受兼容性承诺保护。
4. **不篡改宿主状态**：不重写宿主路由、不猴子补丁宿主模块、不改写非本插件创建的
   localStorage 键（前端）/数据库表（后端）。

### 9.2 安全

1. **插件 = 完全信任的代码**：后端插件与宿主同进程同权限（可读写数据库、发网络请求）；
   前端插件以页面完整权限运行（可读登录态）。只安装你信任的插件，发布插件即发布权限。
2. **路由默认带认证**：仅对公开 webhook 等场景使用 `auth=False`，且必须自行验签
   （如 HMAC 签名头）；公开端点不返回任何用户数据。
3. **不泄露机密**：不读取/外发宿主密钥（`api.settings` 已对机密字段兜底隐藏）；
   插件自身的第三方凭证存 `api.data_dir` 下文件，不硬编码进源码。
4. **校验一切外部输入**：路由入参用 pydantic 模型校验；不拼接 SQL（用 SQLAlchemy 参数化）。

### 9.3 数据隔离（多用户）

1. 涉及用户数据的路由必须 `Depends(api.deps.get_current_user)` 并按 `user.id` 过滤。
2. 访问会话/方案资源用 `api.deps.get_owned_session` / `get_owned_plan`（他人资源 404，
   不泄露存在性）。
3. 跨用户聚合仅限明确的管理员场景，并套 `api.deps.require_admin`。

### 9.4 兼容性与版本

1. 宿主对两条 API 线分别承诺语义化版本：minor/patch 只增不改，breaking 变更升 major
   并在本文档记录迁移说明。
2. 插件应做**能力发现**而非版本硬判：后端订阅不存在的事件静默无效；
   前端 `widgets.slots()` 探插槽、`events.on` 探事件、`DeepTutor.version` 兜底。
3. 声明 `min_api`（后端）让你的插件在不兼容宿主上优雅拒绝，而不是运行到一半出错。

### 9.5 性能

1. 事件监听器轻量：网络调用/重计算放到你自己的后台线程/任务队列。
2. 数据库会话短生命周期：路由内 `Depends(api.deps.get_db)`，用毕即放；不跨请求持有会话。
3. 前端小组件避免高频重渲染与无界增长（参考 §二 开发建议）。

### 9.6 测试与发布

1. **测试**：后端插件可参照 [backend/tests/test_plugins.py](backend/tests/test_plugins.py)
   的模式——临时目录写插件文件 + `load_plugins(app, dir)` + `TestClient` 断言路由/事件行为；
   至少覆盖：加载成功、路由认证语义、事件收发。
2. **发布形态**：单文件 `.py` 或一个目录（含 `plugin.py` 与 README）；README 说明
   用途、配置项、权限范围（用到哪些路由/事件/数据）。
3. **版本纪律**：`PLUGIN_META.version` 随发布递增；变更写 CHANGELOG；
   破坏性变更同时提升 `min_api` 并在 README 显著说明。

---

## 十、版本历史

| 版本线 | 版本 | 日期 | 变更 |
|---|---|---|---|
| 后端 | 1.2.0 | 2026-07-19 | 新增 `learning_log_added` 事件（学习打卡提交后广播，用户行为事件目录补齐） |
| 前端 | 1.3.0 | 2026-07-19 | 新增插槽 `plan.detail`（方案详情页头部下方宫格；组件内可 `useParams()` 取 planId 做方案级面板） |
| 后端 | 1.1.0 | 2026-07-19 | 新增 `materials_completed` 事件（材料生成完成；SSE/同步两路径均广播，幂等重渲染不广播） |
| 后端 | 1.0.0 | 2026-07-19 | 首个后端插件 API：`plugins/` 目录自动发现装载（单文件/包插件、`_` 前缀停用）、`api` 门面（routes/events/tools/settings/deps/data_dir/log）、核心事件（startup/shutdown/plan_created/progress_updated/quiz_submitted）、路由命名空间 `/api/plugins/<id>/`（默认认证）、状态端点 `GET /api/plugins`、插件工具汇入 `default_registry()` |
| 前端 | 1.2.0 | 2026-07-19 | 新增 `api.request`（全方法 REST 代理）、`widgets.slots()`（插槽能力发现）；小组件渲染异常改为单组件级错误边界隔离（此前会拖垮整个应用）；widgets/skins 订阅回调异常隔离（与 events 语义一致）；文档补事件前向兼容说明 |
| 前端 | 1.1.0 | 2026-07-19 | 新增 `taskchange` 事件（任务相位迁移；配合 `api.get('/sessions')` 查询任务全量状态） |
| 前端 | 1.0.0 | 2026-07-19 | 首个预留版本：`widgets`（插槽 `home.dashboard` / `sidebar.footer`）、`skins`（令牌 `--dt-card-radius` / `--dt-font-sans`）、`events`（`skinchange`）、`api.get`、`plugins.load/unload/list` |

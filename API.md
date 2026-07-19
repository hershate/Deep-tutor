# API.md - Deep-tutor 前端美化插件 API

> **本文件用途**：Deep-tutor 为第三方美化/功能插件预留的前端 API 的**权威契约文档**。
> 所有预留 API（小组件插槽、皮肤令牌、事件、插件加载、REST 代理）都必须记录在本文件中，
> 以便后续参考与其他开发者的插件开发。**新增/变更任何预留 API 时，必须同步更新本文件。**
>
> 实现源码：[frontend/src/lib/plugins.ts](frontend/src/lib/plugins.ts)

- **API 版本**：`1.2.0`（`DeepTutor.version`，语义化版本）
- **兼容性承诺**：minor / patch 版本向后兼容（只增不改）；major 版本变更会在本文记录迁移说明。
- **运行环境**：浏览器端。应用启动后全局对象 `window.DeepTutor` 可用（在 React 树渲染前挂载）。

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

> 插槽只增不改：新版本可能追加插槽，已有插槽名不会改名或删除。

### 方法

#### `widgets.register(def: WidgetDef): void`

```ts
interface WidgetDef {
  id: string              // 全局唯一；重复注册同 id 视为覆盖更新
  slot: WidgetSlot        // 'home.dashboard' | 'sidebar.footer'
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

## 八、版本历史

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.2.0 | 2026-07-19 | 新增 `api.request`（全方法 REST 代理）、`widgets.slots()`（插槽能力发现）；小组件渲染异常改为单组件级错误边界隔离（此前会拖垮整个应用）；widgets/skins 订阅回调异常隔离（与 events 语义一致）；文档补事件前向兼容说明 |
| 1.1.0 | 2026-07-19 | 新增 `taskchange` 事件（任务相位迁移；配合 `api.get('/sessions')` 查询任务全量状态） |
| 1.0.0 | 2026-07-19 | 首个预留版本：`widgets`（插槽 `home.dashboard` / `sidebar.footer`）、`skins`（令牌 `--dt-card-radius` / `--dt-font-sans`）、`events`（`skinchange`）、`api.get`、`plugins.load/unload/list` |

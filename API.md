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
| 前端插件 API | `1.7.0` | 浏览器全局 `window.DeepTutor.version`（React 树渲染前挂载） |
| 后端插件 API | `1.6.0` | 插件 `register(api)` 收到的 `api.version`；状态端点 `GET /api/plugins` 的 `api_version` |

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

## 二补 · 页面 API（`DeepTutor.pages`，v1.4.0）

> **"用 API 完全替换前端"的核心使能项。** 小组件（§二）只能往既有页面的固定插槽注入卡片；
> 页面 API 让插件注册**完整的路由页 + 可选侧栏导航**——插件因此能构建自己的整套页面
> （如一个完全不同风格的学习仪表盘 / 专注工具 / 复习游戏），不再受插槽限制。

### 路径命名空间 `/p/`（安全默认）

插件页面一律挂载在 **`/p/<path>`** 下，与内置路由（`/`、`/plans`、`/notes`、…）**零碰撞**——
插件**不可能覆盖**任何既有页面。`path` 支持多级（`'pomodoro/stats'` → `/p/pomodoro/stats`），
仅允许字母数字 `-` `_` 段、以 `/` 分隔；含 `..` 或非法字符的路径注册时报错。

### `pages.register(def: PageDef): void`

```ts
interface PageDef {
  path: string                 // 相对 /p/ 的路径（如 'pomodoro'）；唯一，重复注册同 path 覆盖更新
  component: ComponentType     // React 组件，无 props；数据获取用组件内 hooks
  title?: string               // 页面标题（document.title），可选
  nav?: {                      // 可选：注册侧栏导航项；缺省则页面可达但不出现在导航（深链/二级页）
    label: string              // 侧栏显示名
    icon?: string              // 图标（emoji/字符），缺省 🧩
    order?: number             // 排序权重，小的在前，缺省 100
  }
}
```

参数非法（缺 path / 非法路径 / component 非函数 / nav 缺 label）抛出 `Error`。

```js
// 例：番茄钟插件注册一个完整页面 + 侧栏入口
DT.pages.register({
  path: 'pomodoro',
  title: '番茄钟',
  component: PomodoroPage,          // 完整 React 页面组件（可用 hooks / api.get 拉数据）
  nav: { label: '番茄钟', icon: '🍅', order: 100 },
})
// -> 侧栏出现「🍅 番茄钟」，点击打开 /p/pomodoro 渲染 PomodoroPage
```

### 其余方法

- `pages.unregister(path): boolean` —— 幂等移除；页面随下次渲染消失，导航项一并移除。
- `pages.get(path): PageDef | undefined` —— 查询某 path 的页面定义。
- `pages.list(): PageDef[]` —— 全部页面（含无 nav 的，按 path 升序）。主要供调试。
- `pages.navItems(): PageDef[]` —— 带导航项的页面（按 nav.order 升序），侧栏数据源。
- `pages.subscribe(fn): () => void` —— 订阅注册表变化，返回退订函数（一般无需使用）。

### 宿主行为保证

- **应用壳内渲染**：插件页面渲染在应用 Layout 内（顶栏 / 侧栏可用），体验与内置页面一致。
- **单页错误边界**：插件页面渲染异常被**隔离**（不白屏整应用），换页自愈；异常输出到控制台。
- **响应式**：插件在导航后异步注册页面时（如刷新后自动加载顺序），注册即生效命中。
- **未命中兜底**：访问未注册 / 已卸载的 `/p/...` 路径，显示「插件页面不存在」+ 回首页链接，
  绝不静默或白屏。

---

## 二补二 · 路由/导航 API（`DeepTutor.router`，v1.5.0）

> **页面 API 的配套能力。** 纯 JS 插件拿不到 react-router 实例（无构建链）——没有本命名空间，
> 插件页面之间只能 `location.href` 整页跳转（丢 SPA 状态、白屏闪烁），做不出"完整的另一个前端"。
> 本命名空间以门面形式暴露**最小路由面**：SPA 导航 + 路由变化订阅（语义由宿主控制，
> 不泄露 react-router 版本细节，插件不受宿主路由库升级影响）。

### `router.navigate(to: string, opts?: { replace?: boolean }): void`

站内 SPA 导航（**不整页刷新**）。`to` 须以 `/` 开头（如 `/p/reader?id=3`、`/plans`），
否则抛 `Error`；`opts.replace=true` 替换当前历史条目而非 push（重定向语义）。

```js
// 例：插件仪表盘卡片点击 -> 跳进插件详情页（SPA，无闪烁）
DT.router.navigate('/p/reader?planId=' + planId)
// 重定向语义（如插件的"设置完成"后替换历史，回退键不回到表单页）
DT.router.navigate('/p/home', { replace: true })
```

### 路径快照与订阅

- `router.path(): string` —— 当前 pathname（如 `/p/reader`）。
- `router.href(): string` —— 当前 pathname+search+hash（如 `/p/reader?id=3`）。
- `router.subscribe(fn): () => void` —— 路由变化订阅，返回退订函数。
  （React Router 对 push 不发 `popstate`，插件无法自行可靠监听，故由门面提供。）

三者配合 `useSyncExternalStore` 可做**响应式路由渲染**（如插件自己的 tab 条高亮当前页）：

```js
const h = DT.React.createElement
const { useSyncExternalStore } = DT.React
function CurrentPath() {
  const href = useSyncExternalStore(DT.router.subscribe, DT.router.href)
  return h('span', null, '当前位置: ' + href)
}
```

> **查询参数读取无需门面**：插件与宿主同窗口，`window.location.search` +
> `new URLSearchParams(...)` 随时可用（如深链 `/p/reader?planId=3` 在组件挂载时读取）。

---

## 二补三 · 覆盖层 API（`DeepTutor.overlay`，v1.6.0）

> **"用 API 完全改造前端"的使能项。** 小组件（§二）只能往既有页面的固定插槽注入卡片、
> 页面（§二补）只能在 `/p/` 下渲染整页——两者都无法"在整个应用之上"绘制内容。要做
> **WebGL 全屏背景特效 / 可自由拖放的浮窗 / 2D 物理碰撞叠层 / 全屏 3D 小游戏**，都需要一个
> **脱离页面、覆盖整个视口**的渲染层。本命名空间把"应用根级 React 渲染"纳入受支持的插件门面
> （错误边界隔离 + pointer-events 控制 + 生命周期清理），而非让插件用裸 DOM 硬塞
> `document.body`（绕过 React、无清理、不受错误边界保护、升级即碎）。

### 三层 z-index 分层

注册时选一个 `layer`，决定渲染栈位置与默认行为：

| layer | z-index | 位置 | 典型用途 | 默认 pointer-events |
|---|---|---|---|---|
| `background` | `-1` | 应用内容**之后**（仅透过应用透明区域——页边/留白/或插件自设透明区——可见，body 背景始终兜底） | WebGL 动态背景、粒子场 | `none`（在应用之后，本就不可交互） |
| `content` | `45` | 应用内容之上、**模态/Toast 之下**（高于吸顶 header） | 可拖放浮窗、HUD、画布叠层、2D 物理场景 | `none`（穿透到下层应用；交互元素请设 `auto`） |
| `top` | `9999` | **万物之上**（盖过模态/Toast） | 全屏 3D 小游戏、彩纸/烟花、接管式特效 | `none`（同上；游戏 canvas 设 `auto`） |

> 分层只增不改：新版本可能追加分层，已有分层名不会改名或删除。
>
> **pointer-events 关键语义**：层容器与包裹层**默认 `none`**——覆盖层**不会拦截**下层应用的点击/滚动，
> 避免一个全屏 overlay 误锁整个 UI。需要交互（拖拽浮窗、3D 游戏接输入）时，注册时设
> `pointerEvents: 'auto'`，此时**该 overlay 的整个区域**会捕获指针事件（其余 overlay 与应用不受影响）。

### `overlay.register(def: OverlayDef): void`

```ts
interface OverlayDef {
  id: string               // 全局唯一；重复注册同 id 视为覆盖更新
  layer: OverlayLayer      // 'background' | 'content' | 'top'
  component: ComponentType // React 组件，无 props；可用 hooks / TanStack Query / React Router
  pointerEvents?: 'none' | 'auto'  // 缺省 'none'（点击穿透）；交互浮窗/游戏设 'auto'
  order?: number           // 同层内绘制顺序，小的在前（后绘制、视觉靠上）；缺省 100；0-99 内置保留
}
```

参数非法（缺 id / 未知 layer / component 非函数）抛出 `Error`。

```js
// 例 1：WebGL 动态背景（应用内容之后，透过留白可见）
DT.overlay.register({
  id: 'acme.bg',
  layer: 'background',
  component: WebGLBackground,  // 内部 <canvas> 满 viewport 绘制 shader 动画
})

// 例 2：可拖放浮窗（应用内容之上、模态之下；整窗需交互 -> pointerEvents:'auto'）
DT.overlay.register({
  id: 'acme.floating-notes',
  layer: 'content',
  pointerEvents: 'auto',
  component: FloatingPanel,    // 内部自管绝对定位 + 拖拽（pointer-events 已在包裹层开启）
})

// 例 3：全屏 3D 小游戏（盖过一切；接管输入）
DT.overlay.register({
  id: 'acme.3d-quest',
  layer: 'top',
  pointerEvents: 'auto',
  component: ThreeDGame,       // Three.js/Babylon 场景；满屏 canvas 接管键鼠
})
```

### 覆盖层其余方法

- `overlay.unregister(id): boolean` —— 幂等移除；组件随下次渲染消失。
- `overlay.list(layer?): OverlayDef[]` —— 某分层（缺省全部层）当前覆盖层（按 order 升序）。主要供调试。
- `overlay.layers(): readonly string[]` —— 当前宿主支持的全部分层名（只读）。用于**能力发现**：
  插件在旧宿主上可跳过新分层降级，在新宿主上自动可用，无需解析/比较版本号：

```js
if (DeepTutor.overlay.layers().includes('top')) {
  DeepTutor.overlay.register({ id: 'my.game', layer: 'top', component: Game })
}
```

- `overlay.subscribe(fn): () => void` —— 订阅注册表变化，返回退订函数（一般无需使用，宿主已自动响应）。

### 覆盖层宿主行为保证

- **全路由渲染**：覆盖层渲染在应用根级（`document.body` 上的门户容器），覆盖**所有页面**含登录/设置页；
  组件内可用 `useLocation()`/`router.path()` 按路由自显隐（如只在 `/plans` 显示某 HUD）。
- **单 overlay 错误边界**：覆盖层渲染异常被**隔离**（该 overlay 自身隐藏、错误输出到控制台），
  不影响应用、其余 overlay 与同层其他组件；同 id 重新注册新组件后获重试机会。即便如此，仍请保证组件不抛异常。
- **React 上下文可用**：覆盖层组件渲染在 `BrowserRouter` + `QueryClientProvider` 内，可正常使用
  `useNavigate`/`useLocation`（SPA 导航）与 `useQuery`（取后端数据，经 `api.get` 同一登录态）。
- **可观测**：已注册覆盖层记录在「插件」管理页（`/plugins`）「当前生效」区，按分层聚合（管理员/用户均可见）。
- **渲染载体**：宿主为每分层在 `document.body` 上创建一个 `position:fixed;inset:0` 容器
  （`id="dt-overlay-back|content|top"`，`pointer-events:none`），插件组件在其中自由绘制；
  **请勿直接操作这些容器**（内部实现，升级不另行通知）——只经门面注册。

> **能力提示（无需门面即可用）**：覆盖层组件与宿主同窗口，可直接用 `document.createElement('script')`
> 从 CDN 载入 [Three.js](https://threejs.org/) / [Matter.js](https://brm.io/matter-js/) / [p5.js](https://p5js.org/)
> 等图形/物理库，或 `fetch`/`Image()` 载入纹理资源——3D 小游戏、2D 物理所需的全部生态库都可用。
> 门面只负责"把你的 React 组件放到正确的全屏层"，绘图与物理引擎由你自选。

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

事件有两类来源，插件用同一 `events.on` 订阅：

- **前端本地事件**：`skinchange` / `taskchange`（本进程内 `emit`）。
- **后端事件桥**（v1.7.0，iter 315）：宿主在登录态自动连接 `GET /api/events/stream`（SSE，
  见 §8.7），把**后端事件总线**事件实时转发为 `DeepTutor.events`——插件订阅即**实时**收到
  （方案生成完成、学习进度更新、测验提交、打卡、材料生成完成等），**免轮询**。这是
  "用 API 完全替换前端"的实时性使能项（替代前端做 live 仪表盘时，数据变化自动推送）。

### 事件清单

| 事件名 | payload | 来源 | 触发时机 |
|---|---|---|---|
| `skinchange` | `{ name: string }` | 前端 | 皮肤切换后（含亮/暗切换引起的令牌重放） |
| `taskchange`（v1.1.0） | `{ id; kind; phase; title }` | 前端 | 方案/材料生成任务的每次相位迁移 |
| `plan_created`（v1.7.0） | `{ plan_id; session_id; version; topic }` | 后端 | 方案新版本落库后（生成/重生成/重试统一收口） |
| `progress_updated`（v1.7.0） | `{ user_id; plan_id; node_id; status; mastery }` | 后端 | 单节点进度更新提交后 |
| `quiz_submitted`（v1.7.0） | `{ user_id; quiz_id; plan_id; type; correct }` | 后端 | 测验作答提交后 |
| `materials_completed`（v1.7.0） | `{ plan_id; session_id; unit_count; degraded_count; partial }` | 后端 | 自学材料生成完成 |
| `learning_log_added`（v1.7.0） | `{ user_id; plan_id; log_id; date; minutes; note }` | 后端 | 学习打卡提交后 |
| `<plugin 自定义>`（v1.7.0） | 任意 | 后端 | 后端插件 `api.events.emit("myplugin.x", {...})`（按归属用户过滤推送） |

> 事件只增不改：新版本可能追加事件，已有事件名与 payload 结构向后兼容。
>
> **前向兼容**：订阅宿主尚不存在的事件名不会报错——监听器只是永不触发。`on`/`emit` 接受
> 任意字符串事件名（未知名以 `unknown` 载荷传递），插件可放心订阅新版本事件，在旧宿主上自然降级。
>
> **后端事件隔离**：经 SSE 桥转发的后端事件**按归属用户过滤**——他人的事件不会推给你
> （`startup`/`shutdown` 等全局生命周期事件除外，广播给全部连接）。详见 §8.7。
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

> 💡 应用内置「插件」管理页（侧栏 🧩 插件，`/plugins`）已对本节方法提供可视化操作：
> 用户无需打开控制台即可按 URL 加载/移除插件、查看并切换已注册皮肤与小组件。
> 分发插件时可直接把 URL 交给用户到该页粘贴加载。

#### `plugins.load(url: string): Promise<void>`

加载外部插件脚本并**持久化**到 `localStorage('dt_plugins')`（刷新/重开自动按序加载）。
URL 必须 `http(s)://` 开头；脚本加载失败 reject。
**失败界定**：硬错误（404/拒绝连接等）立即 reject；**15 秒无响应按超时 reject**（v1.3.1）——
自动加载按序等待每个脚本 settle，超时保证一个挂起 URL 不会饿死其后的插件。

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

- **API 版本**：`1.6.0`（`api.version`，与前端 API 独立版本线）
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
- `register` 抛异常时，该插件在异常前已完成的注册（挂载的路由、订阅的事件、注册的
  工具）会被**逆序回滚**——`failed` 状态与真实运行态一致，失败插件的代码不会在
  宿主内继续存活（iter 247）。

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
| `api.parsers` | `ParsersNamespace` | 文件解析器注册（v1.3.0）：为私有/小众格式注册解析器，上传与解析自动委托（§8.4a） |
| `api.exporters` | `ExportersNamespace` | 导出格式注册（v1.4.0）：为私有/小众格式注册导出器，导出端点 format 校验与分发自动委托（§8.4b） |
| `api.llm` | `LlmNamespace` | 宿主 LLM 能力调用（v1.5.0）：插件复用用户已配置的供应商做 AI 功能，预算守卫+用量计量同宿主端点（§8.4c）；v1.6.0 起 `complete(provider_kind=)` 可分发到插件注册的新协议 Provider |
| `api.providers` | `ProvidersNamespace` | LLM Provider 扩展点（v1.6.0）：为内置仅支持 Anthropic 协议的 LLM 层注册新协议 Provider（OpenAI/Ollama/DeepSeek/Gemini 等），经 `api.llm.complete(provider_kind=)` 调用，预算守卫+计量同宿主（§8.4d） |

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

#### `api.parsers` —— 文件解析器注册（v1.3.0 扩展点）

为内置 `parse_material` **不支持的文件格式**（私有格式 / 小众格式，如 `.epub` 电子书、
`.xmind`/`.one` 笔记、企业私有格式）注册解析器——上传校验与解析链路自动识别并委托，
**无需改宿主代码即可"让后端支持新文件格式"**。

```python
api.parsers.register(
    extensions: list[str],   # 如 [".epub"]；自动规范化为小写带点
    handler: Callable,        # async 或 sync，签名 (path, parse_mode) -> (text, meta)
    *,
    label: str = "",          # 人类可读格式名（GET /materials/formats 展示用），可选
    magic: bytes | None = None,  # 上传 magic-bytes 校验签名（如 b"%PDF"），可选
    override: bool = False,   # True 则即便对内置格式也接管（高级用法，启动告警）
) -> None
```

**handler 契约**（与内置 `_parse_pdf`/`_parse_docx` 等**完全一致**，便于照搬内置解析器写法）：

```python
# async 或 sync 均可：async 直接 await；sync 在线程池执行（不阻塞事件循环）
async def parse_epub(path: pathlib.Path, parse_mode: str) -> tuple[str, dict]:
    text = epub_lib.extract_text(path)   # 你的解析逻辑
    return text, {"source_type": "epub", "parser": "myplugin", "pages": n}
```

- 返回 `(text, meta)`；`text` 为空 / 纯空白 -> 上游 `empty_content`；
  抛任何异常 -> 上游 `parse_failed`（消息经服务器路径清洗，不泄露绝对路径）。
- `parse_mode` 取值与内置一致（`"full"` 默认高质量 / `"fast"` 快速提取），**插件可忽略**。
- 宿主对插件解析结果施加与内置**相同的安全后处理**：截断至 `MAX_PARSED_CHARS`、
  空内容校验、`chunks` 分块——插件无需自行处理这些边界。

**内置优先（安全默认）**：对内置已支持的扩展名（`.pdf`/`.docx`/`.txt`/…）注册解析器
**默认不生效**（内置处理），避免误伤既有解析。确需替换内置时显式 `override=True`
（启动告警，属高级用法——替换内置解析器请充分测试）。

**上传层自动接纳**：插件注册的扩展名经上传校验（会话资料 `POST /materials/upload` 与
知识库 `POST /kb/documents/upload` 共用同一校验）自动放行；注册了 `magic` 则按其校验
文件内容与扩展名一致。客户端可经 `GET /materials/formats` 发现本实例接受的全部格式
（内置 + 插件），用于上传前格式提示。

> **可观测性**：注册的扩展名记录在插件状态（`GET /api/plugins` 行的 `parsers` 字段，
> 管理员可见）。

**完整示例**：EPUB 电子书解析插件

```python
# plugins/epub_parser/plugin.py
import zipfile
from pathlib import Path

PLUGIN_META = {"id": "epub", "name": "EPUB Parser", "version": "0.1.0", "min_api": "1.3"}

def register(api):
    def parse_epub(path: Path, parse_mode: str):  # sync handler（epub 是 zip，CPU 密集）
        with zipfile.ZipFile(path) as zf:          # zip-slip 防护由你的代码负责（§9.2）
            htmls = [zf.read(n) for n in zf.namelist() if n.endswith((".xhtml", ".html"))]
        text = b"\n".join(htmls).decode("utf-8", "replace")  # 简化：实际应剥 HTML 标签
        return text, {"source_type": "epub", "parser": "epub"}

    api.parsers.register([".epub"], parse_epub, label="EPUB 电子书", magic=b"PK")
```

重启后端 -> 上传 `.epub` 文件即被本插件解析为文本，注入下游学习方案生成。

#### `api.exporters` —— 导出格式注册（v1.4.0 扩展点，§8.4b）

为内置导出（`md`/`pdf`/`slides`/`ics`/`anki`）**不支持的格式**（私有格式 / 小众格式，
如 `.epub` 电子书、`.mobi`、企业归档格式、自定义卡片格式）注册导出器——
`POST /api/export/{plan_id}` 的 format 校验与分发自动识别并委托，
**无需改宿主代码即可"让后端支持新导出格式"**（与 `api.parsers` 对称的出口侧：
parsers 管"读进来"，exporters 管"导出去"）。

```python
api.exporters.register(
    format: str,              # 如 "epub"；自动规范化为小写，字符集 [a-z0-9_-]
    handler: Callable,        # async 或 sync，签名 (plan, ctx) -> dict
    *,
    label: str = "",          # 人类可读格式名（GET /export/formats 展示用），可选
    override: bool = False,   # True 则即便对内置格式也接管（高级用法，启动告警）
) -> None
```

**handler 契约**：

```python
# async 或 sync 均可：async 直接 await；sync 在线程池执行（不阻塞事件循环，
# 与内置 pdf/ics/anki 同步导出经 to_thread 同一口径）
async def render_epub(plan: dict, ctx: dict) -> dict:
    return {
        "filename": "plan.epub",          # 必填：建议下载文件名（宿主仅取 basename）
        "data": epub_bytes,               # content(str) 或 data(bytes) 必填其一
        "mime": "application/epub+zip",   # 可选；其余自定义键原样并入响应
    }
```

- 入参 `plan`：方案完整内容字典（`intent`/`schedule`/`quizzes` 等，与
  `GET /plans/{id}` 的 `content` 一致）；`ctx` = `{"plan_id", "user_id", "wrong_only"}`。
  `wrong_only` 是内置 anki 专属口径——仅 `override=True` 接管 anki 的插件会收到
  `True`；普通插件格式带 `wrong_only=true` 会被端点 400 拒绝（不静默忽略）。
- 返回 dict：**必含 `filename`**（str，宿主仅取 basename 防路径注入）；**必含
  `content`（str，文本产物随响应返回）或 `data`（bytes，二进制产物 base64 为
  `bytes_b64` 返回，上限 32MiB）其一**；其余键（如 `mime`/自定义元数据）原样并入
  响应——保留键 `file_url`/`format`/`filename`/`content`/`bytes_b64`/`data` 由宿主
  控制，插件不可覆盖。额外键须 JSON 可序列化。
- 抛任何异常 -> 上游 `export_failed`（400）；返回不合规（缺 filename/缺产物/类型错/
  超上限）同样 `export_failed`。**不静默降级为 md**——私有格式降级产物与请求不符。
- sync handler 在线程池执行——需要额外查库的插件请自行新建 DB 会话（**勿跨线程
  共享宿主请求会话**，SQLAlchemy Session 非线程安全）。

**内置优先（安全默认）**：对内置格式注册导出器**默认不生效**（内置处理），避免误伤
既有导出。确需替换内置时显式 `override=True`（启动告警，属高级用法——如用自有
PDF 引擎替换内置 weasyprint 路径，请充分测试）。

**端点层自动接纳**：`POST /api/export/{plan_id}` 的 `format` 字段自 v1.4.0 起为自由
字符串（原 `Literal` 枚举已移除——否则插件格式会在请求校验层被 422 拦截）；未知格式
显式 `unsupported_format`（400），不再静默降级。客户端可经 `GET /export/formats`
发现本实例可用的全部导出格式（内置 + 插件，含 `label`/`plugin_id`/`override`），
用于动态渲染导出菜单——替代前端/外部客户端据此与宿主格式能力保持同步。

> **可观测性**：注册的格式名记录在插件状态（`GET /api/plugins` 行的 `exporters`
> 字段，管理员可见）。插件导出走与内置相同的 12 次/分钟限流与方案归属守卫。

**完整示例**：EPUB 电子书导出插件

```python
# plugins/epub_exporter/plugin.py
PLUGIN_META = {"id": "epubexp", "name": "EPUB Exporter", "version": "0.1.0", "min_api": "1.4"}

def register(api):
    def render_epub(plan: dict, ctx: dict):  # sync renderer（打包 zip，CPU 密集）
        import io, zipfile
        buf = io.BytesIO()
        topic = (plan.get("intent") or {}).get("topic", "plan")
        with zipfile.ZipFile(buf, "w") as zf:  # 简化：实际 EPUB 需 mimetype/OPF/NCX
            zf.writestr("content.txt", f"# {topic}\n")
        return {
            "filename": f"plan-{ctx['plan_id']}.epub",
            "data": buf.getvalue(),
            "mime": "application/epub+zip",
        }

    api.exporters.register("epub", render_epub, label="EPUB 电子书")
```

重启后端 -> `POST /api/export/{plan_id}` 带 `{"format": "epub"}` 即由本插件渲染，
响应 `bytes_b64` 为 EPUB 二进制（前端/客户端 base64 解码后 Blob 下载）。

#### `api.llm` —— 宿主 LLM 能力调用（v1.5.0 扩展点，§8.4c）

插件做 AI 功能（自定义总结/批改/翻译/问答/私有语料加工等）**直接复用宿主已配置的
供应商**——用户无需为插件重复配 key。供应商按 `user_id` 三级回退解析（DB 供应商
档案 -> Key 凭证档案 -> `.env`，与宿主端点同一口径）；调用前经 `check_daily_budget`
**日预算守卫**（与生成/幻灯片端点同规），用量计入 `api_usage`（用量页/日预算统计
口径完整）——插件的 LLM 花费对用户完全可见、受预算约束，不会成为"暗花费"。

```python
api.llm.is_configured(user_id: int) -> bool   # 该用户是否已配置任一供应商（降级判断用）

resp = await api.llm.complete(
    user_id: int,          # 以谁的供应商配置调用（通常来自 api.deps.get_current_user）
    prompt: str,           # 用户消息（非空）
    *,
    system: str = "",      # 系统提示（可选）
    max_tokens: int = 2048,          # 收敛到 [1, 8192]（防失控调用；日预算守卫是底线）
    provider_kind: str | None = None # v1.6.0：指定时改为分发到该 kind 的插件 Provider（§8.4d），
                                     #   凭证由插件自理；预算守卫+计量与默认路径同口径。None -> 用户已配置的供应商
) -> dict                  # {"content", "model", "stop_reason", "usage": {...}}（只增不改）
```

**错误映射**（均为 `ApiError`——插件路由内直接上抛即得宿主统一错误格式）：

| 场景 | code | HTTP |
|---|---|---|
| 该用户未配置任何供应商 | `no_llm_provider` | 503 |
| 日预算耗尽 | `budget_exceeded` | 402 |
| 瞬态上游错误（限流/5xx/连接失败） | `server_busy` | 503 |
| 其他错误（如请求被上游拒绝） | `bad_gateway` | 502 |

- v1 为**非流式单次补全**；多轮对话/工具编排请自行用返回文本组织（或经 `api.tools`
  注册工具融入 Agent 管线）。瞬态错误插件可自行退避重试。
- `is_configured(user_id)` 与 `complete` 的解析口径严格一致——插件据此在"无 key"
  时优雅降级（如路由返回配置引导），而非调用时才撞 503。

**完整示例**：学习卡片批注插件（为插件页面提供 AI 批注）

```python
# plugins/annotator/plugin.py
from fastapi import APIRouter, Depends

PLUGIN_META = {"id": "annotator", "name": "AI Annotator", "version": "0.1.0", "min_api": "1.5"}

def register(api):
    router = APIRouter()

    @router.post("/annotate")
    async def annotate(body: dict, user=Depends(api.deps.get_current_user)):
        if not api.llm.is_configured(user.id):
            return {"configured": False, "hint": "请先在设置页配置 LLM 供应商"}
        resp = await api.llm.complete(
            user.id,
            f"为以下学习笔记写一句鼓励性批注：\n{body.get('text', '')[:2000]}",
            system="你是温和的学习助教，批注不超过 50 字。",
            max_tokens=256,
        )
        return {"configured": True, "note": resp["content"], "model": resp["model"]}

    api.routes.mount(router)  # -> POST /api/plugins/annotator/annotate（默认带宿主认证）
```

#### `api.providers` —— LLM Provider 扩展点（v1.6.0 扩展点，§8.4d）

为内置**仅支持 Anthropic 协议**的 LLM 层注册**新协议 Provider**——让后端经插件 API 即可
"支持新的 LLM Provider"（OpenAI `/v1/chat/completions`、Ollama、DeepSeek 原生、Gemini 等）。
注册后经 `api.llm.complete(..., provider_kind=...)` 调用，**带宿主日预算守卫 + `api_usage`
计量**（与宿主端点同口径）——非 Anthropic 协议不再须插件 DIY `httpx` 绕开预算/计量（"暗花费"）。

```python
api.providers.register(
    kind: str,              # 协议名（如 "openai"、"ollama"）；规范化为小写，字符集 [a-z0-9][a-z0-9_-]{0,31}
                            #   'anthropic' 为内置保留，注册拒绝（ValueError）
    handler: Callable,      # 补全函数，async 或 sync，签名见下
    *,
    default_model: str = "",# 该 kind 默认模型名（api.llm 未显式传 model 时用），可选
    label: str = "",        # 人类可读协议名（GET /providers/kinds 展示用），可选
) -> None
api.providers.kinds() -> list[str]   # 本插件已注册的协议名（调试/状态展示用）
```

**handler 契约**（async 或 sync，签名相同；async 直接 await，sync 在线程池执行不阻塞事件循环）：

```python
async def chat_openai(
    system: str, user: str, *,
    model: str, max_tokens: int,
    api_key: str, base_url: str,   # iter 313：凭证（见下方"凭证来源"）
) -> dict:
    # api_key/base_url 非空时用之（来自用户供应商档案）；空串则回退插件自有凭证
    # （api.data_dir / 环境变量 / 自有设置路由）。
    client = openai.OpenAI(api_key=api_key or my_own_key, base_url=base_url or None)
    resp = client.chat.completions.create(model=model, max_tokens=max_tokens,
                                          messages=([{"role":"system","content":system}] if system else [])
                                                 + [{"role":"user","content":user}])
    return {
        "content": resp.choices[0].message.content,   # 必填：补全文本
        "model": resp.model,                          # 必填：实际所用模型名
        "stop_reason": resp.choices[0].finish_reason, # 可选
        "usage": {                                    # 必填（成本计量用）
            "input_tokens": resp.usage.prompt_tokens,        # 必填
            "output_tokens": resp.usage.completion_tokens,   # 必填
            "cache_read_input_tokens": 0,                    # 可选，缺省 0
            "cache_creation_input_tokens": 0,                # 可选，缺省 0
        },
    }
```

> **凭证来源**（`api_key`/`base_url`，iter 313 profile 集成）：**经用户供应商档案调用**
> （档案 `kind` 设为该协议、`get_provider` 分发、或 `/providers/{id}/test`）时，`api_key`/`base_url`
> 来自档案——用户在设置页统一配 key + 选协议，无需为插件另配；**经 `api.llm.complete(provider_kind=)`
> 调用**（无档案上下文）时传空串，插件 handler 据此回退自有凭证。即：非空用档案、空则插件自理。

**异常约定**（插件无需 import 宿主内部）：handler 抛任何异常 -> 宿主分类——在异常实例上设
`transient = True`（或抛名为 `ConnectError`/`TimeoutException`/`RateLimitError`/`APIConnectionError`
等常见网络瞬态类型）标记瞬态，宿主据此退避重试（`api.llm` -> `server_busy` 503）；其余非瞬态
（含 4xx/返回契约不合规）-> `bad_gateway` 502。返回 dict 缺必填字段或类型错 -> `bad_gateway`。

> **安全/稳定要点**：① 纯增量——`provider_kind=None`（缺省）时 `api.llm.complete` 行为与 v1.5.0
> 字节一致（走用户已配置的 Anthropic 供应商），无插件注册 Provider 时全链路不变；② 花费治理
> 与宿主同规（日预算守卫 + `api_usage` 计量双闸），插件 Provider 不能绕过日预算；③ `max_tokens`
> 收敛 [1, 8192] 防失控天价调用；④ 凭证不入宿主——插件自管其新协议 key（存 `api.data_dir`，
> 不硬编码进源码）；⑤ 内置 `'anthropic'` 保留名拒绝注册，防与内置协议冲突。

**完整示例**：OpenAI 兼容 Provider 插件（注册新协议 + 暴露配置路由 + 经 api.llm 调用）

```python
# plugins/openai_provider/plugin.py
import json
from pathlib import Path
from fastapi import APIRouter, Depends
import httpx

PLUGIN_META = {"id": "openaiprovider", "name": "OpenAI Provider", "version": "0.1.0", "min_api": "1.6"}

_cfg_file = lambda api: Path(api.data_dir) / "config.json"

def register(api):
    def _load_cfg():
        f = _cfg_file(api)
        return json.loads(f.read_text("utf-8")) if f.exists() else {}

    async def chat_openai(system, user, *, model, max_tokens, api_key="", base_url=""):
        # 凭证优先用档案传入（profile 集成路径，用户在设置页配 key）；空则回退插件自有配置
        cfg = _load_cfg()  # {base_url, api_key, model} —— 插件路由自配的回退凭证
        key = api_key or cfg.get("api_key")
        url = (base_url or cfg.get("base_url") or "https://api.openai.com").rstrip("/")
        if not key:
            raise RuntimeError("OpenAI provider 未配置 api_key（档案或插件配置二选一）")
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                f"{url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model or cfg.get("model", "gpt-4o"),
                      "max_tokens": max_tokens,
                      "messages": ([{"role": "system", "content": system}] if system else [])
                                 + [{"role": "user", "content": user}]},
            )
            r.raise_for_status()  # 4xx/5xx -> 非 transient -> bad_gateway（超时/连接由 httpx 抛，宿主按名判 transient）
            data = r.json()
        return {
            "content": data["choices"][0]["message"]["content"],
            "model": data["model"],
            "stop_reason": data["choices"][0].get("finish_reason"),
            "usage": {
                "input_tokens": data.get("usage", {}).get("prompt_tokens", 0),
                "output_tokens": data.get("usage", {}).get("completion_tokens", 0),
            },
        }

    api.providers.register("openai", chat_openai, default_model="gpt-4o", label="OpenAI 兼容")

    router = APIRouter()
    @router.post("/generate")
    async def generate(body: dict, user=Depends(api.deps.get_current_user)):
        # 经 api.llm 调用本插件注册的 openai 协议——日预算守卫 + api_usage 计量同宿主
        resp = await api.llm.complete(
            user.id, body.get("prompt", ""), system="你是助教",
            max_tokens=512, provider_kind="openai",
        )
        return {"text": resp["content"], "model": resp["model"]}

    @router.put("/config")
    def set_config(body: dict, user=Depends(api.deps.require_admin)):
        # 凭证落插件私有数据目录（不入宿主 DB；仅管理员可配）
        _cfg_file(api).write_text(json.dumps(body), "utf-8")
        return {"ok": True}

    api.routes.mount(router)
```

装载后：`GET /api/providers/kinds` 列出 `openai`（§发现端点）；插件路由与 `api.llm.complete(..., provider_kind="openai")` 即可调用 OpenAI，花费受日预算约束、记入用量页。

> **接入宿主供应商档案（iter 313 profile 集成，已落地）**：插件 Provider 不仅可经
> `api.llm.complete(provider_kind=...)` 显式调用，还可**接入用户供应商档案**——档案 `kind` 字段
> 设为该协议（如 `openai`）后，`get_provider(user_id)` 自动分发到对应 `PluginProvider`，**方案生成主线**
> 与所有宿主端点（生成/重生成/slides/测验等）即用新协议，档案 `api_key`/`base_url` 作凭证传入 handler。
> 用户在设置页统一配 key + 选协议（`GET /api/providers/kinds` 发现可用协议），无需为插件另配。
>
> **供应商档案 `kind` 字段**：`POST/PUT /api/providers` 接受 `kind`（规范化小写；缺省/空串 → `'anthropic'`）。
> 档案 `kind` 为插件协议时：`get_provider(user_id)` 返回 `PluginProvider`（未知/未注册 `kind` 抛
> `RuntimeError`，不静默回退）；`POST /api/providers/{id}/test` 同口径分发（未注册 `kind` 返回
> `ok:false` 明确错误）。`GET /api/providers` 回显 `kind`。**既有档案 `kind` 为空 → `'anthropic'`，
> `get_provider` 行为与集成前字节一致（零回归）。**

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
  "api_version": "1.6.0",
  "plugins": [
    {
      "id": "hello", "name": "Hello", "version": "0.1.0",
      "state": "loaded", "error": "", "file": ".../plugins/hello.py",
      "routes": ["/api/plugins/hello"],
      "events": ["plan_created"],
      "tools": [],
      "parsers": [],
      "exporters": []
    }
  ],
  "subscribed_events": ["plan_created", "shutdown", "startup"]
}
```

`state: "failed"` 时 `error` 给出失败原因（import/register/id 非法/重复/min_api 不兼容）。

本端点同时是内置「插件」管理页（`/plugins`，后端插件区块）的数据源——
管理员可在页面直接查看各插件装载状态与错误，无需手工调用。

### 8.7 实时事件流：`GET /api/events/stream`（SSE）

把后端事件总线（§8.5 核心事件 + 插件自定义事件）**实时推送**给已认证客户端——
Server-Sent Events。前端插件 / 外部替代前端 / CLI 据此构建实时 UI（学习进度更新、
方案生成完成、测验提交、学习打卡等），**无需轮询**。这是"用 API 完全替换前端"的
实时性使能项（一个完整替代前端必须能感知后端数据变化）。

**鉴权与连接**：与宿主端点同一机制（JWT / `AUTH_TOKEN` / 开放本机模式）。SSE 经
`fetch` + `ReadableStream` 携带 `Authorization` 头（浏览器原生 `EventSource` 不能带
自定义头，故客户端需用 fetch 流式读，如本项目前端 [sse.ts](frontend/src/lib/sse.ts)）。

**用户隔离（P4 安全默认）**：服务端按归属用户过滤事件——

- payload 带 `user_id`（如 `progress_updated`/`quiz_submitted`/`learning_log_added`）-> 仅转发给该用户；
- payload 带 `session_id`（如 `plan_created`/`materials_completed`）-> 服务端解析 `session.user_id` 后仅转发给归属用户；
- `startup`/`shutdown` 等全局生命周期事件 -> 广播给全部连接；
- 既无 `user_id`/`session_id` 又非全局的事件（如某些插件自定义事件）-> **丢弃**（safe-by-default，防跨用户泄露）。

**帧格式**（标准 SSE）：

```text
event: progress_updated
data: {"user_id": 1, "plan_id": 7, "node_id": "n3", "status": "done", "mastery": 0.8}

event: plan_created
data: {"plan_id": 12, "session_id": 3, "version": 1, "topic": "React"}

: ping
```

- 每个事件一个 `event:` 行（事件名）+ `data:` 行（JSON payload），空行分隔。
- 空闲时每 15s 发一行 `: ping` 心跳注释（防代理/浏览器缓冲挂起连接）。
- 客户端断开 -> 服务端立即退订、释放资源（无连接/监听泄漏）。

**事件名与 payload**：见 §8.5 核心事件清单（只增不改；新版本可能追加事件与键）。
插件自定义事件（`api.events.emit`）若 payload 含 `user_id`/`session_id` 同样按归属
过滤转发；不含则被安全丢弃。

**客户端最小示例**（fetch 流式，同前端 sse.ts 范式）：

```js
// 经 Authorization 头鉴权（EventSource 做不到，故用 fetch）
const res = await fetch('/api/events/stream', { headers: { Authorization: `Bearer ${token}` } })
const reader = res.body.getReader()
const dec = new TextDecoder()
let buf = ''
for (;;) {
  const { value, done } = await reader.read()
  if (done) break
  buf += dec.decode(value)
  // 按 SSE 规范逐事件解析（空行分隔；: 开头是心跳注释，跳过）
  // event: <name>  /  data: <json>  -> 触发对应处理
}
```

> **多进程部署**：事件总线为进程内内存广播（同 `ratelimit`、生成 SSE）——多进程
> 部署时事件仅在本进程内可见，需经 sticky session 或共享 pubsub（Redis 等）跨进程
> 分发（接口已按可替换设计，未来可扩展）。

### 8.8 完整示例

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
| 后端 | 1.6.0（additive 档案字段） | 2026-07-22 | 供应商档案 `kind` 字段 + `get_provider` 分发 + `/test` 分发（iter 313 profile 集成）：档案 `kind` 设为插件协议（`api.providers` 注册的）时，`get_provider` 返回 `PluginProvider`——**方案生成主线与全部宿主端点即用新协议**（OpenAI/Ollama 等），档案 `api_key`/`base_url` 作凭证传入 handler（统一管 key，无需为插件另配）；alembic 迁移加 `kind` 列 + `_REVISION_PROBES` 探测；handler 契约加 `api_key`/`base_url` 参（profile 非空用之、`provider_kind` 路径空串插件自理）；既有档案 kind 空 → anthropic，行为字节不变（零回归）；不涉 `register(api)` 门面，无版本号变动 |
| 后端 | 1.6.0 | 2026-07-22 | 新增 `api.providers` LLM Provider 扩展点（插件注册新协议 Provider——OpenAI/Ollama/DeepSeek/Gemini 等，`register/kinds`）+ `api.llm.complete(provider_kind=)` 分发 + 发现端点 `GET /providers/kinds`：内置仅支持 Anthropic 协议的 LLM 层经插件 API 即可"支持新的 LLM Provider"，日预算守卫 + `api_usage` 计量同宿主（非 Anthropic 协议不再须 DIY httpx 绕开预算/计量"暗花费"）；PluginProvider 包装器（async/sync handler + dict 契约校验 + 瞬态错误分类）；凭证插件自理（v1 非侵入，profile 集成留下一轮）；插件状态行新增 `providers` 字段 |
| 后端 | 1.5.0（additive 端点） | 2026-07-22 | 新增实时事件流 `GET /api/events/stream`（SSE）：后端事件总线实时推送给已认证客户端，按归属用户过滤（user_id 直归属 / session_id 解析 / 全局事件广播 / 不可归属丢弃），15s 心跳保活，断开即退订无泄漏——"用 API 完全替换前端"的实时性使能项（不涉及 `register(api)` 门面，无版本号变动）；事件总线新增 `subscribe_all` 全局 sink（供 SSE 端点等监听全部事件） |
| 后端 | 1.5.0 | 2026-07-22 | 新增 `api.llm` 宿主 LLM 能力调用（`is_configured`/`complete`）：插件复用用户已配置的供应商做 AI 功能（无需重复配 key），日预算守卫 + `api_usage` 计量与宿主端点同口径，错误映射 no_llm_provider/budget_exceeded/server_busy/bad_gateway |
| 前端 | 1.7.0 | 2026-07-22 | `DeepTutor.events` 后端事件桥：宿主在登录态自动连接 `GET /api/events/stream`（SSE，§8.7），把后端事件总线事件（`plan_created`/`progress_updated`/`quiz_submitted`/`materials_completed`/`learning_log_added`/全局生命周期 + 插件自定义事件）实时转发为 `events`——插件 `events.on(...)` 即实时收到，免轮询（"用 API 完全替换前端"的实时性使能项）；`on`/`emit` 重载放宽接受任意字符串事件名（已知事件类型化、未知名 `unknown` 前向兼容）；后端事件按归属用户过滤（隔离）；新增 `EventsBridge` 组件（鉴权区内挂载，指数退避重连，登出 abort）；同步收口提交 iter 310 后端 SSE（`subscribe_all`/`events.py`） |
| 前端 | 1.6.0 | 2026-07-22 | 新增 `overlay` 命名空间（`register/unregister/list/layers/subscribe`）：插件注册应用根级覆盖层（`background`/`content`/`top` 三层 z-index 分层），脱离页面、覆盖整个视口绘制——"用 API 完全改造前端"使能项（WebGL 背景特效 / 可拖放浮窗 / 2D 物理叠层 / 全屏 3D 小游戏）；pointer-events 默认穿透不阻塞 UI（交互 opt-in `auto`）+ 单 overlay 错误边界隔离 + 全路由渲染（组件内可用路由/取数 hooks）+ `/plugins` 可观测 |
| 前端 | 1.5.0 | 2026-07-22 | 新增 `router` 命名空间（`navigate/path/href/subscribe`）：插件页面间 SPA 导航（不整页刷新）+ 路由变化响应式订阅——页面 API 的配套能力，插件多页应用闭环；查询参数读取走原生 `window.location.search`（文档明示） |
| 前端 | 1.4.0 | 2026-07-22 | 新增 `pages` 命名空间（`DeepTutor.pages.register/get/unregister/list/navItems/subscribe`）：插件注册完整路由页（`/p/` 命名空间隔离，零内置碰撞）+ 可选侧栏导航——"用 API 完全替换前端"核心使能项；单页错误边界隔离 + 未命中兜底 |
| 后端 | 1.4.0 | 2026-07-22 | 新增 `api.exporters` 导出格式扩展点（插件为私有/小众格式注册导出器；`POST /export/{plan_id}` format 解除 Literal 枚举改显式校验，`GET /export/formats` 格式发现端点；插件状态行新增 `exporters` 字段）——与 `api.parsers` 对称的出口侧 |
| 后端 | 1.3.0 | 2026-07-22 | 新增 `api.parsers` 文件解析扩展点（插件为私有/小众格式注册解析器；上传校验自动接纳插件扩展名 + 可选 magic；`GET /materials/formats` 格式发现端点；插件状态行新增 `parsers` 字段） |
| 后端 | 1.2.0 | 2026-07-19 | 新增 `learning_log_added` 事件（学习打卡提交后广播，用户行为事件目录补齐） |
| 前端 | 1.3.1 | 2026-07-19 | 修复：插件脚本 15 秒无响应按超时 reject（此前永不 settle，自动加载时一个挂起 URL 会饿死其后全部持久化插件） |
| 前端 | 1.3.0 | 2026-07-19 | 新增插槽 `plan.detail`（方案详情页头部下方宫格；组件内可 `useParams()` 取 planId 做方案级面板） |
| 后端 | 1.1.0 | 2026-07-19 | 新增 `materials_completed` 事件（材料生成完成；SSE/同步两路径均广播，幂等重渲染不广播） |
| 后端 | 1.0.0 | 2026-07-19 | 首个后端插件 API：`plugins/` 目录自动发现装载（单文件/包插件、`_` 前缀停用）、`api` 门面（routes/events/tools/settings/deps/data_dir/log）、核心事件（startup/shutdown/plan_created/progress_updated/quiz_submitted）、路由命名空间 `/api/plugins/<id>/`（默认认证）、状态端点 `GET /api/plugins`、插件工具汇入 `default_registry()` |
| 前端 | 1.2.0 | 2026-07-19 | 新增 `api.request`（全方法 REST 代理）、`widgets.slots()`（插槽能力发现）；小组件渲染异常改为单组件级错误边界隔离（此前会拖垮整个应用）；widgets/skins 订阅回调异常隔离（与 events 语义一致）；文档补事件前向兼容说明 |
| 前端 | 1.1.0 | 2026-07-19 | 新增 `taskchange` 事件（任务相位迁移；配合 `api.get('/sessions')` 查询任务全量状态） |
| 前端 | 1.0.0 | 2026-07-19 | 首个预留版本：`widgets`（插槽 `home.dashboard` / `sidebar.footer`）、`skins`（令牌 `--dt-card-radius` / `--dt-font-sans`）、`events`（`skinchange`）、`api.get`、`plugins.load/unload/list` |

// Deep-tutor 前端美化插件 API（预留接口，稳定契约见仓库根目录 API.md）。
//
// 第三方插件通过全局 `window.DeepTutor` 访问本模块，可：
//   - widgets：向命名插槽（slot）注册小组件（React 组件，可用 DeepTutor.React 免打包 React）
//   - pages：注册完整路由页（/p/ 命名空间）+ 可选侧栏导航——"用 API 完全替换前端"使能项（v1.4.0）
//   - overlay：注册应用根级覆盖层（WebGL 背景/浮窗/物理叠层/3D 接管）——"用 API 完全改造前端"使能项（v1.6.0）
//   - skins：注册/应用外观皮肤（CSS 变量令牌集，亮/暗双态，选择持久化）
//   - api：以当前登录态调用后端 REST（get 便捷方法 + request 全方法）
//   - events：订阅 API 事件
//   - plugins：加载/卸载外部插件脚本（URL 持久化，刷新后自动加载）
//
// 兼容性承诺：API_VERSION 语义化版本；minor/patch 向后兼容，major 变更会在 API.md 记录迁移说明。
import type { ComponentType } from 'react'
import { apiFetch } from './api'
import { useUIStore } from '../store/ui'

export const API_VERSION = '1.6.0'

// ---------------------------------------------------------------------------
// 小组件（widgets）
// ---------------------------------------------------------------------------

/** 命名插槽：页面中允许挂接小组件的位置（新增插槽只在 minor 版本追加，不改名/删除） */
export const WIDGET_SLOTS = ['home.dashboard', 'sidebar.footer', 'plan.detail'] as const
export type WidgetSlot = (typeof WIDGET_SLOTS)[number]

export interface WidgetDef {
  /** 全局唯一 id（建议带插件名前缀，如 'my-plugin.clock'）；重复注册同 id 覆盖 */
  id: string
  /** 目标插槽 */
  slot: WidgetSlot
  /** 渲染组件；无 props。数据获取用组件内 hooks（TanStack Query 上下文可用） */
  component: ComponentType
  /** 排序权重，小的在前；缺省 100。内置组件 0-99 保留 */
  order?: number
}

const widgetRegistry = new Map<string, WidgetDef>()
let registryVersion = 0
const widgetListeners = new Set<() => void>()

function notifyWidgets() {
  registryVersion += 1
  // 单个订阅回调异常不影响其他订阅者与应用（与 events.emit 同一隔离语义）
  widgetListeners.forEach((fn) => {
    try {
      fn()
    } catch (e) {
      console.error('[DeepTutor] widgets 订阅回调异常', e)
    }
  })
}

const widgetsApi = {
  register(def: WidgetDef): void {
    if (!def || typeof def.id !== 'string' || !def.id) throw new Error('[DeepTutor] widget.id 必填')
    if (!WIDGET_SLOTS.includes(def.slot)) throw new Error(`[DeepTutor] 未知插槽: ${String(def.slot)}`)
    if (typeof def.component !== 'function') throw new Error('[DeepTutor] widget.component 必须是组件')
    widgetRegistry.set(def.id, { order: 100, ...def })
    notifyWidgets()
  },
  /** 幂等；返回是否确实移除了一个组件 */
  unregister(id: string): boolean {
    const ok = widgetRegistry.delete(id)
    if (ok) notifyWidgets()
    return ok
  },
  /** 某插槽当前全部组件（按 order 升序，同 order 按注册顺序） */
  list(slot: WidgetSlot): WidgetDef[] {
    return [...widgetRegistry.values()]
      .filter((w) => w.slot === slot)
      .sort((a, b) => (a.order ?? 100) - (b.order ?? 100))
  },
  /** 订阅注册表变化（React useSyncExternalStore 用）；返回退订函数 */
  subscribe(fn: () => void): () => void {
    widgetListeners.add(fn)
    return () => widgetListeners.delete(fn)
  },
  /** v1.2.0：当前宿主支持的全部插槽名（只读）。插件可据此做能力发现，
   *  在旧宿主上降级（跳过新插槽），在新宿主上自动可用——无需解析版本号。 */
  slots(): readonly WidgetSlot[] {
    return WIDGET_SLOTS
  },
  getVersion(): number {
    return registryVersion
  },
}

// ---------------------------------------------------------------------------
// 页面（pages）：注册完整路由页 + 可选侧栏导航（v1.4.0）——"用 API 完全替换前端"使能项
// ---------------------------------------------------------------------------

/** 插件页面路径命名空间前缀。所有插件页面挂载在 /p/ 下，与内置路由零碰撞（防覆盖既有页面） */
export const PAGES_PREFIX = '/p/'

export interface PageNavDef {
  /** 侧栏导航显示名 */
  label: string
  /** 导航图标（emoji/字符），可选；缺省 🧩 */
  icon?: string
  /** 排序权重，小的在前；缺省 100 */
  order?: number
}

export interface PageDef {
  /** 页面路径，相对 /p/（如 'pomodoro' 或多级 'pomodoro/stats'）；唯一，重复注册同 path 覆盖更新 */
  path: string
  /** 渲染组件；无 props。数据获取用组件内 hooks（TanStack Query / React Router 上下文可用） */
  component: ComponentType
  /** 页面标题（document.title），可选 */
  title?: string
  /** 侧栏导航项；缺省则页面可达但不出现在导航（深链/二级页用） */
  nav?: PageNavDef
}

const pageRegistry = new Map<string, PageDef>()
let pageRegistryVersion = 0
const pageListeners = new Set<() => void>()

function notifyPages() {
  pageRegistryVersion += 1
  pageListeners.forEach((fn) => {
    try {
      fn()
    } catch (e) {
      console.error('[DeepTutor] pages 订阅回调异常', e)
    }
  })
}

/** 规范化页面路径：去首尾斜杠（'pomodoro/' / '/pomodoro' -> 'pomodoro'） */
function normalizePagePath(path: string): string {
  return path.replace(/^\/+|\/+$/g, '')
}

/** 路径合法性：非空、无 '..'（防越出命名空间）、段内字母数字-_ 段间 /（多级如 'a/b'） */
function isValidPagePath(p: string): boolean {
  if (!p || p.includes('..')) return false
  return /^[a-z0-9][a-z0-9\-_/]*$/i.test(p)
}

const pagesApi = {
  register(def: PageDef): void {
    if (!def || typeof def.path !== 'string') throw new Error('[DeepTutor] page.path 必填')
    const path = normalizePagePath(def.path)
    if (!isValidPagePath(path)) {
      throw new Error(`[DeepTutor] 非法页面路径: ${def.path}（仅字母数字-_;多级用 /；不可含 ..）`)
    }
    if (typeof def.component !== 'function') throw new Error('[DeepTutor] page.component 必须是组件')
    if (def.nav && typeof def.nav.label !== 'string') throw new Error('[DeepTutor] page.nav.label 必填')
    pageRegistry.set(path, { ...def, path })
    notifyPages()
  },
  /** 幂等；返回是否确实移除了一个页面 */
  unregister(path: string): boolean {
    const ok = pageRegistry.delete(normalizePagePath(path))
    if (ok) notifyPages()
    return ok
  },
  /** 某 path 的页面定义（宿主路由匹配用）；无则 undefined */
  get(path: string): PageDef | undefined {
    return pageRegistry.get(normalizePagePath(path))
  },
  /** 全部页面（按 path 升序），含无 nav 的。主要供调试 */
  list(): PageDef[] {
    return [...pageRegistry.values()].sort((a, b) => a.path.localeCompare(b.path))
  },
  /** 带导航项的页面（按 nav.order 升序，同 order 按注册顺序），供侧栏渲染 */
  navItems(): PageDef[] {
    return [...pageRegistry.values()]
      .filter((p) => p.nav)
      .sort((a, b) => (a.nav!.order ?? 100) - (b.nav!.order ?? 100))
  },
  /** 订阅注册表变化（React useSyncExternalStore 用）；返回退订函数 */
  subscribe(fn: () => void): () => void {
    pageListeners.add(fn)
    return () => pageListeners.delete(fn)
  },
  getVersion(): number {
    return pageRegistryVersion
  },
}

// ---------------------------------------------------------------------------
// 路由/导航（router）：SPA 导航 + 路由变化订阅（v1.5.0）——插件多页应用的必需能力
// ---------------------------------------------------------------------------
//
// 为什么需要：纯 JS 插件拿不到 react-router 实例（无构建链），不暴露的话插件页之间
// 只能 location.href 整页跳转（丢 SPA 状态、白屏闪烁），做不出"完整的另一个前端"。
// 本命名空间以门面形式暴露最小路由面（语义由宿主控制，不泄露 react-router 版本细节）：
// - navigate：SPA 导航（React Router navigate 包装，bridge 未挂载时降级 location.assign）
// - path/href：当前路径快照（配合 subscribe 可用于 useSyncExternalStore 响应式渲染）
// - subscribe：路由变化订阅（React Router 不对 push 发 popstate，插件无法自行可靠监听）
// 查询参数读取无需门面：插件与宿主同窗口，window.location.search + URLSearchParams 即可。

type RouterNavigate = (to: string, opts?: { replace?: boolean }) => void

let routerNavigate: RouterNavigate | null = null // RouterBridge 挂载后注入（TaskEngineBridge 同范式）
let routerHref = `${window.location.pathname}${window.location.search}${window.location.hash}`
const routerListeners = new Set<() => void>()

function notifyRouter() {
  routerListeners.forEach((fn) => {
    try {
      fn()
    } catch (e) {
      console.error('[DeepTutor] router 订阅回调异常', e)
    }
  })
}

/** 内部使用：RouterBridge 注入 navigate 并对齐当前路径（应用启动一次） */
export function bindRouter(navigate: RouterNavigate, pathname: string, search: string, hash: string): void {
  routerNavigate = navigate
  const href = `${pathname}${search}${hash}`
  if (href !== routerHref) {
    routerHref = href
    notifyRouter()
  }
}

/** 内部使用：RouterBridge 在路由变化时同步（pathname/search/hash 任一变化） */
export function setRouterLocation(pathname: string, search: string, hash: string): void {
  const href = `${pathname}${search}${hash}`
  if (href === routerHref) return
  routerHref = href
  notifyRouter()
}

const routerApi = {
  /** SPA 导航（不整页刷新）。to 须以 / 开头（站内路径，如 '/p/reader?id=3'）；
   *  opts.replace=true 替换当前历史条目而非 push（重定向语义）。
   *  bridge 未挂载的极端场景（如登录页脚本上下文）降级为 location.assign 整页跳转。 */
  navigate(to: string, opts?: { replace?: boolean }): void {
    if (typeof to !== 'string' || !to.startsWith('/')) {
      throw new Error('[DeepTutor] router.navigate 的路径须以 / 开头（站内路径）')
    }
    if (routerNavigate) routerNavigate(to, opts)
    else window.location.assign(to)
  },
  /** 当前 pathname（如 '/p/reader'）；等价 window.location.pathname，供响应式快照用 */
  path(): string {
    return routerHref.split(/[?#]/, 1)[0] || '/'
  },
  /** 当前 pathname+search+hash（如 '/p/reader?id=3'）；响应式查询参数场景的快照 */
  href(): string {
    return routerHref
  },
  /** 订阅路由变化（React useSyncExternalStore 用）；返回退订函数 */
  subscribe(fn: () => void): () => void {
    routerListeners.add(fn)
    return () => routerListeners.delete(fn)
  },
}

// ---------------------------------------------------------------------------
// 全局覆盖层（overlay）：应用根级渲染层（v1.6.0）——"用 API 完全改造前端"使能项
// ---------------------------------------------------------------------------
//
// 为什么需要：widgets 只能往既有页面固定插槽注入卡片，pages 只能在 /p/ 下渲染整页——
// 两者都无法"在整个应用之上"绘制内容。要做 WebGL 全屏背景特效 / 可自由拖放的浮窗 /
// 2D 物理碰撞叠层 / 3D 小游戏接管整屏，都需要一个**脱离页面、覆盖整个视口**的渲染层。
// 本命名空间把"应用根级 React 渲染"纳入受支持的插件门面（错误边界隔离 + 生命周期清理 +
// pointer-events 控制），而非让插件用裸 DOM 硬塞 document.body（绕过 React、无清理、
// 不受主题/错误边界保护，升级即碎）。
//
// 三层（z-index 分层，互不遮挡应用正常使用）：
//   background — 应用内容之后（z-index:-1，仅透过应用透明区域可见，如页边/留白）；WebGL 背景特效
//   content    — 应用内容之上、模态/Toast 之下（z-index:45）；浮窗小组件、HUD、画布叠层
//   top        — 万物之上（z-index:9999，盖过模态/Toast）；全屏 3D 游戏、彩纸、接管式特效

/** 覆盖层分层（新增分层只在 minor 版本追加，不改名/删除） */
export const OVERLAY_LAYERS = ['background', 'content', 'top'] as const
export type OverlayLayer = (typeof OVERLAY_LAYERS)[number]

export interface OverlayDef {
  /** 全局唯一 id（建议带插件名前缀，如 'my-plugin.snow'）；重复注册同 id 覆盖 */
  id: string
  /** 渲染分层，决定 z-index 与是否可交互（见上方分层说明） */
  layer: OverlayLayer
  /** 渲染组件；无 props。组件内可用 React hooks、TanStack Query、React Router（useLocation 等） */
  component: ComponentType
  /** 层容器是否拦截鼠标/触摸。缺省 'none'（点击穿透到下层应用，不阻塞 UI）；
   *  需交互的浮窗/3D 游戏设 'auto'（届时该 overlay 区域会捕获指针事件） */
  pointerEvents?: 'none' | 'auto'
  /** 同层内绘制顺序权重，小的在前（后绘制、视觉靠上）；缺省 100。内置保留 0-99 */
  order?: number
}

const overlayRegistry = new Map<string, OverlayDef>()
let overlayRegistryVersion = 0
const overlayListeners = new Set<() => void>()

function notifyOverlays() {
  overlayRegistryVersion += 1
  overlayListeners.forEach((fn) => {
    try {
      fn()
    } catch (e) {
      console.error('[DeepTutor] overlay 订阅回调异常', e)
    }
  })
}

const overlaysApi = {
  register(def: OverlayDef): void {
    if (!def || typeof def.id !== 'string' || !def.id) throw new Error('[DeepTutor] overlay.id 必填')
    if (!OVERLAY_LAYERS.includes(def.layer)) throw new Error(`[DeepTutor] 未知 overlay 分层: ${String(def.layer)}`)
    if (typeof def.component !== 'function') throw new Error('[DeepTutor] overlay.component 必须是组件')
    overlayRegistry.set(def.id, { pointerEvents: 'none', order: 100, ...def })
    notifyOverlays()
  },
  /** 幂等；返回是否确实移除了一个覆盖层 */
  unregister(id: string): boolean {
    const ok = overlayRegistry.delete(id)
    if (ok) notifyOverlays()
    return ok
  },
  /** 某分层当前全部覆盖层（按 order 升序，同 order 按注册顺序）；缺省返回全部层 */
  list(layer?: OverlayLayer): OverlayDef[] {
    const all = [...overlayRegistry.values()]
    const filtered = layer ? all.filter((o) => o.layer === layer) : all
    return filtered.sort((a, b) => (a.order ?? 100) - (b.order ?? 100))
  },
  /** 订阅注册表变化（React useSyncExternalStore 用）；返回退订函数 */
  subscribe(fn: () => void): () => void {
    overlayListeners.add(fn)
    return () => overlayListeners.delete(fn)
  },
  /** v1.6.0：当前宿主支持的全部分层名（只读）。插件据此做能力发现——
   *  在旧宿主上降级（不注册某分层），在新宿主上自动可用，无需解析版本号。 */
  layers(): readonly OverlayLayer[] {
    return OVERLAY_LAYERS
  },
  getVersion(): number {
    return overlayRegistryVersion
  },
}

// ---------------------------------------------------------------------------
// 皮肤（skins）：CSS 变量令牌集
// ---------------------------------------------------------------------------

/**
 * 皮肤令牌：key 为 CSS 变量名（--dt-*），value 为合法 CSS 值。
 * v1 受支持令牌（组件实际消费，见 API.md「皮肤令牌」）：
 *   --dt-card-radius  卡片圆角（Card 组件与全部小组件）
 *   --dt-font-sans    全局正文字体族
 */
export interface SkinDef {
  /** 唯一标识（如 'compact'、'my-plugin.sepia'）；'default' 保留给内置默认外观 */
  name: string
  /** 展示名 */
  label: string
  /** 亮色模式令牌 */
  tokens?: Record<string, string>
  /** 暗色模式追加/覆盖令牌 */
  darkTokens?: Record<string, string>
}

const SKIN_KEY = 'dt_skin'
const skinRegistry = new Map<string, SkinDef>()
skinRegistry.set('default', { name: 'default', label: '默认', tokens: {}, darkTokens: {} })

const appliedTokenKeys = new Set<string>()
let activeSkin = 'default'
const skinListeners = new Set<() => void>()

function currentMode(): 'light' | 'dark' {
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
}

function applySkin(name: string): boolean {
  const skin = skinRegistry.get(name)
  if (!skin) return false
  const root = document.documentElement
  for (const key of appliedTokenKeys) root.style.removeProperty(key)
  appliedTokenKeys.clear()
  const tokens = { ...skin.tokens, ...(currentMode() === 'dark' ? skin.darkTokens : undefined) }
  for (const [key, value] of Object.entries(tokens)) {
    if (!key.startsWith('--dt-')) continue // 只允许 --dt-* 命名空间，防覆盖无关变量
    root.style.setProperty(key, value)
    appliedTokenKeys.add(key)
  }
  activeSkin = name
  try {
    localStorage.setItem(SKIN_KEY, name)
  } catch {
    /* 隐私模式等：皮肤仍当场生效 */
  }
  skinListeners.forEach((fn) => {
    try {
      fn()
    } catch (e) {
      console.error('[DeepTutor] skins 订阅回调异常', e)
    }
  })
  eventsApi.emit('skinchange', { name })
  return true
}

const skinsApi = {
  register(def: SkinDef): void {
    if (!def || !def.name || def.name === 'default') throw new Error('[DeepTutor] skin.name 必填且不能为 default')
    skinRegistry.set(def.name, { tokens: {}, darkTokens: {}, ...def })
  },
  /** 应用皮肤；未知名称返回 false */
  apply: applySkin,
  /** 当前皮肤名 */
  active(): string {
    return activeSkin
  },
  list(): SkinDef[] {
    return [...skinRegistry.values()]
  },
  subscribe(fn: () => void): () => void {
    skinListeners.add(fn)
    return () => skinListeners.delete(fn)
  },
}

// ---------------------------------------------------------------------------
// 事件（events）
// ---------------------------------------------------------------------------

/** API 事件名（新增只在 minor 版本追加） */
export interface DeepTutorEventMap {
  /** 皮肤切换后触发 */
  skinchange: { name: string }
  /** 任务相位迁移（iter 195 F10，v1.1.0）：方案/材料生成任务每次相位变化时触发。
   *  phase: running|clarify|done|error|cancelled|interrupted；
   *  任务全量状态可经 api.get('/sessions') 主动查询。 */
  taskchange: { id: string; kind: 'plan' | 'materials'; phase: string; title: string }
}

type EventName = keyof DeepTutorEventMap
const eventListeners = new Map<EventName, Set<(payload: unknown) => void>>()

const eventsApi = {
  on<E extends EventName>(event: E, fn: (payload: DeepTutorEventMap[E]) => void): () => void {
    let set = eventListeners.get(event)
    if (!set) {
      set = new Set()
      eventListeners.set(event, set)
    }
    const wrapped = fn as (payload: unknown) => void
    set.add(wrapped)
    return () => set.delete(wrapped)
  },
  emit<E extends EventName>(event: E, payload: DeepTutorEventMap[E]): void {
    eventListeners.get(event)?.forEach((fn) => {
      try {
        fn(payload)
      } catch (e) {
        console.error(`[DeepTutor] 事件监听器异常 (${event})`, e)
      }
    })
  },
}

// ---------------------------------------------------------------------------
// REST 代理（api）
// ---------------------------------------------------------------------------

/** v1.2.0：api.request 的选项。body 为任意可 JSON 序列化值（自动 stringify + json 头）。 */
export interface ApiRequestInit {
  /** HTTP 方法，缺省 GET */
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  /** 请求体（自动 JSON.stringify；GET/DELETE 一般省略） */
  body?: unknown
  /** 超时毫秒（缺省 120s；长任务端点可放宽，如 300_000） */
  timeoutMs?: number
  /** 调用方取消信号（组件卸载时中断请求） */
  signal?: AbortSignal
}

const apiProxy = {
  /** GET 便捷方法 */
  get: <T,>(path: string) => apiFetch<T>(path),
  /** v1.2.0：全方法 REST 代理。与前端自身请求同一机制（登录态/超时/401 跳登录）。 */
  request: <T,>(path: string, init?: ApiRequestInit) =>
    apiFetch<T>(path, {
      method: init?.method ?? 'GET',
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
      timeoutMs: init?.timeoutMs,
      signal: init?.signal,
    }),
}

// ---------------------------------------------------------------------------
// 外部插件加载（plugins）
// ---------------------------------------------------------------------------

const PLUGINS_KEY = 'dt_plugins'

function readPluginUrls(): string[] {
  try {
    const raw = localStorage.getItem(PLUGINS_KEY)
    const arr = raw ? (JSON.parse(raw) as unknown) : []
    return Array.isArray(arr) ? arr.filter((u): u is string => typeof u === 'string') : []
  } catch {
    return []
  }
}

function writePluginUrls(urls: string[]): void {
  try {
    localStorage.setItem(PLUGINS_KEY, JSON.stringify(urls))
  } catch {
    /* 忽略持久化失败 */
  }
}

/** 单个插件脚本加载超时（毫秒）：超时按失败处理——防挂起 URL 饿死其后持久化插件 */
const SCRIPT_TIMEOUT_MS = 15_000

// iter 244 F7：本会话已成功注入的脚本 URL——plugins.load 幂等判定（重复 load 同 URL
// 原会重复注入执行脚本：插件重复注册 widget/重复订阅事件 -> 重复 toast/副作用）
const injectedScripts = new Set<string>()

// iter 247：加载进行中的 Promise 台账——F7 的幂等判定只在 onload 后生效，两次并发
// load(同 URL)（如启动自动加载进行中 + 用户在 /plugins 页手动加载同一 URL）会双双
// 通过 has 检查重复注入；并发调用改为共享同一注入 Promise（失败同样共享 reject）
const inflightLoads = new Map<string, Promise<void>>()

function injectScript(url: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script')
    const timer = setTimeout(() => {
      s.remove() // 移除元素取消挂起的请求，防迟到 onload 再 settle
      reject(new Error(`[DeepTutor] 插件脚本加载超时（${SCRIPT_TIMEOUT_MS / 1000}s）: ${url}`))
    }, SCRIPT_TIMEOUT_MS)
    s.src = url
    s.async = true
    s.onload = () => {
      clearTimeout(timer)
      injectedScripts.add(url)
      resolve()
    }
    s.onerror = () => {
      clearTimeout(timer)
      reject(new Error(`[DeepTutor] 插件脚本加载失败: ${url}`))
    }
    document.head.appendChild(s)
  })
}

const pluginsApi = {
  /** 加载外部插件脚本并持久化（刷新后自动加载）；脚本以 window.DeepTutor 完成注册 */
  async load(url: string): Promise<void> {
    if (!/^https?:\/\//i.test(url)) throw new Error('[DeepTutor] 插件 URL 需以 http(s):// 开头')
    const persist = () => {
      const urls = readPluginUrls()
      if (!urls.includes(url)) writePluginUrls([...urls, url])
    }
    if (injectedScripts.has(url)) {
      // iter 244 F7：本会话已注入 -> 幂等返回（仅补持久化登记，不重复执行脚本）
      persist()
      return
    }
    const inflight = inflightLoads.get(url)
    if (inflight) {
      // iter 247：同 URL 加载进行中 -> 共享其结果（成功则幂等，失败同样上抛）
      await inflight
      persist()
      return
    }
    const p = injectScript(url).finally(() => inflightLoads.delete(url))
    inflightLoads.set(url, p)
    await p
    persist()
  },
  /** 从持久化列表移除；已执行的脚本副作用需刷新页面才消除 */
  unload(url: string): void {
    writePluginUrls(readPluginUrls().filter((u) => u !== url))
  },
  list(): string[] {
    return readPluginUrls()
  },
}

// ---------------------------------------------------------------------------
// 聚合 API 与初始化
// ---------------------------------------------------------------------------

/** 内部使用：内置组件注册 / WidgetSlot 消费（与 window.DeepTutor.widgets 同一对象） */
export const widgets = widgetsApi
export const skins = skinsApi
export const events = eventsApi
/** 内部使用：插件页面路由/导航消费（与 window.DeepTutor.pages 同一对象） */
export const pages = pagesApi
/** 内部使用：插件管理页（与 window.DeepTutor.plugins 同一对象） */
export const plugins = pluginsApi
/** 内部使用：插件覆盖层宿主消费（与 window.DeepTutor.overlay 同一对象） */
export const overlays = overlaysApi

export interface DeepTutorAPI {
  /** API 语义化版本 */
  version: string
  widgets: typeof widgetsApi
  skins: typeof skinsApi
  events: typeof eventsApi
  plugins: typeof pluginsApi
  /** v1.4.0：注册完整路由页（/p/ 命名空间）+ 可选侧栏导航——"用 API 完全替换前端"使能项 */
  pages: typeof pagesApi
  /** v1.5.0：SPA 导航 + 路由变化订阅——插件多页应用不整页刷新跳转的必需能力 */
  router: typeof routerApi
  /** v1.6.0：注册应用根级覆盖层（WebGL 背景/浮窗/物理叠层/3D 接管）——"用 API 完全改造前端"使能项 */
  overlay: typeof overlaysApi
  /** 以当前登录态调用后端 REST（路径如 '/progress/stats/summary'，相对 /api 前缀） */
  api: typeof apiProxy
  /** 暴露 React，供无构建链的纯 JS 插件创建组件（const h = DeepTutor.React.createElement） */
  React: unknown
}

let initialized = false

/** 应用启动时调用一次：挂全局 API、恢复皮肤、订阅亮暗切换、自动加载持久化插件 */
export async function initPlugins(React: unknown): Promise<void> {
  if (initialized) return
  initialized = true

  const api: DeepTutorAPI = {
    version: API_VERSION,
    widgets: widgetsApi,
    skins: skinsApi,
    events: eventsApi,
    plugins: pluginsApi,
    pages: pagesApi,
    router: routerApi,
    overlay: overlaysApi,
    api: apiProxy,
    React,
  }
  ;(window as unknown as { DeepTutor: DeepTutorAPI }).DeepTutor = api

  // 恢复上次皮肤
  let saved = 'default'
  try {
    saved = localStorage.getItem(SKIN_KEY) || 'default'
  } catch {
    /* ignore */
  }
  applySkin(saved)

  // 亮/暗切换后重放皮肤（darkTokens 覆盖需重算）；
  // iter 278：订阅 resolvedTheme（system 模式下 OS 切换也触发重放）
  useUIStore.subscribe((state, prev) => {
    if (state.resolvedTheme !== prev.resolvedTheme) applySkin(activeSkin)
  })

  // 自动加载持久化插件（失败仅告警，不阻塞应用）
  for (const url of readPluginUrls()) {
    try {
      await injectScript(url)
    } catch (e) {
      console.warn(e)
    }
  }
}

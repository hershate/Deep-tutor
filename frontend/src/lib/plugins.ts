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
import { toast as _hostToast, type ToastAction } from '../components/Toast'
import { useUIStore } from '../store/ui'

export const API_VERSION = '1.17.0'

// ---------------------------------------------------------------------------
// 插件注册追踪（v1.17.0）：热启停（plugins.disable/enable）的基础
// ---------------------------------------------------------------------------
//
// 前端插件脚本是裸 JS——执行后宿主无法自动得知它注册了什么。为支持「在插件页
// 直接关闭插件（widgets/pages/overlay/skins 全部消失）而不刷新页面」，注入脚本
// 期间把 _currentPluginUrl 置为该插件 URL，各注册表的 register() 把注册键记入
// _pluginRegistrations[url]；disable 时按键逆注册。注意：插件须在脚本执行期间
// 同步完成注册（setTimeout/异步回调中的注册不被追踪，disable 无法清除——与
// 后端 register(api) 同步约定一致）。

/** 当前正在注入的插件脚本 URL（injectScript 期间有效，注册追踪用） */
let _currentPluginUrl: string | null = null

/** 每个插件 URL 在各注册表中的注册键台账 */
interface PluginRegistrations {
  widgets: Set<string>
  pages: Set<string>
  overlays: Set<string>
  skins: Set<string>
}
const _pluginRegistrations = new Map<string, PluginRegistrations>()

/** 注册表通用追踪入口：当前注入中的插件有注册时记账 */
function _track(kind: keyof PluginRegistrations, key: string): void {
  if (!_currentPluginUrl) return
  let reg = _pluginRegistrations.get(_currentPluginUrl)
  if (!reg) {
    reg = { widgets: new Set(), pages: new Set(), overlays: new Set(), skins: new Set() }
    _pluginRegistrations.set(_currentPluginUrl, reg)
  }
  reg[kind].add(key)
}

// ---------------------------------------------------------------------------
// 小组件（widgets）
// ---------------------------------------------------------------------------

/** 命名插槽：页面中允许挂接小组件的位置（新增插槽只在 minor 版本追加，不改名/删除） */
export const WIDGET_SLOTS = [
  'home.dashboard',
  'sidebar.footer',
  'plan.detail',
  'plans.toolbar',
  'review.dashboard',
  'learn.dashboard',
  'notes.toolbar',
  'usage.dashboard',
  'wiki.dashboard',
] as const
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
    _track('widgets', def.id)
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
  /** v1.16.0：覆盖宿主页面路由（如 'plans' → 替换 /plans 页面）。受保护路由不可覆盖（/plugins、/settings、/login、/setup）。
   * 设 override 后，path 仅作注册键（实际路由由 override 值决定宿主页）。组件在应用 Layout 内渲染（含错误边界隔离）。 */
  override?: string
}

const pageRegistry = new Map<string, PageDef>()
let pageRegistryVersion = 0
const pageListeners = new Set<() => void>()

// v1.16.0：页面覆盖注册表（override hostPath -> plugin PageDef）
const pageOverrides = new Map<string, PageDef>()
/** 受保护路由——不可被插件覆盖（防止锁死管理/设置入口） */
const PROTECTED_ROUTES = new Set(['plugins', 'settings', 'login', 'setup'])

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
    // v1.16.0：页面覆盖——override 指定宿主路由（如 'plans'），受保护路由拒绝
    if (def.override) {
      const hostPath = def.override.toLowerCase()
      if (PROTECTED_ROUTES.has(hostPath)) {
        throw new Error(`[DeepTutor] 受保护路由不可覆盖: /${hostPath}（/plugins、/settings、/login、/setup 始终保留）`)
      }
      pageOverrides.set(hostPath, { ...def, path })
      _track('pages', hostPath)
      notifyPages()
      return
    }
    pageRegistry.set(path, { ...def, path })
    _track('pages', path)
    notifyPages()
  },
  /** 幂等；返回是否确实移除了一个页面（含 override） */
  unregister(path: string): boolean {
    const norm = normalizePagePath(path)
    let ok = pageRegistry.delete(norm)
    // 也检查 override（path 可能是 override 的宿主路由名）
    if (pageOverrides.delete(norm) || pageOverrides.delete(path.toLowerCase())) ok = true
    if (ok) notifyPages()
    return ok
  },
  /** v1.16.0：查询某宿主路由是否有插件覆盖注册；返回 PageDef 或 undefined */
  getOverride(hostPath: string): PageDef | undefined {
    return pageOverrides.get(hostPath.toLowerCase())
  },
  /** v1.16.0：全部已注册覆盖（宿主路由名 -> PageDef），供管理页可观测 */
  overrides(): PageDef[] {
    return [...pageOverrides.entries()].map(([k, v]) => ({ ...v, path: k }))
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
    _track('overlays', def.id)
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
// UI 辅助（ui）：可拖放等前端交互工具（v1.8.0）
// ---------------------------------------------------------------------------
//
// 为什么需要：overlay（§二补三）让插件能渲染浮层，但"可自由拖放的小组件"（用户点名需求）
// 须每个插件自行实现拖拽（pointer 事件 + 位置状态 + 边界 + 持久化），重复且易错。本命名空间
// 提供受支持的拖拽工具——插件对自己的 DOM 元素调用一次即获得"可拖放 + 视口边界 + 位置持久化"，
// 配合 overlay 的 content 层即做出可自由拖放的小组件。

export interface DraggableOptions {
  /** 拖拽手柄（pointerdown 起始元素）；缺省 = 被拖元素自身 */
  handle?: HTMLElement | null
  /** localStorage 键——持久化位置，刷新后恢复；缺省不持久化（localStorage 不可用时静默降级） */
  persistKey?: string
  /** 移动边界：'viewport'（缺省，限制在视口内）/ 指定容器元素 / null（不限制） */
  bounds?: 'viewport' | HTMLElement | null
  /** 初始位置（px，fixed 坐标）；缺省 = persistKey 恢复值 或 元素当前 bounding rect */
  initial?: { x: number; y: number }
}

export interface DraggableHandle {
  /** 移除拖拽行为（移除监听）；overlay unregister / 组件卸载时调用 */
  destroy(): void
}

/** 缩放方向（n/s/e/w + 四角）；默认全部 8 个手柄 */
export type ResizeHandle = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw'

export interface ResizableOptions {
  /** 启用哪些手柄；缺省全部 8 个（四边 + 四角） */
  handles?: ResizeHandle[]
  /** 最小尺寸（px）；缺省 {width:80, height:50} */
  minSize?: { width: number; height: number }
  /** localStorage 键——持久化尺寸，刷新后恢复；缺省不持久化 */
  persistKey?: string
}

export interface ResizableHandle {
  /** 移除缩放手柄与监听（overlay unregister / 组件卸载时调用） */
  destroy(): void
}

export interface DroppableOptions {
  /** 过滤哪些被拖元素可放入（返回 true 接受）；缺省接受全部 */
  accept?: (draggedEl: HTMLElement) => boolean
  /** 放下时调用（传入被拖元素） */
  onDrop?: (draggedEl: HTMLElement) => void
  /** 拖拽进入区域时调用（视觉反馈，如高亮） */
  onDragEnter?: (draggedEl: HTMLElement) => void
  /** 拖拽离开区域时调用（移除高亮） */
  onDragLeave?: (draggedEl: HTMLElement) => void
}

export interface DroppableHandle {
  destroy(): void
}

export interface SortableOptions {
  /** 拖拽手柄 CSS 选择器（在 item 内须手动设 draggable=true 或用手柄触发）；缺省=item 自身 */
  handle?: string
  /** 重排后回调（fromIndex → toIndex） */
  onChange?: (fromIndex: number, toIndex: number) => void
}

export interface SortableHandle {
  destroy(): void
}

function _readPosLS(key: string): { x: number; y: number } | null {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const o = JSON.parse(raw) as unknown
    if (o && typeof o === 'object' && Number.isFinite((o as { x?: unknown }).x) && Number.isFinite((o as { y?: unknown }).y)) {
      const o2 = o as { x: number; y: number }
      return { x: o2.x, y: o2.y }
    }
  } catch {
    /* 隐私模式 / 损坏 JSON：静默降级为不持久化 */
  }
  return null
}

function _writePosLS(key: string, x: number, y: number): void {
  try {
    localStorage.setItem(key, JSON.stringify({ x, y }))
  } catch {
    /* 配额/隐私模式：静默 */
  }
}

function _readSizeLS(key: string): { width: number; height: number } | null {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const o = JSON.parse(raw) as unknown
    if (o && typeof o === 'object' && Number.isFinite((o as { width?: unknown }).width) && Number.isFinite((o as { height?: unknown }).height)) {
      const o2 = o as { width: number; height: number }
      return { width: o2.width, height: o2.height }
    }
  } catch {
    /* 隐私模式 / 损坏 JSON：静默降级 */
  }
  return null
}

function _writeSizeLS(key: string, width: number, height: number): void {
  try {
    localStorage.setItem(key, JSON.stringify({ width, height }))
  } catch {
    /* 配额/隐私模式：静默 */
  }
}

// --- 拖拽 pub-sub（makeDraggable 写，makeDroppable 读）---
// makeDraggable 在 pointer-capture 下拖拽时，其他元素收不到 pointer 事件——
// 故 makeDraggable 广播 move/end 位置，makeDroppable 据此判定悬停/放下。
const _dragMoveListeners = new Set<(el: HTMLElement, x: number, y: number) => void>()
const _dragEndListeners = new Set<(el: HTMLElement, x: number, y: number) => void>()

function _notifyDragMove(el: HTMLElement, x: number, y: number): void {
  _dragMoveListeners.forEach((fn) => {
    try {
      fn(el, x, y)
    } catch {
      /* 监听器异常隔离 */
    }
  })
}

function _notifyDragEnd(el: HTMLElement, x: number, y: number): void {
  _dragEndListeners.forEach((fn) => {
    try {
      fn(el, x, y)
    } catch {
      /* 监听器异常隔离 */
    }
  })
}

/** 返回拖拽指针 Y 应插入到哪个元素之前（基于各子元素中点判定）；null=插到末尾。 */
function getDragAfterElement(container: HTMLElement, y: number): HTMLElement | null {
  const children = Array.from(container.children).filter(
    (c): c is HTMLElement =>
      c instanceof HTMLElement &&
      c.getAttribute('data-sortable-item') !== null &&
      c.style.opacity !== '0.4', // 排除正在拖拽的元素
  )
  let closest: HTMLElement | null = null
  let closestOffset = Number.NEGATIVE_INFINITY
  for (const child of children) {
    const box = child.getBoundingClientRect()
    const offset = y - box.top - box.height / 2
    if (offset < 0 && offset > closestOffset) {
      closestOffset = offset
      closest = child
    }
  }
  return closest
}

const uiApi = {
  /** 让一个 DOM 元素可自由拖放（fixed 定位 + pointer 事件 + 边界 + 可选位置持久化）。
   *  返回 { destroy } —— 卸载时调用移除监听。配 overlay content 层做可拖放浮窗/小组件。
   *  元素须是 HTMLElement（缺省设置 position:fixed，按 initial/持久化/当前 rect 定位）。 */
  makeDraggable(el: HTMLElement, opts: DraggableOptions = {}): DraggableHandle {
    if (!el || !(el instanceof HTMLElement)) {
      throw new Error('[DeepTutor] ui.makeDraggable 需传入 HTMLElement')
    }
    const handle: HTMLElement = opts.handle === undefined ? el : (opts.handle ?? el)
    const bounds = opts.bounds === undefined ? 'viewport' : opts.bounds

    // 初始定位
    let x: number
    let y: number
    const persisted = opts.persistKey ? _readPosLS(opts.persistKey) : null
    if (persisted) {
      x = persisted.x
      y = persisted.y
    } else if (opts.initial) {
      x = opts.initial.x
      y = opts.initial.y
    } else {
      const r = el.getBoundingClientRect()
      x = r.left
      y = r.top
    }
    el.style.position = 'fixed'
    el.style.left = `${x}px`
    el.style.top = `${y}px`
    el.style.margin = '0'
    el.style.touchAction = 'none' // 拖拽时阻止触摸滚动

    let dragging = false
    let startX = 0
    let startY = 0
    let origX = 0
    let origY = 0
    let origZ = ''

    const clamp = (nx: number, ny: number): [number, number] => {
      if (!bounds) return [nx, ny]
      const r = el.getBoundingClientRect()
      const bw = bounds === 'viewport' ? null : bounds.getBoundingClientRect()
      const minX = bw ? bw.left : 0
      const minY = bw ? bw.top : 0
      const maxX = (bw ? bw.right : window.innerWidth) - r.width
      const maxY = (bw ? bw.bottom : window.innerHeight) - r.height
      return [Math.max(minX, Math.min(maxX, nx)), Math.max(minY, Math.min(maxY, ny))]
    }

    const onMove = (e: PointerEvent): void => {
      if (!dragging) return
      const [nx, ny] = clamp(origX + (e.clientX - startX), origY + (e.clientY - startY))
      el.style.left = `${nx}px`
      el.style.top = `${ny}px`
      _notifyDragMove(el, e.clientX, e.clientY) // v1.11.0：广播拖拽位置给 makeDroppable
    }

    const onUp = (e: PointerEvent): void => {
      if (!dragging) return
      dragging = false
      el.style.userSelect = ''
      el.style.zIndex = origZ
      window.removeEventListener('pointermove', onMove)
      if (opts.persistKey) {
        const r = el.getBoundingClientRect()
        _writePosLS(opts.persistKey, r.left, r.top)
      }
      _notifyDragEnd(el, e.clientX, e.clientY) // v1.11.0：广播拖拽结束位置给 makeDroppable
    }

    const onDown = (e: PointerEvent): void => {
      if (e.button !== 0 && e.pointerType === 'mouse') return // 仅主键（触摸/笔不限）
      dragging = true
      startX = e.clientX
      startY = e.clientY
      const r = el.getBoundingClientRect()
      origX = r.left
      origY = r.top
      origZ = el.style.zIndex
      el.style.userSelect = 'none'
      el.style.zIndex = '9999' // 拖拽中置顶
      try {
        ;(e.target as HTMLElement | null)?.setPointerCapture?.(e.pointerId)
      } catch {
        /* 某些元素不支持 pointer capture，window 监听已兜底 */
      }
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp, { once: true })
      e.preventDefault()
    }

    handle.addEventListener('pointerdown', onDown)
    return {
      destroy(): void {
        handle.removeEventListener('pointerdown', onDown)
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
      },
    }
  },
  /** 让一个 DOM 元素可缩放（v1.10.0）：在四边/四角加缩放手柄，pointer 拖拽改尺寸。
   *  返回 { destroy }。配 makeDraggable 即做出可拖放 + 可缩放的浮窗/面板。 */
  makeResizable(el: HTMLElement, opts: ResizableOptions = {}): ResizableHandle {
    if (!el || !(el instanceof HTMLElement)) {
      throw new Error('[DeepTutor] ui.makeResizable 需传入 HTMLElement')
    }
    const minSize = opts.minSize ?? { width: 80, height: 50 }
    const handles: ResizeHandle[] = opts.handles ?? ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']
    if (getComputedStyle(el).position === 'static') el.style.position = 'relative' // 手柄 absolute 须定位上下文
    el.style.boxSizing = 'border-box'

    // 初始尺寸（persistKey 恢复 / 当前 offset）
    const persisted = opts.persistKey ? _readSizeLS(opts.persistKey) : null
    if (persisted) {
      el.style.width = `${persisted.width}px`
      el.style.height = `${persisted.height}px`
    }

    const created: HTMLElement[] = []
    const cleanups: Array<() => void> = []

    // 各手柄的位置/光标样式
    const HANDLE_STYLE: Record<ResizeHandle, Partial<CSSStyleDeclaration>> = {
      n: { top: '-4px', left: '0', right: '0', height: '8px', cursor: 'ns-resize' },
      s: { bottom: '-4px', left: '0', right: '0', height: '8px', cursor: 'ns-resize' },
      e: { right: '-4px', top: '0', bottom: '0', width: '8px', cursor: 'ew-resize' },
      w: { left: '-4px', top: '0', bottom: '0', width: '8px', cursor: 'ew-resize' },
      ne: { top: '-4px', right: '-4px', width: '12px', height: '12px', cursor: 'nesw-resize' },
      nw: { top: '-4px', left: '-4px', width: '12px', height: '12px', cursor: 'nwse-resize' },
      se: { bottom: '-4px', right: '-4px', width: '12px', height: '12px', cursor: 'nwse-resize' },
      sw: { bottom: '-4px', left: '-4px', width: '12px', height: '12px', cursor: 'nesw-resize' },
    }

    for (const dir of handles) {
      const h = document.createElement('div')
      h.style.position = 'absolute'
      h.style.zIndex = '10'
      h.style.touchAction = 'none'
      Object.assign(h.style, HANDLE_STYLE[dir])
      el.appendChild(h)
      created.push(h)

      let startX = 0
      let startY = 0
      let startW = 0
      let startH = 0
      let startL = 0
      let startT = 0

      const onMove = (e: PointerEvent): void => {
        const dx = e.clientX - startX
        const dy = e.clientY - startY
        let w = startW
        let hgt = startH
        let l = startL
        let t = startT
        if (dir.includes('e')) w = startW + dx
        if (dir.includes('s')) hgt = startH + dy
        if (dir.includes('w')) {
          w = Math.max(minSize.width, startW - dx)
          l = startL + (startW - w) // 左边收缩时右边缘固定
        }
        if (dir.includes('n')) {
          hgt = Math.max(minSize.height, startH - dy)
          t = startT + (startH - hgt) // 上边收缩时下边缘固定
        }
        w = Math.max(minSize.width, w)
        hgt = Math.max(minSize.height, hgt)
        el.style.width = `${w}px`
        el.style.height = `${hgt}px`
        el.style.left = `${l}px`
        el.style.top = `${t}px`
      }

      const onUp = (): void => {
        el.style.userSelect = ''
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
        if (opts.persistKey) _writeSizeLS(opts.persistKey, el.offsetWidth, el.offsetHeight)
      }

      const onDown = (e: PointerEvent): void => {
        if (e.button !== 0 && e.pointerType === 'mouse') return
        e.preventDefault()
        e.stopPropagation() // 不触发元素自身的拖拽（与 makeDraggable 同元素时）
        startX = e.clientX
        startY = e.clientY
        startW = el.offsetWidth
        startH = el.offsetHeight
        startL = el.offsetLeft
        startT = el.offsetTop
        el.style.userSelect = 'none'
        try {
          h.setPointerCapture?.(e.pointerId)
        } catch {
          /* window 监听已兜底 */
        }
        window.addEventListener('pointermove', onMove)
        window.addEventListener('pointerup', onUp, { once: true })
      }

      h.addEventListener('pointerdown', onDown)
      cleanups.push(() => {
        h.removeEventListener('pointerdown', onDown)
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
      })
    }

    return {
      destroy(): void {
        cleanups.forEach((fn) => fn())
        created.forEach((h) => h.remove())
      },
    }
  },
  /** 让一个 DOM 元素成为拖放目标（v1.11.0）：检测 makeDraggable 元素被拖入/放下。
   *  返回 { destroy }。配 makeDraggable 即做出完整的拖放交互（拖到目标 → onDrop）。 */
  makeDroppable(el: HTMLElement, opts: DroppableOptions = {}): DroppableHandle {
    if (!el || !(el instanceof HTMLElement)) {
      throw new Error('[DeepTutor] ui.makeDroppable 需传入 HTMLElement')
    }
    let hovering = false // 当前是否有被拖元素悬停在本区域

    const inBounds = (x: number, y: number): boolean => {
      const r = el.getBoundingClientRect()
      return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom
    }

    const onMove = (draggedEl: HTMLElement, x: number, y: number): void => {
      if (!draggedEl || draggedEl === el) return // 不接受自身
      if (opts.accept && !opts.accept(draggedEl)) return
      const inside = inBounds(x, y)
      if (inside && !hovering) {
        hovering = true
        opts.onDragEnter?.(draggedEl)
      } else if (!inside && hovering) {
        hovering = false
        opts.onDragLeave?.(draggedEl)
      }
    }

    const onEnd = (draggedEl: HTMLElement, x: number, y: number): void => {
      if (!draggedEl || draggedEl === el) return
      if (opts.accept && !opts.accept(draggedEl)) {
        hovering = false
        return
      }
      if (hovering && inBounds(x, y)) {
        opts.onDrop?.(draggedEl)
      }
      if (hovering) {
        hovering = false
        opts.onDragLeave?.(draggedEl)
      }
    }

    _dragMoveListeners.add(onMove)
    _dragEndListeners.add(onEnd)
    return {
      destroy(): void {
        _dragMoveListeners.delete(onMove)
        _dragEndListeners.delete(onEnd)
      },
    }
  },
  /** 让列表/容器的子项可拖拽重排（v1.12.0）：HTML5 DnD，中点判定插入位置。
   *  返回 { destroy }。任务列表/播放列表/看板列重排等场景。 */
  makeSortable(container: HTMLElement, opts: SortableOptions = {}): SortableHandle {
    if (!container || !(container instanceof HTMLElement)) {
      throw new Error('[DeepTutor] ui.makeSortable 需传入 HTMLElement（容器）')
    }

    let draggedItem: HTMLElement | null = null
    let draggedIndex = -1
    const items = (): HTMLElement[] =>
      Array.from(container.children).filter(
        (c): c is HTMLElement => c instanceof HTMLElement && c !== placeholder,
      )
    // 占位指示元素（拖拽中显示插入位置）
    const placeholder = document.createElement('div')
    placeholder.style.height = '2px'
    placeholder.style.background = 'var(--dt-color-primary, #4f46e5)'
    placeholder.style.borderRadius = '1px'
    placeholder.style.margin = '2px 0'
    placeholder.style.opacity = '0.6'

    const onDragStart = (e: DragEvent): void => {
      const target = (e.target as HTMLElement)?.closest?.('[data-sortable-item]')
      if (!target || !container.contains(target)) return
      draggedItem = target as HTMLElement
      draggedIndex = items().indexOf(draggedItem)
      e.dataTransfer?.setData('text/plain', String(draggedIndex))
      draggedItem.style.opacity = '0.4'
    }

    const onDragOver = (e: DragEvent): void => {
      if (!draggedItem) return
      e.preventDefault() // 允许 drop
      const after = getDragAfterElement(container, e.clientY)
      if (after == null) {
        container.appendChild(placeholder)
      } else if (after !== placeholder) {
        container.insertBefore(placeholder, after)
      }
    }

    const onDrop = (e: DragEvent): void => {
      e.preventDefault()
      if (!draggedItem) return
      // 真正重排：把 draggedItem 移到 placeholder 位置
      container.insertBefore(draggedItem, placeholder)
      placeholder.remove()
      draggedItem.style.opacity = ''
      const newIndex = items().indexOf(draggedItem)
      if (newIndex !== draggedIndex && opts.onChange) {
        opts.onChange(draggedIndex, newIndex)
      }
      draggedItem = null
    }

    const onDragEnd = (): void => {
      placeholder.remove()
      if (draggedItem) draggedItem.style.opacity = ''
      draggedItem = null
    }

    container.addEventListener('dragstart', onDragStart)
    container.addEventListener('dragover', onDragOver)
    container.addEventListener('drop', onDrop)
    container.addEventListener('dragend', onDragEnd)

    // 标记现有子项为 sortable item（设 draggable + data 属性）
    const markItems = (): void => {
      for (const child of Array.from(container.children)) {
        if (child instanceof HTMLElement && !child.hasAttribute('data-sortable-item')) {
          child.setAttribute('data-sortable-item', '')
          child.draggable = true
        }
      }
    }
    markItems()

    return {
      destroy(): void {
        container.removeEventListener('dragstart', onDragStart)
        container.removeEventListener('dragover', onDragOver)
        container.removeEventListener('drop', onDrop)
        container.removeEventListener('dragend', onDragEnd)
        placeholder.remove()
        for (const child of Array.from(container.children)) {
          if (child instanceof HTMLElement) {
            child.removeAttribute('data-sortable-item')
            child.draggable = false
          }
        }
      },
    }
  },
  /** 通知反馈（v1.9.0）：复用宿主 Toast 系统（右下角，3s 自动消失）。
   *  插件据此给用户瞬时反馈（"已保存"/"复制成功"/"出错了"），无需自建 Toast UI。
   *  可选 action（带按钮的通知，TTL 延至 8s 供点击）。 */
  toast: {
    success: (message: string, action?: ToastAction) => _hostToast.success(message, action),
    info: (message: string, action?: ToastAction) => _hostToast.info(message, action),
    error: (message: string, action?: ToastAction) => _hostToast.error(message, action),
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
// 内置皮肤（供用户直接选用，也验证皮肤系统端到端可用）
skinRegistry.set('compact', {
  name: 'compact',
  label: '紧凑',
  tokens: { '--dt-card-radius': '0.375rem' },
  darkTokens: { '--dt-card-radius': '0.375rem' },
})
skinRegistry.set('rounded', {
  name: 'rounded',
  label: '大圆角',
  tokens: { '--dt-card-radius': '1.25rem' },
  darkTokens: { '--dt-card-radius': '1.25rem' },
})

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
    _track('skins', def.name)
    // 通知监听器（Plugins 页据此重渲染，下拉框显示新皮肤——此前 register 不通知是 bug）
    skinListeners.forEach((fn) => {
      try {
        fn()
      } catch (e) {
        console.error('[DeepTutor] skins 注册通知回调异常', e)
      }
    })
  },
  /** v1.17.0：注销皮肤（热停用插件时清除其皮肤）。幂等；返回是否确实移除了一个皮肤。
   *  内置皮肤（default/compact/rounded）不可注销；当前激活皮肤被注销时回退 default。 */
  unregister(name: string): boolean {
    if (name === 'default' || name === 'compact' || name === 'rounded') return false // 内置皮肤保留
    const ok = skinRegistry.delete(name)
    if (!ok) return false
    if (activeSkin === name) applySkin('default') // 回退默认（applySkin 内含通知）
    else {
      skinListeners.forEach((fn) => {
        try {
          fn()
        } catch (e) {
          console.error('[DeepTutor] skins 注销通知回调异常', e)
        }
      })
    }
    return true
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

/** API 事件名（新增只在 minor 版本追加）。
 *
 * 两类事件源：
 * - 前端本地事件：skinchange / taskchange（本进程 emit）。
 * - 后端事件桥（v1.7.0，iter 315）：经 SSE /api/events/stream 转发后端事件总线事件
 *   （plan_created / progress_updated / quiz_submitted / materials_completed /
 *   learning_log_added / startup / shutdown 及插件自定义事件）——插件订阅即实时收到，
 *   免轮询。on/emit 接受任意字符串事件名（未知名以 unknown 载荷传递，前向兼容）。 */
export interface DeepTutorEventMap {
  /** 皮肤切换后触发 */
  skinchange: { name: string }
  /** 任务相位迁移（iter 195 F10，v1.1.0）：方案/材料生成任务每次相位变化时触发。
   *  phase: running|clarify|done|error|cancelled|interrupted；
   *  任务全量状态可经 api.get('/sessions') 主动查询。 */
  taskchange: { id: string; kind: 'plan' | 'materials'; phase: string; title: string }
  // —— 后端事件桥（iter 315，SSE 转发；载荷见 API.md「§核心事件清单」）——
  /** 方案新版本落库后（生成/重生成/重试统一收口） */
  plan_created: { plan_id: number; session_id: number; version: number; topic: string }
  /** 单节点进度更新提交后 */
  progress_updated: { user_id: number; plan_id: number; node_id: string; status: string; mastery: number }
  /** 测验作答提交后 */
  quiz_submitted: { user_id: number; quiz_id: number; plan_id: number; type: string | null; correct: boolean | null }
  /** 自学材料生成完成 */
  materials_completed: { plan_id: number; session_id: number; unit_count: number; degraded_count: number; partial: boolean }
  /** 学习打卡提交后 */
  learning_log_added: { user_id: number; plan_id: number; log_id: number; date: string; minutes: number; note: string | null }
}

type EventName = keyof DeepTutorEventMap
const eventListeners = new Map<string, Set<(payload: unknown) => void>>()

// v1.7.0：重载——已知事件名（DeepTutorEventMap）给类型化载荷；任意字符串（后端/插件自定义
// 事件）以 unknown 载荷传递（前向兼容：订阅宿主尚不存在的事件名不报错，只是暂不触发）。
// 对象字面量不能内联重载，故以 interface 声明重载、字面量提供实现签名。
interface EventsApi {
  on<E extends EventName>(event: E, fn: (payload: DeepTutorEventMap[E]) => void): () => void
  on(event: string, fn: (payload: unknown) => void): () => void
  emit<E extends EventName>(event: E, payload: DeepTutorEventMap[E]): void
  emit(event: string, payload: unknown): void
}

const eventsApi: EventsApi = {
  on(event: string, fn: (payload: unknown) => void): () => void {
    let set = eventListeners.get(event)
    if (!set) {
      set = new Set()
      eventListeners.set(event, set)
    }
    set.add(fn)
    return () => set.delete(fn)
  },
  emit(event: string, payload: unknown): void {
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
// v1.17.0：已停用（disabled）的插件 URL——与 PLUGINS_KEY 分离：load 列表保留
// （插件页仍可见、可一键启用），disabled 列表控制"注入与否"
const DISABLED_PLUGINS_KEY = 'dt_plugins_disabled'

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

function readDisabledUrls(): string[] {
  try {
    const raw = localStorage.getItem(DISABLED_PLUGINS_KEY)
    const arr = raw ? (JSON.parse(raw) as unknown) : []
    return Array.isArray(arr) ? arr.filter((u): u is string => typeof u === 'string') : []
  } catch {
    return []
  }
}

function writeDisabledUrls(urls: string[]): void {
  try {
    localStorage.setItem(DISABLED_PLUGINS_KEY, JSON.stringify(urls))
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
      _currentPluginUrl = null // 清理注册追踪上下文
      s.remove() // 移除元素取消挂起的请求，防迟到 onload 再 settle
      reject(new Error(`[DeepTutor] 插件脚本加载超时（${SCRIPT_TIMEOUT_MS / 1000}s）: ${url}`))
    }, SCRIPT_TIMEOUT_MS)
    s.src = url
    s.async = true
    // 注册追踪：脚本体执行（register 调用）发生在此处 appendChild 与 onload 之间，
    // 故 _currentPluginUrl 须在 appendChild 前置位、onload/onerror/超时后清除。
    // 注：async 脚本执行时序不确定，并发注入不同 URL 时追踪可能错位——约定插件
    // 须在脚本体执行期间同步完成注册（与后端 register(api) 同步约定一致）。
    _currentPluginUrl = url
    s.onload = () => {
      _currentPluginUrl = null
      clearTimeout(timer)
      injectedScripts.add(url)
      resolve()
    }
    s.onerror = () => {
      _currentPluginUrl = null
      clearTimeout(timer)
      reject(new Error(`[DeepTutor] 插件脚本加载失败: ${url}`))
    }
    document.head.appendChild(s)
  })
}

/** v1.17.0：清除某插件按台账追踪到的全部注册（widget/page/overlay/skin）。热停用用。 */
function _clearTrackedRegistrations(url: string): void {
  const reg = _pluginRegistrations.get(url)
  if (!reg) return
  reg.widgets.forEach((id) => widgetsApi.unregister(id))
  reg.pages.forEach((key) => pagesApi.unregister(key))
  reg.overlays.forEach((id) => overlaysApi.unregister(id))
  reg.skins.forEach((name) => skinsApi.unregister(name))
  _pluginRegistrations.delete(url)
}

const pluginsApi = {
  /** 加载外部插件脚本并持久化（刷新后自动加载）；脚本以 window.DeepTutor 完成注册。
   *  v1.17.0：已停用的插件调用 load 视为「重新启用」（先清除停用标记再注入）。 */
  async load(url: string): Promise<void> {
    if (!/^https?:\/\//i.test(url)) throw new Error('[DeepTutor] 插件 URL 需以 http(s):// 开头')
    // 若该插件此前被停用，load 即重新启用
    writeDisabledUrls(readDisabledUrls().filter((u) => u !== url))
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
  /** 从持久化列表移除（永久卸载）。v1.17.0：同时清除其追踪到的注册与停用标记，
   *  无需刷新页面即彻底移除其 widgets/pages/overlays/skins。 */
  unload(url: string): void {
    writePluginUrls(readPluginUrls().filter((u) => u !== url))
    writeDisabledUrls(readDisabledUrls().filter((u) => u !== url))
    _clearTrackedRegistrations(url)
    injectedScripts.delete(url)
  },
  /** v1.17.0：停用一个插件——清除其追踪到的全部注册（widgets/pages/overlays/skins
   *  即时消失）+ 记停用标记（持久化，刷新后不自动加载）。脚本副作用 DOM 残留无害，
   *  enable 时重新注入会重新注册。 */
  disable(url: string): void {
    const disabled = readDisabledUrls()
    if (!disabled.includes(url)) writeDisabledUrls([...disabled, url])
    _clearTrackedRegistrations(url)
    injectedScripts.delete(url) // enable 时重新注入
  },
  /** v1.17.0：启用一个已停用的插件——清除停用标记 + 重新注入脚本（重新注册）。 */
  async enable(url: string): Promise<void> {
    writeDisabledUrls(readDisabledUrls().filter((u) => u !== url))
    if (!injectedScripts.has(url)) {
      const inflight = inflightLoads.get(url)
      if (inflight) {
        await inflight
      } else {
        const p = injectScript(url).finally(() => inflightLoads.delete(url))
        inflightLoads.set(url, p)
        await p
      }
    }
  },
  /** v1.17.0：该插件是否处于停用状态 */
  isDisabled(url: string): boolean {
    return readDisabledUrls().includes(url)
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
/** 内部使用：UI 辅助（与 window.DeepTutor.ui 同一对象） */
export const ui = uiApi

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
  /** v1.8.0：UI 辅助（makeDraggable 可拖放等）——配合 overlay 做可自由拖放的小组件 */
  ui: typeof uiApi
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
    ui: uiApi,
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

  // 自动加载持久化插件（失败仅告警，不阻塞应用）；v1.17.0：跳过已停用的插件
  const disabled = readDisabledUrls()
  for (const url of readPluginUrls()) {
    if (disabled.includes(url)) continue // 停用的插件不自动注入（保持停用状态）
    try {
      await injectScript(url)
    } catch (e) {
      console.warn(e)
    }
  }
}

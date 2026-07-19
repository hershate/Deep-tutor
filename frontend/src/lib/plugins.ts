// Deep-tutor 前端美化插件 API（预留接口，稳定契约见仓库根目录 API.md）。
//
// 第三方插件通过全局 `window.DeepTutor` 访问本模块，可：
//   - widgets：向命名插槽（slot）注册小组件（React 组件，可用 DeepTutor.React 免打包 React）
//   - skins：注册/应用外观皮肤（CSS 变量令牌集，亮/暗双态，选择持久化）
//   - api：以当前登录态调用后端 REST（get 便捷方法 + request 全方法）
//   - events：订阅 API 事件
//   - plugins：加载/卸载外部插件脚本（URL 持久化，刷新后自动加载）
//
// 兼容性承诺：API_VERSION 语义化版本；minor/patch 向后兼容，major 变更会在 API.md 记录迁移说明。
import type { ComponentType } from 'react'
import { apiFetch } from './api'
import { useUIStore } from '../store/ui'

export const API_VERSION = '1.3.0'

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

function injectScript(url: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script')
    s.src = url
    s.async = true
    s.onload = () => resolve()
    s.onerror = () => reject(new Error(`[DeepTutor] 插件脚本加载失败: ${url}`))
    document.head.appendChild(s)
  })
}

const pluginsApi = {
  /** 加载外部插件脚本并持久化（刷新后自动加载）；脚本以 window.DeepTutor 完成注册 */
  async load(url: string): Promise<void> {
    if (!/^https?:\/\//i.test(url)) throw new Error('[DeepTutor] 插件 URL 需以 http(s):// 开头')
    await injectScript(url)
    const urls = readPluginUrls()
    if (!urls.includes(url)) writePluginUrls([...urls, url])
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
/** 内部使用：插件管理页（与 window.DeepTutor.plugins 同一对象） */
export const plugins = pluginsApi

export interface DeepTutorAPI {
  /** API 语义化版本 */
  version: string
  widgets: typeof widgetsApi
  skins: typeof skinsApi
  events: typeof eventsApi
  plugins: typeof pluginsApi
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

  // 亮/暗切换后重放皮肤（darkTokens 覆盖需重算）
  useUIStore.subscribe((state, prev) => {
    if (state.theme !== prev.theme) applySkin(activeSkin)
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

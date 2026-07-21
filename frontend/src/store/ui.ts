// 全局 UI 状态（plan/06 §六 useUIStore）：深浅主题。
// 持久化 localStorage('dt_theme')；iter 278：三态主题（light/dark/system）——
// 此前「首访跟随 prefers-color-scheme 一次 + 手动切换持久化」，OS 自动昼夜切换
// （Win11/macOS 夜间模式常见）后 app 停留旧主题须手动调两次/天。
// system = 持续跟随系统（matchMedia change 即时应用）；新用户默认 system
// （等价原首访行为且持续生效）；已存 'light'/'dark' 用户不受影响（向后兼容）。
// resolvedTheme = 实际生效主题（system 解析后），供插件皮肤等需要「当前亮暗」的消费方订阅。
// 模块加载即应用（防闪烁），切换写 <html> class + color-scheme。
import { create } from 'zustand'

export type Theme = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

const STORAGE_KEY = 'dt_theme'
const MEDIA_QUERY = '(prefers-color-scheme: dark)'

function systemTheme(): ResolvedTheme {
  return window.matchMedia?.(MEDIA_QUERY).matches ? 'dark' : 'light'
}

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'light' || saved === 'dark' || saved === 'system') return saved
  } catch {
    /* localStorage 不可用（隐私模式等） */
  }
  return 'system' // 默认跟随系统（原首访语义的持续化）
}

/** 应用主题到 DOM，返回实际生效的亮/暗。 */
function applyTheme(theme: Theme): ResolvedTheme {
  const eff = theme === 'system' ? systemTheme() : theme
  document.documentElement.classList.toggle('dark', eff === 'dark')
  document.documentElement.style.colorScheme = eff
  return eff
}

interface UIState {
  theme: Theme // 用户选择的模式（light/dark/system）
  resolvedTheme: ResolvedTheme // 实际生效的亮暗（插件皮肤重算等订阅此字段）
  toggleTheme: () => void
  sidebarCollapsed: boolean // AppShell 侧栏折叠为图标态（plan/06 §三）
  toggleSidebar: () => void
}

const SIDEBAR_KEY = 'dt_sidebar_collapsed'
const THEME_ORDER: Theme[] = ['light', 'dark', 'system']

export const useUIStore = create<UIState>((set, get) => ({
  theme: initialTheme(),
  resolvedTheme: applyTheme(initialTheme()), // 模块加载即应用（首屏不闪烁）
  toggleTheme: () => {
    const next = THEME_ORDER[(THEME_ORDER.indexOf(get().theme) + 1) % THEME_ORDER.length]
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      /* 忽略持久化失败，主题仍当场生效 */
    }
    set({ theme: next, resolvedTheme: applyTheme(next) })
  },
  sidebarCollapsed: (() => {
    try {
      return localStorage.getItem(SIDEBAR_KEY) === '1'
    } catch {
      return false
    }
  })(),
  toggleSidebar: () => {
    const next = !get().sidebarCollapsed
    try {
      localStorage.setItem(SIDEBAR_KEY, next ? '1' : '0')
    } catch {
      /* 同上 */
    }
    set({ sidebarCollapsed: next })
  },
}))

// iter 278：OS 主题变化即时跟随（仅 system 模式；light/dark 为用户显式选择不打扰）
window
  .matchMedia?.(MEDIA_QUERY)
  ?.addEventListener?.('change', () => {
    const s = useUIStore.getState()
    if (s.theme === 'system') {
      useUIStore.setState({ resolvedTheme: applyTheme('system') })
    }
  })

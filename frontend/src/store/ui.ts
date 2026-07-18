// 全局 UI 状态（plan/06 §六 useUIStore）：深浅主题。
// 持久化 localStorage('dt_theme')；首访跟随 prefers-color-scheme；
// 模块加载即应用（防闪烁），切换写 <html> class + color-scheme。
import { create } from 'zustand'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'dt_theme'

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'light' || saved === 'dark') return saved
  } catch {
    /* localStorage 不可用（隐私模式等） */
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  document.documentElement.style.colorScheme = theme
}

interface UIState {
  theme: Theme
  toggleTheme: () => void
}

export const useUIStore = create<UIState>((set, get) => ({
  theme: initialTheme(),
  toggleTheme: () => {
    const next: Theme = get().theme === 'dark' ? 'light' : 'dark'
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      /* 忽略持久化失败，主题仍当场生效 */
    }
    applyTheme(next)
    set({ theme: next })
  },
}))

// 模块导入即应用当前主题（首屏不闪烁）
applyTheme(useUIStore.getState().theme)

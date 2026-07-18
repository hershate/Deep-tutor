import type { ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import UsageMeter from './UsageMeter'
import { clearAuth, getStoredUser, hasJwtSession } from '../lib/auth'
import { useUIStore } from '../store/ui'

// AppShell（plan/06 §设计系统 / AppShell）。骨架阶段最小版 + 顶栏导航 + 用量小窗 + 登录态（P4 iter 59）+ 主题切换。
export default function Layout({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const user = getStoredUser()
  const theme = useUIStore((s) => s.theme)
  const toggleTheme = useUIStore((s) => s.toggleTheme)

  function onLogout() {
    clearAuth()
    navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b bg-white dark:border-slate-700 dark:bg-slate-800">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-5">
            <Link to="/" className="text-lg font-semibold tracking-tight hover:text-slate-700 dark:hover:text-slate-200">
              Deep-tutor
            </Link>
            <nav className="flex items-center gap-3 text-sm" aria-label="主导航">
              <Link to="/" className="text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white">
                首页
              </Link>
              <Link to="/usage" className="text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white">
                用量
              </Link>
              <Link to="/settings" className="text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white">
                设置
              </Link>
            </nav>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={toggleTheme}
              aria-label={theme === 'dark' ? '切换为浅色主题' : '切换为深色主题'}
              title={theme === 'dark' ? '切换为浅色主题' : '切换为深色主题'}
              className="rounded-lg border px-2 py-1 text-sm hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
            >
              <span aria-hidden="true">{theme === 'dark' ? '☀️' : '🌙'}</span>
            </button>
            <UsageMeter />
            {hasJwtSession() && user && (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-slate-600 dark:text-slate-300" title={user.username}>
                  {user.name}
                  {user.is_admin && <span className="ml-1 text-xs text-amber-600 dark:text-amber-400">(管理员)</span>}
                </span>
                <button
                  type="button"
                  onClick={onLogout}
                  className="text-slate-500 hover:text-slate-900 underline underline-offset-2 dark:text-slate-400 dark:hover:text-white"
                >
                  注销
                </button>
              </div>
            )}
          </div>
        </div>
      </header>
      <main className="flex-1 w-full max-w-5xl mx-auto px-4 py-6">{children}</main>
      <footer className="border-t bg-white dark:border-slate-700 dark:bg-slate-800">
        <div className="max-w-5xl mx-auto px-4 py-3 text-xs text-slate-400 dark:text-slate-500">
          Deep-tutor · Phase 1 MVP
        </div>
      </footer>
    </div>
  )
}

import { useState, type ReactNode } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import UsageMeter from './UsageMeter'
import { clearAuth, getStoredUser, hasJwtSession } from '../lib/auth'
import { useUIStore } from '../store/ui'

// AppShell（plan/06 §三）：顶栏 + 可折叠侧栏（图标态；窄屏收起为抽屉）。
// 侧栏：＋新建方案 / 方案列表 / 用量 / 设置 + 底部用量小窗；折叠态持久化（useUIStore）。

const NAV_ITEMS = [
  { to: '/', icon: '＋', label: '新建方案', end: true },
  { to: '/plans', icon: '📚', label: '方案列表', end: false },
  { to: '/usage', icon: '📊', label: '用量', end: false },
  { to: '/settings', icon: '⚙️', label: '设置', end: false },
]

export default function Layout({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const user = getStoredUser()
  const theme = useUIStore((s) => s.theme)
  const toggleTheme = useUIStore((s) => s.toggleTheme)
  const collapsed = useUIStore((s) => s.sidebarCollapsed)
  const toggleSidebar = useUIStore((s) => s.toggleSidebar)
  const [drawerOpen, setDrawerOpen] = useState(false) // 窄屏抽屉

  function onLogout() {
    clearAuth()
    navigate('/login', { replace: true })
  }

  const navLinkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${
      isActive
        ? 'bg-blue-50 font-medium text-blue-700 dark:bg-blue-950 dark:text-blue-300'
        : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-700'
    }`

  const sidebarNav = (
    <nav className="flex flex-1 flex-col gap-1" aria-label="侧栏导航">
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          onClick={() => setDrawerOpen(false)}
          className={navLinkClass}
          title={collapsed ? item.label : undefined}
        >
          <span aria-hidden="true" className="w-5 shrink-0 text-center">
            {item.icon}
          </span>
          {!collapsed && <span className="truncate">{item.label}</span>}
        </NavLink>
      ))}
    </nav>
  )

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b bg-white dark:border-slate-700 dark:bg-slate-800">
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-3">
            {/* 窄屏抽屉开关 */}
            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              aria-label="打开导航菜单"
              className="rounded-lg border px-2 py-1 text-sm hover:bg-slate-50 md:hidden dark:border-slate-600 dark:hover:bg-slate-700"
            >
              ☰
            </button>
            <Link to="/" className="text-lg font-semibold tracking-tight hover:text-slate-700 dark:hover:text-slate-200">
              Deep-tutor
            </Link>
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
                  className="text-slate-500 underline underline-offset-2 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"
                >
                  注销
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      <div className="mx-auto flex w-full max-w-6xl flex-1">
        {/* 桌面侧栏（md+）：可折叠为图标态 */}
        <aside
          className={`hidden shrink-0 border-r py-4 pl-4 transition-all md:flex md:flex-col dark:border-slate-700 ${
            collapsed ? 'w-16' : 'w-48'
          }`}
        >
          {sidebarNav}
          <button
            type="button"
            onClick={toggleSidebar}
            aria-label={collapsed ? '展开侧栏' : '收起侧栏'}
            className="mt-4 self-start rounded-lg border px-2 py-1 text-xs text-slate-500 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-700"
          >
            {collapsed ? '»' : '« 收起'}
          </button>
        </aside>

        {/* 窄屏抽屉 */}
        {drawerOpen && (
          <div
            className="fixed inset-0 z-40 bg-black/40 md:hidden"
            onClick={() => setDrawerOpen(false)}
            aria-hidden="true"
          >
            <aside
              className="h-full w-56 bg-white p-4 shadow-xl dark:bg-slate-800"
              role="dialog"
              aria-label="导航菜单"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="mb-3 flex items-center justify-between">
                <span className="font-semibold">Deep-tutor</span>
                <button
                  type="button"
                  onClick={() => setDrawerOpen(false)}
                  aria-label="关闭导航菜单"
                  className="rounded px-2 py-1 text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700"
                >
                  ✕
                </button>
              </div>
              {/* 抽屉内始终展开态：强制 collapsed=false 的副本 */}
              <nav className="flex flex-col gap-1" aria-label="抽屉导航">
                {NAV_ITEMS.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    onClick={() => setDrawerOpen(false)}
                    className={navLinkClass}
                  >
                    <span aria-hidden="true" className="w-5 shrink-0 text-center">
                      {item.icon}
                    </span>
                    <span className="truncate">{item.label}</span>
                  </NavLink>
                ))}
              </nav>
            </aside>
          </div>
        )}

        <main className="min-w-0 flex-1 px-4 py-6">{children}</main>
      </div>

      <footer className="border-t bg-white dark:border-slate-700 dark:bg-slate-800">
        <div className="mx-auto w-full max-w-6xl px-4 py-3 text-xs text-slate-400 dark:text-slate-500">
          Deep-tutor · Phase 1 MVP
        </div>
      </footer>
    </div>
  )
}

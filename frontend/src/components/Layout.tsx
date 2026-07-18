import { useEffect, useState, type ReactNode } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { Toaster } from './Toast'
import UsageMeter from './UsageMeter'
import { AUTH_CHANGED_EVENT, clearAuth, getStoredUser, hasJwtSession, type AuthUser } from '../lib/auth'
import { useSetupStatus } from '../lib/setup'
import { useUIStore } from '../store/ui'

// AppShell（plan/06 §三）：顶栏 + 可折叠侧栏（图标态；窄屏收起为抽屉）。
// 侧栏：＋新建方案 / 方案列表 / 用量 / 设置 + 底部用量小窗；折叠态持久化（useUIStore）。

const NAV_ITEMS = [
  { to: '/', icon: '＋', label: '新建方案', end: true },
  { to: '/plans', icon: '📚', label: '方案列表', end: false },
  { to: '/wiki', icon: '🧠', label: '知识库', end: false },
  { to: '/usage', icon: '📊', label: '用量', end: false },
  { to: '/settings', icon: '⚙️', label: '设置', end: false },
]

export default function Layout({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const location = useLocation() // iter 92：按路由 key 触发主区入场动画
  // err.md D17：getStoredUser() 直读 localStorage 非响应式（改名后顶栏不更新）——
  // 改 state + 监听 AUTH_CHANGED_EVENT（同源 setAuth/clearAuth/updateStoredUser）
  // 与 storage 事件（其他标签页）
  const [user, setUser] = useState<AuthUser | null>(() => getStoredUser())
  useEffect(() => {
    const sync = () => setUser(getStoredUser())
    window.addEventListener(AUTH_CHANGED_EVENT, sync)
    window.addEventListener('storage', sync)
    return () => {
      window.removeEventListener(AUTH_CHANGED_EVENT, sync)
      window.removeEventListener('storage', sync)
    }
  }, [])
  const theme = useUIStore((s) => s.theme)
  const toggleTheme = useUIStore((s) => s.toggleTheme)
  const collapsed = useUIStore((s) => s.sidebarCollapsed)
  const toggleSidebar = useUIStore((s) => s.toggleSidebar)
  const [drawerOpen, setDrawerOpen] = useState(false) // 窄屏抽屉
  // iter 89：开放模式（未设密码）/ 未配 API Key 的常驻提示横幅（状态查询失败不渲染）
  const { data: setupStatus } = useSetupStatus()

  // iter 106：路由切换回到页面顶部（此前停留在前一页的滚动位置，长列表跳详情体验突兀）
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [location.pathname])

  function onLogout() {
    clearAuth()
    navigate('/login', { replace: true })
  }

  const navLinkClass = ({ isActive }: { isActive: boolean }) =>
    `group relative flex items-center gap-2 rounded-lg px-3 py-2 text-sm transition-all duration-200 ${
      isActive
        ? 'bg-blue-50 font-medium text-blue-700 shadow-sm dark:bg-blue-950 dark:text-blue-300'
        : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-700 dark:hover:text-white'
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
      {/* iter 92：吸顶 + 毛玻璃顶栏，滚动时内容从其下滑过 */}
      <header className="sticky top-0 z-30 border-b bg-white/80 backdrop-blur-md dark:border-slate-700 dark:bg-slate-800/80">
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-3">
            {/* 窄屏抽屉开关 */}
            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              aria-label="打开导航菜单"
              className="rounded-lg border px-2 py-1 text-sm transition-colors hover:bg-slate-50 md:hidden dark:border-slate-600 dark:hover:bg-slate-700"
            >
              ☰
            </button>
            <Link to="/" className="text-lg font-semibold tracking-tight transition-colors hover:text-blue-600 dark:hover:text-blue-400">
              Deep-tutor
            </Link>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={toggleTheme}
              aria-label={theme === 'dark' ? '切换为浅色主题' : '切换为深色主题'}
              title={theme === 'dark' ? '切换为浅色主题' : '切换为深色主题'}
              className="rounded-lg border px-2 py-1 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
            >
              <span aria-hidden="true" className="inline-block transition-transform duration-300 hover:rotate-12">{theme === 'dark' ? '☀️' : '🌙'}</span>
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

      {/* iter 89 状态横幅：开放模式安全提示 / 未配 API Key 功能提示 */}
      {setupStatus?.open_mode && (
        <div role="status" className="border-b border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950">
          <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-4 py-1.5 text-xs text-amber-800 dark:text-amber-200">
            <span>当前未设置访问密码，本机局域网内任何人可访问此应用。</span>
            <Link to="/settings" className="font-medium underline underline-offset-2 hover:text-amber-900 dark:hover:text-amber-100">
              去设置密码
            </Link>
          </div>
        </div>
      )}
      {setupStatus && !setupStatus.llm_configured && (
        <div role="status" className="border-b border-blue-200 bg-blue-50 dark:border-blue-900 dark:bg-blue-950">
          <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-4 py-1.5 text-xs text-blue-800 dark:text-blue-200">
            <span>尚未配置 AI API Key，学习方案生成功能暂不可用。</span>
            <Link to="/settings" className="font-medium underline underline-offset-2 hover:text-blue-900 dark:hover:text-blue-100">
              去配置
            </Link>
          </div>
        </div>
      )}

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
            className="mt-4 self-start rounded-lg border px-2 py-1 text-xs text-slate-500 transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-700"
          >
            {collapsed ? '»' : '« 收起'}
          </button>
        </aside>

        {/* 窄屏抽屉（iter 92：遮罩淡入 + 面板左滑入） */}
        {drawerOpen && (
          <div
            className="fixed inset-0 z-40 animate-fade-in bg-black/40 md:hidden"
            onClick={() => setDrawerOpen(false)}
            aria-hidden="true"
          >
            <aside
              className="h-full w-56 animate-slide-in-left bg-white p-4 shadow-xl dark:bg-slate-800"
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

        {/* iter 92：key=路由路径，切换页面时主区淡入上移 */}
        <main key={location.pathname} className="min-w-0 flex-1 animate-fade-in-up px-4 py-6">{children}</main>
      </div>

      <footer className="border-t bg-white dark:border-slate-700 dark:bg-slate-800">
        <div className="mx-auto w-full max-w-6xl px-4 py-3 text-xs text-slate-400 dark:text-slate-500">
          Deep-tutor · Phase 1 MVP
        </div>
      </footer>
      <Toaster />
    </div>
  )
}

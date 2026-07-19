import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom'
import { Toaster } from './Toast'
import TaskCenter from './TaskCenter'
import TaskPanel from './TaskPanel'
import UsageMeter from './UsageMeter'
import WidgetSlot from './WidgetSlot'
import { AUTH_CHANGED_EVENT, clearAuth, getStoredUser, hasJwtSession, type AuthUser } from '../lib/auth'
import { useSetupStatus } from '../lib/setup'
import { useUIStore } from '../store/ui'

// AppShell（plan/06 §三）：全宽顶栏 + 贴边侧栏（图标态可折叠；窄屏收起为抽屉）。
// 侧栏贴视口左缘、sticky 吸于顶栏下（顶栏高度实测，随视口变化自适应）；
// 主内容在剩余空间内居中（max-w-5xl 保持阅读宽度）；折叠态持久化（useUIStore）。

const NAV_ITEMS = [
  { to: '/', icon: '＋', label: '新建方案', end: true },
  { to: '/plans', icon: '📚', label: '方案列表', end: false },
  { to: '/wiki', icon: '🧠', label: '知识库', end: false },
  { to: '/usage', icon: '📊', label: '用量', end: false },
  { to: '/plugins', icon: '🧩', label: '插件', end: false },
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

  // 贴边侧栏：实测吸顶高度（sticky 偏移 + 可视高度），ResizeObserver 随视口/换行自适应
  const headerRef = useRef<HTMLElement | null>(null)
  const [headerH, setHeaderH] = useState(0)
  useEffect(() => {
    const el = headerRef.current
    if (!el) return
    const update = () => setHeaderH(el.offsetHeight)
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // iter 106：路由切换回到页面顶部（此前停留在前一页的滚动位置，长列表跳详情体验突兀）
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [location.pathname])

  // iter 110：长页面回到顶部悬浮按钮（滚动 >400px 出现）
  const [showBackTop, setShowBackTop] = useState(false)
  useEffect(() => {
    const onScroll = () => setShowBackTop(window.scrollY > 400)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  function backToTop() {
    // 尊重 prefers-reduced-motion：即时跳转而非平滑滚动
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' })
  }

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
      {/* iter 108：键盘/读屏用户跳到主内容的 skip link（聚焦时才可见） */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-blue-600 focus:px-3 focus:py-2 focus:text-sm focus:text-white focus:shadow-lg"
      >
        跳到主内容
      </a>
      {/* iter 92：吸顶 + 毛玻璃顶栏，滚动时内容从其下滑过；全宽贴边 */}
      <header ref={headerRef} className="sticky top-0 z-30 border-b bg-white/80 backdrop-blur-md dark:border-slate-700 dark:bg-slate-800/80">
        <div className="flex w-full items-center justify-between px-4 py-3">
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
            {/* iter 188 F3：任务中心（顶栏指示器+下拉面板；窄屏/折叠态唯一入口） */}
            <TaskCenter />
            {hasJwtSession() && user && (
              <div className="flex items-center gap-2 text-sm">
                <span className="text-slate-600 dark:text-slate-300" title={user.username}>
                  {user.name}
                  {user.is_admin && <span className="ml-1 text-xs text-amber-600 dark:text-amber-400">(管理员)</span>}
                </span>
                <button
                  type="button"
                  onClick={onLogout}
                  className="text-slate-500 underline underline-offset-2 transition-colors hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"
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
          <div className="flex w-full items-center justify-between px-4 py-1.5 text-xs text-amber-800 dark:text-amber-200">
            <span>当前未设置访问密码，本机局域网内任何人可访问此应用。</span>
            <Link to="/settings" className="font-medium underline underline-offset-2 transition-colors hover:text-amber-900 dark:hover:text-amber-100">
              去设置密码
            </Link>
          </div>
        </div>
      )}
      {setupStatus && !setupStatus.llm_configured && (
        <div role="status" className="border-b border-blue-200 bg-blue-50 dark:border-blue-900 dark:bg-blue-950">
          <div className="flex w-full items-center justify-between px-4 py-1.5 text-xs text-blue-800 dark:text-blue-200">
            <span>尚未配置 AI API Key，学习方案生成功能暂不可用。</span>
            <Link to="/settings" className="font-medium underline underline-offset-2 transition-colors hover:text-blue-900 dark:hover:text-blue-100">
              去配置
            </Link>
          </div>
        </div>
      )}

      <div className="flex w-full flex-1">
        {/* 桌面侧栏（md+）：贴视口左缘，sticky 吸于顶栏下，可折叠为图标态；白底与主区（slate-50）分层 */}
        <aside
          className={`sticky hidden shrink-0 self-start overflow-y-auto border-r bg-white px-3 py-4 transition-all md:flex md:flex-col dark:border-slate-700 dark:bg-slate-800 ${
            collapsed ? 'w-16' : 'w-48'
          }`}
          style={{ top: headerH, height: `calc(100vh - ${headerH}px)` }}
        >
          {sidebarNav}
          {/* iter 188 F3：侧栏常驻任务面板（仅展开态；折叠/窄屏走顶栏任务中心） */}
          {!collapsed && <TaskPanel />}
          {/* iter 183：插件 API sidebar.footer 插槽（内置为空，供第三方插件挂接） */}
          {!collapsed && <WidgetSlot slot="sidebar.footer" layout="stack" className="mt-4" />}
          <button
            type="button"
            onClick={toggleSidebar}
            aria-label={collapsed ? '展开侧栏' : '收起侧栏'}
            aria-expanded={!collapsed}
            className="mt-auto self-start rounded-lg border px-2 py-1 text-xs text-slate-500 transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-700"
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
                  className="rounded px-2 py-1 text-slate-400 transition-colors hover:bg-slate-100 dark:hover:bg-slate-700"
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

        {/* iter 92：key=路由路径，切换页面时主区淡入上移；id 供 skip link 跳转；scroll-mt 防吸顶 header 遮挡跳转落点 */}
        {/* 内容在剩余空间内居中，max-w-5xl 保持阅读宽度（原 max-w-6xl 减去侧栏宽度后的近似等效） */}
        <main id="main-content" tabIndex={-1} key={location.pathname} className="min-w-0 flex-1 animate-fade-in-up scroll-mt-16 px-4 py-6 sm:px-6">
          <div className="mx-auto w-full max-w-5xl">{children}</div>
        </main>
      </div>

      <footer className="border-t bg-white dark:border-slate-700 dark:bg-slate-800">
        <div className="w-full px-4 py-3 text-xs text-slate-400 dark:text-slate-500">
          Deep-tutor · Phase 1 MVP
        </div>
      </footer>
      {/* iter 110：回到顶部（右下悬浮，Toast z-50 短暂遮挡可接受；scale-in 入场） */}
      {showBackTop && (
        <button
          type="button"
          onClick={backToTop}
          aria-label="回到顶部"
          title="回到顶部"
          className="fixed bottom-4 right-4 z-40 animate-scale-in rounded-full border bg-white/90 p-2.5 text-slate-500 shadow-lg backdrop-blur transition-all hover:-translate-y-0.5 hover:text-blue-600 hover:shadow-xl active:scale-95 dark:border-slate-600 dark:bg-slate-800/90 dark:text-slate-300 dark:hover:text-blue-400"
        >
          <svg aria-hidden="true" className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
            <path fillRule="evenodd" d="M10 3a1 1 0 0 1 .7.3l5 5a1 1 0 0 1-1.4 1.4L11 6.42V16a1 1 0 1 1-2 0V6.42L5.7 9.7A1 1 0 0 1 4.3 8.3l5-5A1 1 0 0 1 10 3Z" clipRule="evenodd" />
          </svg>
        </button>
      )}
      <Toaster />
    </div>
  )
}

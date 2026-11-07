import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { Bell, Blocks, BookOpen, Brain, CalendarDays, Check, ChevronDown, CircleAlert, Cloud, Command, FileText, FolderKanban, Home, Layers3, LibraryBig, ListChecks, Map, Menu, Moon, MoreHorizontal, Pause, Play, Plus, Search, Settings as SettingsIcon, Sparkles, Sun, Target, TimerReset, TrendingUp, Users, X, Zap } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import Dashboard from './pages/Dashboard'
import Plan from './pages/Plan'
import KnowledgeMap from './pages/KnowledgeMap'
import Practice from './pages/Practice'
import Review from './pages/Review'

import Projects from './pages/Projects'
import Reports from './pages/Reports'
import Community from './pages/Community'
import Settings from './pages/Settings'

import SharedPlan from './pages/SharedPlan'
import TutorPanel from './components/TutorPanel'
import AgentTaskCenter from './components/AgentTaskCenter'
import DataDirectoryPanel from './components/DataDirectoryPanel'
import SecurityPanel from './components/SecurityPanel'
import { Button, IconButton, Modal, useFocusContainment } from './components/UI'
import type { PageId, Project, Task } from './data'
import { backendErrorMessage, onBackendEvent, reportBackendError } from './backend/client'
import type { AppSnapshot } from './backend/client'
import { useBackendState } from './backend/useBackendState'
import { engagementBackend } from './backend/engagement'
import { quickPages } from './backend/engagement'
import type { DashboardOverview, NotificationOverview, SearchResult } from './backend/engagement'
import { communityBackend } from './backend/community'
import { clampToBytes } from './limits'
import { localId } from './ids'
import { reportsBackend } from './backend/reports'
import type { FocusSession } from './backend/reports'
import type { PracticeTarget } from './backend/practice'

const StudyRoom = lazy(() => import('./pages/StudyRoom'))
const Library = lazy(() => import('./pages/Library'))
const PlanGenerator = lazy(() => import('./pages/PlanGenerator'))
const AgentCenter = lazy(() => import('./pages/AgentCenter'))
const Integrations = lazy(() => import('./pages/Integrations'))
const ProviderHub = lazy(() => import('./pages/ProviderHub'))

const navGroups: { label: string; items: { id: PageId; label: string; icon: LucideIcon }[] }[] = [
  { label: '学习', items: [{ id: 'today', label: '今日', icon: Home }, { id: 'generator', label: 'AI 方案生成', icon: Sparkles }, { id: 'plan', label: '学习计划', icon: CalendarDays }, { id: 'study', label: '学习空间', icon: BookOpen }, { id: 'map', label: '知识地图', icon: Map }, { id: 'agent', label: 'Agent 助手', icon: Brain }] },
  { label: '巩固', items: [{ id: 'practice', label: '练习测评', icon: ListChecks }, { id: 'review', label: '记忆复习', icon: Layers3 }, { id: 'library', label: '笔记与 Wiki', icon: LibraryBig }] },
  { label: '成长', items: [{ id: 'projects', label: '项目作品', icon: FolderKanban }, { id: 'reports', label: '学习报告', icon: TrendingUp }, { id: 'community', label: '学习社区', icon: Users }, { id: 'providers', label: '模型供应商', icon: Cloud }, { id: 'integrations', label: '插件与事件', icon: Blocks }] },
]
const allNav = navGroups.flatMap(group => group.items)

function BrandMark() {
  return <svg viewBox="0 0 36 36" aria-hidden="true" className="brand-mark"><path d="M7 9.5 18 5l11 4.5v15L18 30 7 24.5Z" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="m7 9.5 11 5 11-5M18 14.5V30M11.5 18.5l6.5 3 6.5-3" fill="none" stroke="currentColor" strokeWidth="1.8"/><path d="m23 7 2-3 2 3" fill="none" stroke="currentColor" strokeWidth="1.8"/></svg>
}

export default function App() {
  const initialPage = allNav.some(item => item.id === window.location.hash.slice(1)) || ['settings', 'shared'].includes(window.location.hash.slice(1)) ? window.location.hash.slice(1) as PageId : 'today'
  const [page, setPage] = useState<PageId>(initialPage)
  const { snapshot, ready, startupError, loginRequired, replaceCollection, resetApplication, importBackup, importNotes } = useBackendState()
  const { tasks, notes, resources, projects, preferences } = snapshot
  // 24#5：集合 setter 支持更新函数（以最新集合为基准），消除一帧双写的静默丢失
  const setTasks = (value: Task[] | ((current: Task[]) => Task[])) => replaceCollection('tasks', value)
  const setNotes = (value: AppSnapshot['notes'] | ((current: AppSnapshot['notes']) => AppSnapshot['notes'])) => replaceCollection('notes', value)
  const setProjects = (value: Project[] | ((current: Project[]) => Project[])) => replaceCollection('projects', value)
  const setPreferences = (value: AppSnapshot['preferences'] | ((current: AppSnapshot['preferences']) => AppSnapshot['preferences'])) => replaceCollection('preferences', value)
  const [mobileNav, setMobileNav] = useState(false)
  const [studyTarget, setStudyTarget] = useState<{ planId: string; stageId: string; nonce: number } | null>(null)
  const [practiceTarget, setPracticeTarget] = useState<PracticeTarget | null>(null)
  const [mapPlanId, setMapPlanId] = useState<string | null>(null)
  const [tutorOpen, setTutorOpen] = useState(false)
  const [tutorContext, setTutorContext] = useState<{ planId: string; label: string } | null>(null)
  const [modal, setModal] = useState<'search' | 'quick' | 'notifications' | 'tasks' | null>(null)
  // A-10：多槽 toast——两个并发后端错误后到不再覆盖先到
  const [toasts, setToasts] = useState<{ id: number; message: string; tone: 'info' | 'error' }[]>([])
  const [quickType, setQuickType] = useState('任务')
  const [quickTitle, setQuickTitle] = useState('')
  const [focusOpen, setFocusOpen] = useState(false)
  const [focusSession, setFocusSession] = useState<FocusSession | null>(null)
  // A-19：从任务行进入专注时携带的任务 id（无关联专注为 null）
  const focusTaskRef = useRef<number | null>(null)
  const [focusBusy, setFocusBusy] = useState(false)
  const [notifications, setNotifications] = useState<NotificationOverview | null>(null)
  const [notificationsError, setNotificationsError] = useState('')
  const [homeSummary, setHomeSummary] = useState<DashboardOverview | null>(null)
  const tutorRef = useRef<HTMLElement>(null)
  // 导师抽屉为 aria-modal 弹层：焦点必须圈禁在内（09#5）；
  // 专注层的圈禁在其组件内部接线（14#10）
  useFocusContainment(tutorRef, tutorOpen)

  const notify = (message: string) => setToasts(current => [...current, { id: Date.now() + Math.random(), message, tone: 'info' as const }].slice(-4))
  const dismissToast = (id: number) => setToasts(current => current.filter(toast => toast.id !== id))
  // 浏览器前进/后退或手动改 hash 时同步路由（navigate 只单向写 hash）
  useEffect(() => {
    const sync = () => {
      const target = window.location.hash.slice(1)
      if (allNav.some(item => item.id === target) || ['settings', 'shared'].includes(target)) setPage(current => (current === target ? current : target as PageId))
    }
    window.addEventListener('hashchange', sync)
    window.addEventListener('popstate', sync)
    return () => { window.removeEventListener('hashchange', sync); window.removeEventListener('popstate', sync) }
  }, [])
  // 退出只读分享页：清掉地址栏 share 令牌并回到主界面
  const exitShared = useCallback(() => {
    const url = new URL(window.location.href)
    if (!url.searchParams.has('share') && url.hash !== '#shared') return
    url.searchParams.delete('share')
    if (url.hash === '#shared') url.hash = ''
    window.history.replaceState(null, '', url.pathname + url.search + url.hash)
    setPage(previous => (previous === 'shared' ? 'today' : previous))
  }, [])
  useEffect(() => {
    // 后端错误以错误样式（alert）播报，与成功提示区分，不再共用绿色对勾
    const onError = (event: Event) => setToasts(current => [...current, { id: Date.now() + Math.random(), message: (event as CustomEvent<string>).detail, tone: 'error' as const }].slice(-4))
    window.addEventListener('backend-error', onError)
    return () => window.removeEventListener('backend-error', onError)
  }, [])
  // 32#15：每条 toast 独立计时——原先依赖整个数组，任一新提示会重建全部
  // 计时器，旧提示的剩余存活时间被重置为满时长；以已登记集合保证每条只排一次
  const scheduledToasts = useRef<Set<number>>(new Set())
  useEffect(() => {
    for (const toast of toasts) {
      if (scheduledToasts.current.has(toast.id)) continue
      scheduledToasts.current.add(toast.id)
      window.setTimeout(() => {
        scheduledToasts.current.delete(toast.id)
        setToasts(current => current.filter(item => item.id !== toast.id))
      }, toast.tone === 'error' ? 5200 : 3200)
    }
  }, [toasts])
  useEffect(() => {
    if (!ready || loginRequired) return
    let active = true
    let stop: (() => void) | undefined
    const fail = (error: unknown) => {
      if (active) { setNotificationsError(backendErrorMessage(error)); reportBackendError(error) }
    }
    const load = async () => {
      try {
        const next = await engagementBackend.notifications()
        if (active) { setNotifications(next); setNotificationsError('') }
      } catch (error) { fail(error) }
    }
    void load()
    void onBackendEvent(event => {
      if (active && ['notifications.', 'review.', 'community.', 'plan.', 'study.checkin'].some(prefix => event.kind.startsWith(prefix))) void load()
    }).then(unlisten => { if (active) stop = unlisten; else unlisten() }).catch(fail)
    const timer = window.setInterval(() => { void load() }, 30000)
    return () => { active = false; stop?.(); window.clearInterval(timer) }
  }, [ready, loginRequired])
  const [homeSummaryError, setHomeSummaryError] = useState('')
  const [summaryReload, setSummaryReload] = useState(0)
  useEffect(() => {
    if (!ready || loginRequired) return
    let active = true
    let stop: (() => void) | undefined
    const load = async () => {
      // 摘要全局只加载这一份：侧栏与"今日"页共用，前缀取两处需求并集，避免重复请求
      const next = await engagementBackend.dashboard()
      if (active) { setHomeSummary(next); setHomeSummaryError('') }
    }
    void load().catch(error => { if (active) { setHomeSummaryError(backendErrorMessage(error)); reportBackendError(error) } })
    void onBackendEvent(event => {
      if (active && ['state.changed', 'plan.', 'study.checkin', 'security.session_started', 'focus.', 'practice.', 'review.'].some(prefix => event.kind.startsWith(prefix))) void load().catch(error => { if (active) { setHomeSummaryError(backendErrorMessage(error)); reportBackendError(error) } })
    }).then(unlisten => { if (active) stop = unlisten; else unlisten() }).catch(reportBackendError)
    return () => { active = false; stop?.() }
  }, [ready, loginRequired, summaryReload])
  useEffect(() => {
    // 社区预约提醒：后端只在被领取时投递（置 delivered_at 并发 community.reminder_due），
    // 应用打开期间每分钟领取一次到期提醒，触发通知中心刷新与弹层提示
    if (!ready || loginRequired) return
    let active = true
    const claim = async () => {
      try {
        const due = await communityBackend.claimDueReminders()
        if (active && due.length) notify(`预约「${due[due.length - 1].title}」即将开始`)
      } catch { /* 尽力而为的提醒轮询：失败时等待下一轮，不打断主界面 */ }
    }
    void claim()
    const timer = window.setInterval(() => { void claim() }, 60000)
    return () => { active = false; window.clearInterval(timer) }
  }, [ready, loginRequired])
  useEffect(() => {
    document.documentElement.dataset.theme = preferences.theme
    document.documentElement.classList.toggle('large-text', preferences.largeText)
    document.documentElement.classList.toggle('high-contrast', preferences.highContrast)
    document.documentElement.classList.toggle('reduce-motion', preferences.reducedMotion)
  }, [preferences])
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        // 14#4：专注层 z90 完全遮挡搜索模态（z70），此时打开只会劫持键盘焦点
        if (!focusOpen) { event.preventDefault(); setModal('search') }
      }
      if (event.key === 'Escape') {
        // 24#2：Esc 分层仲裁——Modal（含确认框）打开时交给弹层栈处理最顶层，
        // 专注层次之，最后才收起抽屉/移动导航/共享页，一次 Esc 只关一层
        if (document.querySelector('.overlay')) return
        if (focusOpen) { setFocusOpen(false); return }
        if (tutorOpen) { setTutorOpen(false); return }
        setMobileNav(false); exitShared()
      }
    }
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler)
  }, [exitShared, focusOpen, tutorOpen])
  const openFocus = (taskId?: number) => {
    // A-19：从任务行进入时可携带任务 id，专注秒计入任务桶参与取大——
    // 否则「自由专注 + 勾选任务」双来源双计（03#20 原场景）
    focusTaskRef.current = taskId ?? null
    setFocusOpen(true)
    void reportsBackend.focus().then(overview => {
      setFocusSession(overview.active)
    }).catch(reportBackendError)
  }
  const toggleFocus = async () => {
    if (focusBusy) return
    setFocusBusy(true)
    try {
      const next = focusSession?.status === 'running' ? await reportsBackend.pauseFocus() : focusSession?.status === 'paused' ? await reportsBackend.resumeFocus() : await reportsBackend.startFocus(focusTaskRef.current, 25 * 60)
      setFocusSession(next)
    } catch (error) { reportBackendError(error, 'write') } finally { setFocusBusy(false) }
  }
  const finishFocus = async (cancelled: boolean): Promise<FocusSession | null> => {
    if (!focusSession || focusBusy) return null
    setFocusBusy(true)
    try {
      const done = cancelled ? await reportsBackend.cancelFocus() : await reportsBackend.completeFocus()
      setFocusSession(done)
      focusTaskRef.current = null
      notify(`${cancelled ? '已取消并保存' : '已完成'} ${done.actualSeconds} 秒真实专注`)
      return done
    } catch (error) {
      // 19#2：会话已在别处收尾（并发完成/重启恢复）——本地作废，避免
      // 计时层每秒重试造成 1Hz 错误风暴
      const code = typeof error === 'object' && error !== null && 'code' in error ? String((error as { code?: unknown }).code) : ''
      if (code === 'no_active_focus') {
        setFocusSession(null)
        focusTaskRef.current = null
        notify('专注会话已结束')
        return null
      }
      reportBackendError(error, 'write')
      return null
    } finally { setFocusBusy(false) }
  }
  const resetFocus = async () => {
    if (focusSession && ['running', 'paused'].includes(focusSession.status) && !(await finishFocus(true))) return
    focusTaskRef.current = null
    setFocusSession(null)
  }
  const navigate = (target: PageId) => {
    setPage(target); window.location.hash = target; setMobileNav(false); setTutorOpen(false)
    // 24#7：系统级 prefers-reduced-motion 同样禁用平滑滚动——显式 behavior
    // 会覆盖 CSS 的 scroll-behavior，只看应用内偏好会让系统设置失效
    const systemReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    window.scrollTo({ top: 0, behavior: preferences.reducedMotion || systemReduced ? 'auto' : 'smooth' })
  }
  const addQuick = async () => {
    if (!quickTitle.trim()) return
    try {
      if (quickType === '任务') await setTasks(current => [...current, { id: localId(), title: quickTitle.trim(), subject: '快速记录', duration: 20, done: false, kind: '学习' }])
      // 19#3：updated 持久化 ISO 时间戳（渲染层格式化为相对时间），不再写入
      // 相对时间文案「刚刚」——那会让笔记永远显示"刚刚"
      else await setNotes(current => [{ id: localId(), title: quickTitle.trim(), excerpt: '通过全局快速记录创建。', tag: '复盘', updated: new Date().toISOString(), content: quickTitle.trim() }, ...current])
      notify(`${quickType}已保存`); setQuickTitle(''); setModal(null)
    } catch (error) { reportBackendError(error, 'write') }
  }
  const importAll = (path: string) => importBackup(path)
  const resetAll = async () => {
    const next = await resetApplication()
    notify('本地任务、笔记、资料、项目、草稿和偏好已清空')
    return next
  }
  const openSearchResult = (result: SearchResult) => {
    if (result.planId && result.stageId) {
      setStudyTarget({ planId: result.planId, stageId: result.stageId, nonce: Date.now() })
      navigate('study')
    } else {
      navigate(result.page)
    }
    setModal(null)
  }
  const markAllRead = async () => {
    try {
      setNotifications(await engagementBackend.markAllRead())
      setModal(null)
      notify('通知已全部标为已读')
    } catch (error) { reportBackendError(error, 'write') }
  }
  const readNotification = async (id: string, page: PageId) => {
    try {
      setNotifications(await engagementBackend.markRead(id))
      setModal(null)
      navigate(page)
    } catch (error) { reportBackendError(error, 'write') }
  }
  const renderPage = () => {
    switch (page) {
      case 'today': return <Dashboard tasks={tasks} setTasks={setTasks} onNavigate={navigate} onFocus={openFocus} notify={notify} overview={homeSummary} summaryError={homeSummaryError} onRetrySummary={() => setSummaryReload(current => current + 1)} />
      case 'generator': return <PlanGenerator onNavigate={navigate} notify={notify} />
      case 'plan': return <Plan setTasks={setTasks} onNavigate={navigate} notify={notify} />
      case 'agent': return <AgentCenter notify={notify} />
      case 'study': return <StudyRoom target={studyTarget} onFocus={() => setFocusOpen(true)} onNavigate={navigate} onTutorContext={setTutorContext} notify={notify} />
      case 'map': return <KnowledgeMap onNavigate={navigate} notify={notify} initialPlanId={mapPlanId} onStudy={(planId, stageId) => { setStudyTarget({ planId, stageId, nonce: Date.now() }); navigate('study') }} onPractice={(planId, stageId, nodeId) => { setPracticeTarget({ planId, stageId, nodeId, nonce: Date.now() }); navigate('practice') }} />
      case 'practice': return <Practice notify={notify} target={practiceTarget} onTargetConsumed={() => setPracticeTarget(null)} />
      case 'review': return <Review notify={notify} />
      case 'library': return <Library notes={notes} setNotes={setNotes} resources={resources} onImportNotes={importNotes} notify={notify} />
      case 'projects': return <Projects projects={projects} setProjects={setProjects} onNavigateMap={planId => { setMapPlanId(planId); navigate('map') }} notify={notify} />
      case 'reports': return <Reports notify={notify} onNavigate={navigate} />
      case 'community': return <Community notify={notify} />
      case 'integrations': return <Integrations notify={notify} />
      case 'providers': return <ProviderHub notify={notify} />
      case 'settings': return <Settings preferences={preferences} setPreferences={setPreferences} onImport={importAll} counts={{ tasks: tasks.length, notes: notes.length, resources: resources.length, projects: projects.length }} onReset={resetAll} notify={notify} />
      case 'shared': return <SharedPlan token={new URLSearchParams(window.location.search).get('share')} />
    }
  }

  if (page === 'shared') {
    return <div className="shared-screen">
      <header className="shared-topbar"><BrandMark /><strong>知舟 · 只读分享预览</strong><Button variant="secondary" size="sm" onClick={exitShared}><X size={15} />返回应用</Button></header>
      <SharedPlan token={new URLSearchParams(window.location.search).get('share')} />
    </div>
  }

  if (startupError) {
    return <main className="page-loading startup-recovery"><h1>本地数据或实时连接打开失败</h1><p role="alert">{startupError}</p><DataDirectoryPanel /><ToastStack toasts={toasts} onClose={dismissToast} /></main>
  }
  if (loginRequired) {
    return <main className="page-loading access-gate"><h1>选择本地用户</h1><p>会话已过期或尚未建立。请选择本地身份，继续进入对应的学习工作区。</p><div className="surface access-gate-panel"><SecurityPanel notify={notify} /></div><ToastStack toasts={toasts} onClose={dismissToast} /></main>
  }
  if (!ready) {
    return <main className="page-loading"><LoaderMark />正在打开本地学习数据…<ToastStack toasts={toasts} onClose={dismissToast} /></main>
  }

  return <div className={`app-shell ${page === 'study' ? 'study-mode' : ''}`}>
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); document.getElementById('main-content')?.focus() }}>跳到主要内容</a>
    {mobileNav && <button className="nav-backdrop" aria-label="关闭导航" onClick={() => setMobileNav(false)} />}
    <aside className={`sidebar ${mobileNav ? 'is-open' : ''}`}>
      <div className="brand"><BrandMark /><div><strong>知舟</strong><span>DEEP TUTOR</span></div><IconButton label="关闭导航" className="mobile-close" onClick={() => setMobileNav(false)}><X size={19} /></IconButton></div>
      <button className="goal-switch" onClick={() => navigate('plan')}><span><Target size={17} /></span><div><small>当前目标</small><strong>{homeSummary?.goal || '尚未设置学习目标'}</strong></div><ChevronDown size={15} /></button>
      <nav className="main-nav" aria-label="主导航">{navGroups.map(group => <div className="nav-group" key={group.label}><span className="nav-label">{group.label}</span>{group.items.map(item => <button key={item.id} className={page === item.id ? 'active' : ''} onClick={() => navigate(item.id)}><item.icon size={18} /><span>{item.label}</span>{item.id === 'review' && notifications && notifications.unreadCount > 0 && <i>{notifications.unreadCount}</i>}</button>)}</div>)}</nav>
      <div className="sidebar-bottom"><button className={page === 'settings' ? 'active' : ''} onClick={() => navigate('settings')}><SettingsIcon size={18} /><span>设置与偏好</span></button><button onClick={() => setModal('tasks')}><Zap size={18} /><span>Agent 任务中心</span></button><div className="sidebar-profile"><span>{(homeSummary?.name || '本').slice(0, 1)}</span><div><strong>{homeSummary?.name || '本地学习者'}</strong><small>连续学习 {homeSummary?.streak ?? 0} 天</small></div><button aria-label="个人设置" onClick={() => navigate('settings')}><MoreHorizontal size={18} /></button></div></div>
    </aside>

    <div className="app-main">
      <header className="topbar"><div className="topbar-left"><IconButton label="打开导航" className="mobile-menu" onClick={() => setMobileNav(true)}><Menu size={20} /></IconButton><button className="global-search" onClick={() => setModal('search')}><Search size={17} /><span>搜索课程、笔记或知识节点…</span><kbd>⌘ K</kbd></button></div><div className="topbar-actions"><button className="quick-add" onClick={() => setModal('quick')}><Plus size={18} /><span>快速记录</span></button><IconButton label={preferences.theme === 'light' ? '切换深色模式' : '切换明亮模式'} onClick={() => setPreferences(current => ({ ...current, theme: current.theme === 'light' ? 'dark' : 'light' }))}  /* 35#5：updater 形态——渲染闭包整对象会被排队写入整体覆盖（32#13 同型） */>{preferences.theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}</IconButton><IconButton label="通知" className="notification-button" onClick={() => setModal('notifications')}><Bell size={18} />{notifications && notifications.unreadCount > 0 && <i>{notifications.unreadCount}</i>}</IconButton><Button variant="secondary" size="sm" className="ask-agent" onClick={() => setTutorOpen(true)}><Sparkles size={16} />问知舟</Button></div></header>
      <main id="main-content" className="main-content" tabIndex={-1}><Suspense fallback={<div className="page-loading"><LoaderMark />正在加载学习工作区…</div>}>{renderPage()}</Suspense></main>
    </div>

    <nav className="mobile-bottom-nav" aria-label="移动端快捷导航">{[{ id: 'today' as PageId, label: '今日', icon: Home }, { id: 'study' as PageId, label: '学习', icon: BookOpen }, { id: 'review' as PageId, label: '复习', icon: Layers3 }, { id: 'library' as PageId, label: '笔记', icon: LibraryBig }].map(item => <button key={item.id} className={page === item.id ? 'active' : ''} onClick={() => navigate(item.id)}><item.icon size={20} /><span>{item.label}</span></button>)}<button onClick={() => setMobileNav(true)}><Menu size={20} /><span>全部</span></button></nav>

    {tutorOpen && <div className="drawer-overlay" onMouseDown={e => { if (e.target === e.currentTarget) setTutorOpen(false) }}><aside className="global-tutor" role="dialog" aria-modal="true" aria-label="知舟全局导师" tabIndex={-1} ref={tutorRef}><TutorPanel compact onClose={() => setTutorOpen(false)} planId={tutorContext?.planId} contextLabel={tutorContext?.label} hintsOnly={preferences.hintsOnly} /></aside></div>}

    <SearchLayer open={modal === 'search'} onClose={() => setModal(null)} onOpenResult={openSearchResult} />
    <Modal open={modal === 'quick'} onClose={() => setModal(null)} title="快速记录">
      <div className="form-stack"><div className="segmented">{['任务', '笔记'].map(item => <button className={quickType === item ? 'active' : ''} key={item} onClick={() => setQuickType(item)}>{item}</button>)}</div><label>{quickType === '任务' ? '要做什么？' : '想记下什么？'}<input autoFocus value={quickTitle} onChange={e => setQuickTitle(clampToBytes(e.target.value, 500))} onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) addQuick() }} placeholder={quickType === '任务' ? '例如：复习递归错题' : '例如：先验概率决定判断的起点'} /></label><p className="form-help">{quickType === '任务' ? '将加入今日任务，之后可在学习计划中调整。' : '将保存到笔记资料库，标签为「复盘」。'}</p><Button onClick={addQuick} disabled={!quickTitle.trim()}>保存{quickType}</Button></div>
    </Modal>
    <Modal open={modal === 'notifications'} onClose={() => setModal(null)} title="通知与提醒">
      {notificationsError && <div className="form-error" role="alert"><span>{notificationsError}</span><Button variant="ghost" size="sm" onClick={() => { setNotificationsError(''); void engagementBackend.notifications().then(next => { setNotifications(next); setNotificationsError('') }).catch(error => { setNotificationsError(backendErrorMessage(error)); reportBackendError(error) }) }}>重新读取</Button></div>}
      {notifications?.suppressed && <p className="form-help">当前处于免打扰时段；通知仍保留在中心，不主动弹出提醒。</p>}
      <div className="notification-list">{notifications?.items.map(item => <button key={item.id} onClick={() => void readNotification(item.id, item.page)}><span className="notification-icon"><Bell size={18} /></span><div><strong>{item.title}</strong><p>{item.body}</p><small>{new Date(item.createdAt).toLocaleString()} · {item.readAt ? '已读' : '未读'}</small></div></button>)}{notifications && !notifications.items.length && <div className="search-empty">当前没有真实通知。到期复习、计划和预约会在这里出现。</div>}{!notifications && !notificationsError && <div className="search-empty">正在读取真实通知…</div>}</div><Button variant="ghost" disabled={!notifications || notifications.unreadCount === 0} onClick={() => void markAllRead()}><Check size={16} />全部标为已读</Button>
    </Modal>
    <Modal open={modal === 'tasks'} onClose={() => setModal(null)} title="Agent 任务中心">
      <AgentTaskCenter notify={notify} />
    </Modal>

    {focusOpen && <FocusLayer
      session={focusSession}
      tasks={tasks}
      busy={focusBusy}
      onToggle={() => void toggleFocus()}
      onFinish={finishFocus}
      onReset={() => void resetFocus()}
      onExit={() => setFocusOpen(false)}
    />}
    <ToastStack toasts={toasts} onClose={dismissToast} />
  </div>
}

function LoaderMark() { return <span className="loader-mark" aria-hidden="true" /> }

function SearchResultIcon({ kind }: { kind: string }) {
  const icons: Record<string, LucideIcon> = { note: FileText, material: BookOpen, node: Map, plan: CalendarDays, task: ListChecks, project: FolderKanban }
  const Icon = icons[kind] || Search
  return <Icon size={18} />
}

function Highlight({ text, query }: { text: string; query: string }) {
  if (!query.trim()) return <>{text}</>
  const parts = text.split(new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'ig'))
  return <>{parts.map((part, index) => part.toLowerCase() === query.toLowerCase() ? <mark key={index}>{part}</mark> : part)}</>
}

// 多槽错误/成功提示堆叠（A-10）：并发后端错误各自可见，互不覆盖
function ToastStack({ toasts, onClose }: { toasts: { id: number; message: string; tone: 'info' | 'error' }[]; onClose: (id: number) => void }) {
  if (!toasts.length) return null
  return <div className="toast-stack">{toasts.map(toast => <div key={toast.id} className={`toast ${toast.tone === 'error' ? 'toast-error' : ''}`} role={toast.tone === 'error' ? 'alert' : 'status'}>{toast.tone === 'error' ? <CircleAlert size={17} /> : <Check size={17} />}<span>{toast.message}</span><button aria-label="关闭提示" onClick={() => onClose(toast.id)}><X size={15} /></button></div>)}</div>
}

// 专注层独立组件（14#10）：计时器每秒只重渲染本层，
// 不再驱动整个 App reconcile；焦点圈禁也在此接线
function FocusLayer({ session, tasks, busy, onToggle, onFinish, onReset, onExit }: {
  session: FocusSession | null
  tasks: Task[]
  busy: boolean
  onToggle: () => void
  onFinish: (cancelled: boolean) => Promise<FocusSession | null>
  onReset: () => void
  onExit: () => void
}) {
  const [seconds, setSeconds] = useState(session?.remainingSeconds ?? 25 * 60)
  const ref = useRef<HTMLDivElement>(null)
  // 19#2：自动收尾只尝试一次——完成请求持久性失败时不再每秒重试+
  // 每秒错误 toast（1Hz 风暴），失败后由手动按钮接管；新会话重置标记
  const autoFinishedRef = useRef(false)
  useFocusContainment(ref, true)
  useEffect(() => {
    setSeconds(session ? session.remainingSeconds : 25 * 60)
    autoFinishedRef.current = false
  }, [session])
  useEffect(() => {
    if (session?.status !== 'running') return
    const tick = () => {
      const remaining = Math.max(0, session.plannedSeconds - Math.floor((Date.now() - Date.parse(session.startedAt)) / 1000))
      setSeconds(remaining)
      if (remaining === 0 && !busy && !autoFinishedRef.current) {
        autoFinishedRef.current = true
        void onFinish(false)
      }
    }
    tick()
    const timer = window.setInterval(tick, 1000)
    return () => window.clearInterval(timer)
  }, [session, busy, onFinish])
  const minutes = Math.floor(seconds / 60).toString().padStart(2, '0')
  const display = (seconds % 60).toString().padStart(2, '0')
  return <div className="focus-overlay" role="dialog" aria-modal="true" aria-label="专注学习" tabIndex={-1} ref={ref}><div className="focus-top"><div><BrandMark /><span>知舟 · 专注空间</span></div><IconButton label="退出专注" onClick={onExit}><X size={22} /></IconButton></div><div className="focus-content"><p>当前任务</p><h2>{session?.taskId ? tasks.find(task => task.id === session.taskId)?.title || `任务 #${session.taskId}` : '自由专注'}</h2><div className="focus-timer"><span>{minutes}</span><i>:</i><span>{display}</span></div><div className="focus-actions"><Button variant="light" size="lg" disabled={busy} onClick={onToggle}>{session?.status === 'running' ? <Pause size={20} /> : <Play size={20} />}{session?.status === 'running' ? '暂停' : session?.status === 'paused' ? '继续专注' : '开始专注'}</Button><Button variant="light" disabled={busy || !session || !['running', 'paused'].includes(session.status)} onClick={() => void onFinish(false)}>结束并保存</Button><IconButton label="取消当前专注并重置" disabled={busy} onClick={onReset}><TimerReset size={21} /></IconButton></div><span className="focus-quote">一次只做好一件事。计时、暂停和完成都由原生后端保存。</span></div><button className="focus-exit" onClick={onExit}>Esc 退出专注</button></div>
}

// 全局搜索独立组件（14#11）：输入 state 只重渲染弹层，
// 击键不再整页 reconcile
function SearchLayer({ open, onClose, onOpenResult }: {
  open: boolean
  onClose: () => void
  onOpenResult: (result: SearchResult) => void
}) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState('')
  // 20#9：键盘导航——↵ 角标暗示 Enter 打开，补上下方向键选择与高亮
  const [activeIndex, setActiveIndex] = useState(0)
  useEffect(() => {
    let active = true
    // 空查询直接给本地页面快捷入口，不发 IPC；有词时 120ms 去抖后走后端全文检索
    if (!query.trim()) { setResults(quickPages); setSearching(false); setError(''); return }
    const timer = window.setTimeout(() => {
      setSearching(true)
      setError('')
      void engagementBackend.search(query).then(next => {
        if (active) setResults(next)
      }).catch(cause => {
        if (active) { setError(backendErrorMessage(cause)); reportBackendError(cause) }
      }).finally(() => { if (active) setSearching(false) })
    }, 120)
    return () => { active = false; window.clearTimeout(timer) }
  }, [query])
  useEffect(() => { setActiveIndex(0) }, [results])
  const openResult = (result: SearchResult) => { setQuery(''); onOpenResult(result) }
  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (!results.length) return
    if (event.key === 'ArrowDown') { event.preventDefault(); setActiveIndex(index => (index + 1) % results.length) }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActiveIndex(index => (index - 1 + results.length) % results.length) }
    else if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); openResult(results[activeIndex] ?? results[0]) }
  }
  return <Modal open={open} onClose={() => { onClose(); setQuery('') }} title="全局搜索">
    <div className="command-search"><Search size={20} /><input autoFocus value={query} onChange={e => setQuery(clampToBytes(e.target.value, 200))} onKeyDown={onKeyDown} placeholder="输入页面、笔记、任务或知识节点…" /></div><div className="command-results">{results.length ? results.map((result, index) => <button key={`${result.kind}-${result.id}`} className={index === activeIndex ? 'active' : ''} onMouseEnter={() => setActiveIndex(index)} onClick={() => openResult(result)}><SearchResultIcon kind={result.kind} /><div><strong><Highlight text={result.title} query={query} /></strong><span>{result.subtitle}</span></div><span>↵</span></button>) : <div className="search-empty" role={error ? 'alert' : undefined}>{searching ? '正在检索真实工作区…' : error || '没有找到匹配结果，请尝试其他关键词。'}</div>}</div><div className="command-hint"><Command size={14} />支持搜索功能、笔记、资料、方案、任务、项目与知识节点；↑↓ 选择、↵ 打开、Esc 关闭</div>
  </Modal>
}

import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { Bell, Blocks, BookOpen, Brain, CalendarDays, Check, ChevronDown, CircleHelp, Cloud, Command, FileText, FolderKanban, Home, Layers3, LibraryBig, ListChecks, Map, Menu, MessageSquareText, Moon, MoreHorizontal, Pause, Play, Plus, Search, Settings as SettingsIcon, Sparkles, Sun, Target, TimerReset, TrendingUp, Users, X, Zap } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import Dashboard from './pages/Dashboard'
import Plan from './pages/Plan'
import StudyRoom from './pages/StudyRoom'
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
import { Button, IconButton, Modal } from './components/UI'
import { knowledgeNodes } from './data'
import type { PageId, Project, Task } from './data'
import { backend, reportBackendError } from './backend/client'
import type { AppSnapshot } from './backend/client'
import { useBackendState } from './backend/useBackendState'
import { communityBackend } from './backend/community'

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
  const { snapshot, ready, startupError, replaceCollection, resetApplication, importBackup } = useBackendState()
  const { tasks, notes, resources, projects, preferences } = snapshot
  const setTasks = (value: Task[]) => replaceCollection('tasks', value)
  const setNotes = (value: AppSnapshot['notes']) => replaceCollection('notes', value)
  const setProjects = (value: Project[]) => replaceCollection('projects', value)
  const setPreferences = (value: AppSnapshot['preferences']) => replaceCollection('preferences', value)
  const [mobileNav, setMobileNav] = useState(false)
  const [studyTarget, setStudyTarget] = useState<{ planId: string; stageId: string } | null>(null)
  const [tutorOpen, setTutorOpen] = useState(false)
  const [tutorContext, setTutorContext] = useState<{ planId: string; label: string } | null>(null)
  const [modal, setModal] = useState<'search' | 'quick' | 'notifications' | 'tasks' | null>(null)
  const [toast, setToast] = useState('')
  const [query, setQuery] = useState('')
  const [quickType, setQuickType] = useState('任务')
  const [quickTitle, setQuickTitle] = useState('')
  const [focusOpen, setFocusOpen] = useState(false)
  const [focusSeconds, setFocusSeconds] = useState(25 * 60)
  const [focusRunning, setFocusRunning] = useState(false)

  const notify = (message: string) => setToast(message)
  useEffect(() => {
    const onError = (event: Event) => notify((event as CustomEvent<string>).detail)
    window.addEventListener('backend-error', onError)
    return () => window.removeEventListener('backend-error', onError)
  }, [])
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(''), 3200); return () => window.clearTimeout(timer) }, [toast])
  useEffect(() => {
    if (!ready) return
    let active = true
    const poll = async () => {
      try {
        const reminders = await communityBackend.claimDueReminders()
        if (active) reminders.forEach(reminder => notify(`学习预约「${reminder.title}」：${new Date(reminder.startAt).toLocaleString()} 开始`))
      } catch (error) { reportBackendError(error) }
    }
    void poll()
    const timer = window.setInterval(() => { void poll() }, 30000)
    return () => { active = false; window.clearInterval(timer) }
  }, [ready])
  useEffect(() => {
    document.documentElement.dataset.theme = preferences.theme
    document.documentElement.classList.toggle('large-text', preferences.largeText)
    document.documentElement.classList.toggle('high-contrast', preferences.highContrast)
    document.documentElement.classList.toggle('reduce-motion', preferences.reducedMotion)
  }, [preferences])
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setModal('search') }
      if (event.key === 'Escape') { setMobileNav(false); setTutorOpen(false); setFocusOpen(false); setFocusRunning(false) }
    }
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler)
  }, [])
  useEffect(() => {
    if (!focusRunning) return
    const timer = window.setInterval(() => setFocusSeconds(prev => { if (prev <= 1) { setFocusRunning(false); notify('本次专注完成，休息一下吧'); return 0 } return prev - 1 }), 1000)
    return () => window.clearInterval(timer)
  }, [focusRunning])
  const navigate = (target: PageId) => { setPage(target); window.location.hash = target; setMobileNav(false); setTutorOpen(false); window.scrollTo({ top: 0, behavior: preferences.reducedMotion ? 'auto' : 'smooth' }) }
  const addQuick = async () => {
    if (!quickTitle.trim()) return
    try {
      if (quickType === '任务') await setTasks([...tasks, { id: Date.now(), title: quickTitle.trim(), subject: '快速记录', duration: 20, done: false, kind: '学习' }])
      else await setNotes([{ id: Date.now(), title: quickTitle.trim(), excerpt: '通过全局快速记录创建。', tag: '复盘', updated: '刚刚', content: quickTitle.trim() }, ...notes])
      notify(`${quickType}已保存`); setQuickTitle(''); setModal(null)
    } catch (error) { reportBackendError(error) }
  }
  const importAll = (path: string) => importBackup(path)
  const resetAll = async () => {
    const next = await resetApplication()
    notify('本地任务、笔记、资料、项目、草稿和偏好已清空')
    return next
  }
  const searchResults = useMemo(() => {
    const q = query.trim().toLowerCase()
    const plans = q ? ['贝叶斯学习方案', '计算机考研学习计划'].filter(name => name.toLowerCase().includes(q)).map(name => ({ id: 'plan' as PageId, title: name, subtitle: '学习方案 · 深链跳转', icon: CalendarDays })) : []
    const foundNotes = notes.filter(note => q && `${note.title}${note.content}`.toLowerCase().includes(q)).map(note => ({ id: 'library' as PageId, title: note.title, subtitle: `笔记 · ${note.tag}`, icon: FileText }))
    const docs = resources.filter(resource => q && resource.title.toLowerCase().includes(q)).map(resource => ({ id: 'library' as PageId, title: resource.title, subtitle: `KB 文档 · ${resource.type}`, icon: BookOpen }))
    const nodes = knowledgeNodes.filter(node => q && `${node.label}${node.description}`.toLowerCase().includes(q)).map(node => ({ id: 'map' as PageId, title: node.label, subtitle: `知识节点 · 掌握度 ${node.mastery}%`, icon: Map }))
    const pages = allNav.filter(item => !q || item.label.toLowerCase().includes(q)).map(item => ({ id: item.id, title: item.label, subtitle: '功能页面', icon: item.icon }))
    return [...plans, ...foundNotes, ...docs, ...nodes, ...pages].slice(0, 12)
  }, [query, notes, resources])
  const minutes = Math.floor(focusSeconds / 60).toString().padStart(2, '0')
  const seconds = (focusSeconds % 60).toString().padStart(2, '0')

  const renderPage = () => {
    switch (page) {
      case 'today': return <Dashboard tasks={tasks} setTasks={setTasks} onNavigate={navigate} onFocus={() => setFocusOpen(true)} notify={notify} />
      case 'generator': return <PlanGenerator onNavigate={navigate} notify={notify} />
      case 'plan': return <Plan tasks={tasks} setTasks={setTasks} onNavigate={navigate} notify={notify} />
      case 'agent': return <AgentCenter notify={notify} />
      case 'study': return <StudyRoom target={studyTarget} onFocus={() => setFocusOpen(true)} onNavigate={navigate} onTutorContext={setTutorContext} notify={notify} />
      case 'map': return <KnowledgeMap onNavigate={navigate} notify={notify} onStudy={(planId, stageId) => { setStudyTarget({ planId, stageId }); navigate('study') }} />
      case 'practice': return <Practice notify={notify} />
      case 'review': return <Review notify={notify} />
      case 'library': return <Library notes={notes} setNotes={setNotes} resources={resources} notify={notify} />
      case 'projects': return <Projects projects={projects} setProjects={setProjects} notify={notify} />
      case 'reports': return <Reports notify={notify} />
      case 'community': return <Community notify={notify} />
      case 'integrations': return <Integrations notify={notify} />
      case 'providers': return <ProviderHub notify={notify} />
      case 'settings': return <Settings preferences={preferences} setPreferences={setPreferences} onImport={importAll} counts={{ tasks: tasks.length, notes: notes.length, resources: resources.length, projects: projects.length }} onReset={resetAll} notify={notify} />
      case 'shared': return <SharedPlan token={new URLSearchParams(window.location.search).get('share')} />
    }
  }

  if (page === 'shared') {
    return <SharedPlan token={new URLSearchParams(window.location.search).get('share')} />
  }

  if (startupError) {
    return <main className="page-loading"><LoaderMark /><h1>本地数据或实时连接打开失败</h1><p>{startupError}</p><Button onClick={() => window.location.reload()}>重试</Button></main>
  }
  if (!ready) {
    return <main className="page-loading"><LoaderMark />正在打开本地学习数据…</main>
  }

  return <div className={`app-shell ${page === 'study' ? 'study-mode' : ''}`}>
    <a className="skip-link" href="#main-content">跳到主要内容</a>
    {mobileNav && <button className="nav-backdrop" aria-label="关闭导航" onClick={() => setMobileNav(false)} />}
    <aside className={`sidebar ${mobileNav ? 'is-open' : ''}`}>
      <div className="brand"><BrandMark /><div><strong>知舟</strong><span>DEEP TUTOR</span></div><IconButton label="关闭导航" className="mobile-close" onClick={() => setMobileNav(false)}><X size={19} /></IconButton></div>
      <button className="goal-switch" onClick={() => navigate('plan')}><span><Target size={17} /></span><div><small>当前目标</small><strong>计算机考研</strong></div><ChevronDown size={15} /></button>
      <nav className="main-nav" aria-label="主导航">{navGroups.map(group => <div className="nav-group" key={group.label}><span className="nav-label">{group.label}</span>{group.items.map(item => <button key={item.id} className={page === item.id ? 'active' : ''} onClick={() => navigate(item.id)}><item.icon size={18} /><span>{item.label}</span>{item.id === 'review' && <i>12</i>}</button>)}</div>)}</nav>
      <div className="sidebar-bottom"><button className={page === 'settings' ? 'active' : ''} onClick={() => navigate('settings')}><SettingsIcon size={18} /><span>设置与偏好</span></button><button onClick={() => setModal('tasks')}><Zap size={18} /><span>Agent 任务中心</span><i className="running-dot" /></button><div className="sidebar-profile"><span>陈</span><div><strong>陈默</strong><small>连续学习 16 天</small></div><button aria-label="个人设置" onClick={() => navigate('settings')}><MoreHorizontal size={18} /></button></div></div>
    </aside>

    <div className="app-main">
      <header className="topbar"><div className="topbar-left"><IconButton label="打开导航" className="mobile-menu" onClick={() => setMobileNav(true)}><Menu size={20} /></IconButton><button className="global-search" onClick={() => setModal('search')}><Search size={17} /><span>搜索课程、笔记或知识点…</span><kbd>⌘ K</kbd></button></div><div className="topbar-actions"><button className="quick-add" onClick={() => setModal('quick')}><Plus size={18} /><span>快速记录</span></button><IconButton label={preferences.theme === 'light' ? '切换深色模式' : '切换明亮模式'} onClick={() => setPreferences({ ...preferences, theme: preferences.theme === 'light' ? 'dark' : 'light' })}>{preferences.theme === 'light' ? <Moon size={18} /> : <Sun size={18} />}</IconButton><IconButton label="通知" className="notification-button" onClick={() => setModal('notifications')}><Bell size={18} /><i /></IconButton><Button variant="secondary" size="sm" className="ask-agent" onClick={() => setTutorOpen(true)}><Sparkles size={16} />问知舟</Button></div></header>
      <main id="main-content" className="main-content"><Suspense fallback={<div className="page-loading"><LoaderMark />正在加载学习工作区…</div>}>{renderPage()}</Suspense></main>
    </div>

    <nav className="mobile-bottom-nav" aria-label="移动端快捷导航">{[{ id: 'today' as PageId, label: '今日', icon: Home }, { id: 'study' as PageId, label: '学习', icon: BookOpen }, { id: 'review' as PageId, label: '复习', icon: Layers3 }, { id: 'library' as PageId, label: '笔记', icon: LibraryBig }].map(item => <button key={item.id} className={page === item.id ? 'active' : ''} onClick={() => navigate(item.id)}><item.icon size={20} /><span>{item.label}</span></button>)}<button onClick={() => setMobileNav(true)}><Menu size={20} /><span>全部</span></button></nav>

    {tutorOpen && <div className="drawer-overlay" onMouseDown={e => { if (e.target === e.currentTarget) setTutorOpen(false) }}><aside className="global-tutor" role="dialog" aria-modal="true" aria-label="知舟全局导师"><TutorPanel compact onClose={() => setTutorOpen(false)} planId={tutorContext?.planId} contextLabel={tutorContext?.label} /></aside></div>}

    <Modal open={modal === 'search'} onClose={() => { setModal(null); setQuery('') }} title="全局搜索">
      <div className="command-search"><Search size={20} /><input autoFocus value={query} onChange={e => setQuery(e.target.value)} placeholder="输入页面、笔记或知识点…" /></div><div className="command-results">{searchResults.length ? searchResults.map((result, index) => <button key={`${result.id}-${index}`} onClick={() => { navigate(result.id); setModal(null); setQuery('') }}><result.icon size={18} /><div><strong><Highlight text={result.title} query={query} /></strong><span>{result.subtitle}</span></div><span>↵</span></button>) : <div className="search-empty">没有找到匹配结果，试试“复习”或“贝叶斯”。</div>}</div><div className="command-hint"><Command size={14} />支持搜索功能与笔记内容，按 Esc 关闭</div>
    </Modal>
    <Modal open={modal === 'quick'} onClose={() => setModal(null)} title="快速记录">
      <div className="form-stack"><div className="segmented">{['任务', '笔记'].map(item => <button className={quickType === item ? 'active' : ''} key={item} onClick={() => setQuickType(item)}>{item}</button>)}</div><label>{quickType === '任务' ? '要做什么？' : '想记下什么？'}<input autoFocus value={quickTitle} onChange={e => setQuickTitle(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') addQuick() }} placeholder={quickType === '任务' ? '例如：复习递归错题' : '例如：先验概率决定判断的起点'} /></label><p className="form-help">{quickType === '任务' ? '将加入今日任务，之后可在学习计划中调整。' : '将保存到笔记资料库，标签为「复盘」.'}</p><Button onClick={addQuick} disabled={!quickTitle.trim()}>保存{quickType}</Button></div>
    </Modal>
    <Modal open={modal === 'notifications'} onClose={() => setModal(null)} title="通知与提醒">
      <div className="notification-list"><button onClick={() => { navigate('review'); setModal(null) }}><span className="notification-icon"><Layers3 size={18} /></span><div><strong>12 张记忆卡已到期</strong><p>预计 8 分钟，现在适合进行一次主动回忆。</p><small>10 分钟前</small></div></button><button onClick={() => { navigate('projects'); setModal(null) }}><span className="notification-icon"><FolderKanban size={18} /></span><div><strong>项目反馈已生成</strong><p>分类器项目有 2 条关于评估指标的新建议。</p><small>1 小时前</small></div></button><button onClick={() => { navigate('plan'); setModal(null) }}><span className="notification-icon"><CalendarDays size={18} /></span><div><strong>本周计划负荷适中</strong><p>周六项目任务可灵活迁移到周日。</p><small>今天 08:00</small></div></button></div><Button variant="ghost" onClick={() => { notify('所有通知已标为已读'); setModal(null) }}><Check size={16} />全部标为已读</Button>
    </Modal>
    <Modal open={modal === 'tasks'} onClose={() => setModal(null)} title="Agent 任务中心">
      <AgentTaskCenter notify={notify} />
    </Modal>

    {focusOpen && <div className="focus-overlay" role="dialog" aria-modal="true" aria-label="专注学习"><div className="focus-top"><div><BrandMark /><span>知舟 · 专注空间</span></div><IconButton label="退出专注" onClick={() => { setFocusOpen(false); setFocusRunning(false) }}><X size={22} /></IconButton></div><div className="focus-content"><p>当前任务</p><h2>理解贝叶斯定理与先验概率</h2><div className="focus-timer"><span>{minutes}</span><i>:</i><span>{seconds}</span></div><div className="focus-actions"><Button variant="light" size="lg" onClick={() => setFocusRunning(!focusRunning)}>{focusRunning ? <Pause size={20} /> : <Play size={20} />}{focusRunning ? '暂停' : '开始专注'}</Button><IconButton label="重置为 25 分钟" onClick={() => { setFocusSeconds(25 * 60); setFocusRunning(false) }}><TimerReset size={21} /></IconButton></div><span className="focus-quote">一次只做好一件事。消息与其他页面会在你回来时保留。</span></div><button className="focus-exit" onClick={() => { setFocusOpen(false); setFocusRunning(false) }}>Esc 退出专注</button></div>}
    {toast && <div className="toast" role="status"><Check size={17} /><span>{toast}</span><button aria-label="关闭提示" onClick={() => setToast('')}><X size={15} /></button></div>}
  </div>
}

function LoaderMark() { return <span className="loader-mark" aria-hidden="true" /> }

function Highlight({ text, query }: { text: string; query: string }) {
  if (!query.trim()) return <>{text}</>
  const parts = text.split(new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'ig'))
  return <>{parts.map((part, index) => part.toLowerCase() === query.toLowerCase() ? <mark key={index}>{part}</mark> : part)}</>
}

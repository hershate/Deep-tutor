import { useState } from 'react'
import { ArrowRight, BookOpen, Brain, CalendarDays, Check, ChevronRight, CircleAlert, Clock3, Flame, Lightbulb, LoaderCircle, Play, RotateCcw, Sparkles, Timer, TrendingUp } from 'lucide-react'
import type { PageId, Task } from '../data'
import { Badge, Button, MiniBars, ProgressBar, ProgressRing, SectionHeader } from '../components/UI'
import { reportBackendError } from '../backend/client'
import type { AppSnapshot } from '../backend/client'
import type { DashboardOverview } from '../backend/engagement'
import { localId } from '../ids'

/** 摘要由 App 全局加载一次并随事件刷新（侧栏与今日页共用），页面自身不再重复请求。 */
export default function Dashboard({ tasks, setTasks, onNavigate, onFocus, notify, overview, summaryError, onRetrySummary }: { tasks: Task[]; setTasks: (tasks: Task[] | ((current: Task[]) => Task[])) => Promise<AppSnapshot>; onNavigate: (page: PageId) => void; onFocus: (taskId?: number) => void; notify: (message: string) => void; overview: DashboardOverview | null; summaryError: string; onRetrySummary: () => void }) {
  const error = summaryError
  // 43#3：在途翻转集——勾选后立即按目标态渲染（IPC 往返期间控件不再无响应），
  // 同一任务在途时的二次点击直接忽略（原先连点两次经串行化相互抵消）
  const [pendingToggles, setPendingToggles] = useState<Set<number>>(() => new Set())
  // 43#13：快捷占位任务落库进行中禁用，连点不再堆叠多条
  const [addingTask, setAddingTask] = useState(false)
  if (!overview && error) return <div className="page-loading" role="alert"><CircleAlert size={25} /><h2>学习摘要读取失败</h2><p>{error}</p><Button onClick={onRetrySummary}>重新读取</Button></div>
  if (!overview) return <div className="page-loading"><LoaderCircle size={22} className="spin" />正在读取真实学习摘要…</div>
  const shownDone = (task: Task) => task.done !== pendingToggles.has(task.id)
  const done = tasks.filter(shownDone).length
  const completion = tasks.length ? Math.round(done / tasks.length * 100) : 0
  const next = overview.nextTask
  const nextPage: PageId = next?.kind === '复习' ? 'review' : next?.kind === '练习' ? 'practice' : next?.kind === '项目' ? 'projects' : 'study'
  const recommendation = overview.recommendation
  const weeklyMinutes = overview.week.reduce((sum, day) => sum + day.seconds / 60, 0)
  const toggleTask = (id: number) => {
    if (pendingToggles.has(id)) return
    setPendingToggles(current => new Set(current).add(id))
    void setTasks(current => current.map(task => task.id === id ? { ...task, done: !task.done } : task))
      .catch(taskError => reportBackendError(taskError, 'write'))
      .finally(() => setPendingToggles(current => { const nextSet = new Set(current); nextSet.delete(id); return nextSet }))
  }
  const formatDate = (value: string) => {
    // 纯日期串（YYYY-MM-DD）按本地时区解析：Date.parse 会按 UTC 零点，
    // 西半球时区 toLocaleDateString 会显示成前一天（13#15）
    const plain = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
    if (plain) {
      const [year, month, day] = [Number(plain[1]), Number(plain[2]) - 1, Number(plain[3])] as const
      return new Date(year, month, day).toLocaleDateString('zh-CN')
    }
    const timestamp = Date.parse(value)
    return Number.isNaN(timestamp) ? value : new Date(timestamp).toLocaleDateString('zh-CN')
  }
  return <div className="page dashboard-page">
    <div className="page-heading"><div><p className="date-line">{new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' })}</p><h1>你好，{overview.name}。</h1><p>根据真实记录，选择下一步，而不是填充示例目标。</p></div><Button variant="secondary" onClick={() => onFocus()}><Play size={17} />进入专注</Button></div>
    <section className="today-hero"><div className="hero-orbit" aria-hidden="true"><span /><span /><span /></div><div className="today-hero-copy"><Badge tone="green">当前目标 · {overview.goal}</Badge><h2>把「不知道学什么」<br />变成清晰的下一步。</h2>{next ? <><p>下一站：{next.title} · {next.detail}</p><div className="hero-actions"><Button variant="light" size="lg" onClick={() => onNavigate(nextPage)}>继续学习<ArrowRight size={18} /></Button><span><Clock3 size={16} />预计 {next.minutes} 分钟</span></div></> : <><p>当前没有待完成任务，可以创建下一项真实学习活动。</p><div className="hero-actions"><Button variant="light" size="lg" onClick={() => onNavigate('plan')}>查看学习计划<ArrowRight size={18} /></Button></div></>}</div><div className="hero-progress"><ProgressRing value={overview.progress} size={148}><div><strong>{overview.progress}%</strong><span>方案生成进度</span></div></ProgressRing><p>{overview.deadline ? `目标日 ${formatDate(overview.deadline)}` : '尚未设置目标日'}</p></div></section>
    <div className="dashboard-grid"><section className="surface task-surface"><SectionHeader title="今日学习航线" description={`${done}/${tasks.length} 项已完成 · 约 ${tasks.filter(task => !shownDone(task)).reduce((sum, task) => sum + task.duration, 0)} 分钟剩余`} action={<Button variant="ghost" size="sm" onClick={() => onNavigate('plan')}>查看计划<ChevronRight size={16} /></Button>} /><div className="task-progress"><ProgressBar value={completion} /><span>{completion}%</span></div><div className="task-list">{tasks.slice(0, 100).map((task, index) => <div className={`task-row ${shownDone(task) ? 'is-done' : ''}`} key={task.id}><button className="task-check" aria-label={shownDone(task) ? `将${task.title}标为未完成` : `完成${task.title}`} onClick={() => toggleTask(task.id)}>{shownDone(task) ? <Check size={16} /> : <span>{index + 1}</span>}</button><div className="task-copy"><strong>{task.title}</strong><span>{task.subject} · {task.kind}</span></div><span className="task-time">{task.duration} 分钟</span>{!shownDone(task) && <button className="task-go" aria-label={`专注此任务：${task.title}`} title="专注此任务" onClick={() => onFocus(task.id)}><Timer size={16} /></button>}{!shownDone(task) && <button className="task-go" aria-label={`开始${task.title}`} onClick={() => onNavigate(task.kind === '复习' ? 'review' : task.kind === '练习' ? 'practice' : task.kind === '项目' ? 'projects' : 'study')}><Play size={16} /></button>}</div>)}{!tasks.length && <p className="form-help">还没有任务，先添加一项真实学习活动。</p>}{tasks.length > 100 && <p className="form-help">任务较多，当前显示前 100 条；可在学习计划中清理已完成任务。</p>}</div><button className="text-action" disabled={addingTask} onClick={() => { setAddingTask(true); void setTasks(current => [...current, { id: localId(), title: '新的学习任务', subject: '待分类', duration: 20, done: false, kind: '学习' }]).then(() => notify('任务已保存，可在学习计划中编辑')).catch(error => reportBackendError(error, 'write')).finally(() => setAddingTask(false)) }}>{addingTask ? '正在添加…' : '＋ 添加今日任务'}</button></section>
      <aside className="dashboard-side"><section className="surface review-callout"><div className="review-callout-head"><span className="soft-icon"><RotateCcw size={19} /></span><Badge tone="amber">记忆到期</Badge></div><h3>{overview.reviewDue} 张卡片等待复习</h3><p>按真实到期时间安排主动回忆。</p><div className="review-meta"><span>预计 {overview.reviewMinutes} 分钟</span></div><Button onClick={() => onNavigate('review')}>开始复习<ArrowRight size={17} /></Button></section><section className="surface streak-panel"><div><Flame size={20} /><strong>连续记录 {overview.streak} 天</strong></div><p>保持节奏，但休息也算计划的一部分。</p><div className="week-dots">{overview.week.map((day, index) => <div key={day.kind}><span className={day.seconds > 0 ? 'active' : ''}>{day.seconds > 0 && <Check size={13} />}</span><small>{['日', '一', '二', '三', '四', '五', '六'][new Date(`${day.kind}T00:00:00`).getDay()] || index + 1}</small></div>)}</div></section></aside>
    </div>
    <div className="dashboard-lower"><section className="surface rhythm-panel"><SectionHeader title="本周学习节奏" description={`真实记录 ${(weeklyMinutes / 60).toFixed(1)} 小时 · 柱高为每日分钟`} action={<Badge tone="green"><TrendingUp size={13} />真实数据</Badge>} /><MiniBars values={overview.week.map(day => day.seconds / 60)} labels={overview.week.map(day => new Date(`${day.kind}T00:00:00`).toLocaleDateString('zh-CN', { weekday: 'short' }))} /><div className="rhythm-stats"><div><span>有效学习记录</span><strong>{(overview.focusSeconds / 3600).toFixed(1)}<small>小时</small></strong></div><div><span>主动练习</span><strong>{overview.practiceCount}<small>题</small></strong></div><div><span>记忆保持率</span><strong>{overview.retentionPercent === null ? '—' : `${Math.round(overview.retentionPercent * 100)}%`}</strong></div></div></section><section className="surface insight-panel"><div className="insight-title"><Sparkles size={19} /><h2>知舟的学习观察</h2></div>{recommendation ? <><p><strong>{recommendation.title}</strong>：{recommendation.detail}</p><div className="insight-evidence"><CircleAlert size={17} /><span>建议来自当前工作区的真实证据。</span></div><Button variant="secondary" onClick={() => recommendation.kind === 'focus' ? onFocus() : onNavigate('practice')}><Lightbulb size={17} />{recommendation.kind === 'focus' ? '开始真实专注' : '前往针对练习'}</Button></> : <><p>当前没有足够的证据生成学习建议。</p><Button variant="secondary" onClick={() => onNavigate('plan')}><BookOpen size={17} />查看真实计划</Button></>}</section><section className="surface upcoming-panel"><SectionHeader title="接下来" action={<CalendarDays size={19} />} />{overview.upcoming.length ? overview.upcoming.map((item, itemIndex) => <button className="upcoming-item" key={`${item.kind}-${item.title}-${item.date}-${itemIndex}`} onClick={() => onNavigate(item.page)}><span className="upcoming-date">{formatDate(item.date)}</span><div><strong>{item.title}</strong><p>{item.detail}</p></div></button>) : <p className="form-help">暂无已安排的考试或项目。</p>}<button className="text-action" onClick={() => onNavigate('projects')}>查看项目与里程碑<ArrowRight size={15} /></button></section></div>
    <div className="demo-note"><Brain size={15} />首页只使用当前工作区的真实记录；空状态代表尚无数据，不填充示例。</div>
  </div>
}

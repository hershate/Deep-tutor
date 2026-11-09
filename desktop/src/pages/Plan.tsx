import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, ArrowDownToLine, ArrowRight, CalendarRange, Check, ChevronLeft, ChevronRight, Clock3, Plus, RefreshCw, Share2, SlidersHorizontal, Sparkles, Target } from 'lucide-react'
import type { PageId, Task } from '../data'
import { Badge, Button, ConfirmDialog, EmptyState, Modal, ProgressBar, SectionHeader } from '../components/UI'
import ExportCenter from '../components/ExportCenter'
import SharePlanDialog from '../components/SharePlanDialog'
import { backendErrorMessage, onBackendEvent, reportBackendError } from '../backend/client'
import type { AppSnapshot } from '../backend/client'
import { localId } from '../ids'
import { localShareUrl } from '../backend/sharing'
import { planningBackend } from '../backend/planning'
import type { PlanAlternative, PlanOverview, ScheduleEvent } from '../backend/planning'
import { clampToBytes } from '../limits'
import { statusName } from '../status'

const weekDays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
function localDate(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
function currentMonday() {
  const date = new Date()
  date.setHours(12, 0, 0, 0)
  date.setDate(date.getDate() - (date.getDay() + 6) % 7)
  return localDate(date)
}
function shiftWeek(start: string, amount: number) {
  const date = new Date(`${start}T12:00:00`)
  date.setDate(date.getDate() + amount * 7)
  return localDate(date)
}

export default function Plan({ setTasks, onNavigate, notify }: { setTasks: (tasks: Task[] | ((current: Task[]) => Task[])) => Promise<AppSnapshot>; onNavigate: (page: PageId) => void; notify: (message: string) => void }) {
  const [overview, setOverview] = useState<PlanOverview | null>(null)
  const [weekStart, setWeekStart] = useState(currentMonday)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [addOpen, setAddOpen] = useState(false)
  const [goalOpen, setGoalOpen] = useState(false)
  const [alternativesOpen, setAlternativesOpen] = useState(false)
  const [alternatives, setAlternatives] = useState<PlanAlternative[]>([])
  const [selectedTask, setSelectedTask] = useState<ScheduleEvent | null>(null)
  const [taskDate, setTaskDate] = useState('')
  const [title, setTitle] = useState('')
  const [subject, setSubject] = useState('')
  const [duration, setDuration] = useState(30)
  const [newTaskDate, setNewTaskDate] = useState(localDate(new Date()))
  const [goal, setGoal] = useState('')
  const [deadline, setDeadline] = useState('')
  const [weeklyHours, setWeeklyHours] = useState(15)
  const [level, setLevel] = useState('')
  const [exportOpen, setExportOpen] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)
  // 43#5：强度 segmented 外观是筛选、行为是覆盖性重排——点击先落确认层
  const [intensityTarget, setIntensityTarget] = useState<string | null>(null)
  const plan = overview?.plan ?? null
  const ready = Boolean(plan?.result && ['done', 'degraded'].includes(plan.status))
  const progress = overview?.stages.length ? Math.round(overview.stages.filter(stage => stage.completed).length / overview.stages.length * 100) : 0
  const load = overview?.loadPercent ?? 0
  const loadText = load > 100 ? '超出每周可投入时间' : load >= 75 ? '接近每周投入上限' : '仍有可安排的余量'

  // 51#2：序号守卫——快速翻周/事件并发时，后到的旧周响应不再覆盖新周视图
  const refreshSeq = useRef(0)
  // 60#1：可选 week 参数供写路径复查（写回值不得绕过守卫直写 overview）
  const refresh = useCallback(async (week: string = weekStart) => {
    const ticket = ++refreshSeq.current
    try {
      const next = await planningBackend.overview(week)
      if (ticket !== refreshSeq.current) return
      setOverview(next)
      setError('')
    } catch (cause) { if (ticket === refreshSeq.current) setError(backendErrorMessage(cause)) }
  }, [weekStart])
  // 51#9：事件监听只挂载一次——原先依赖含 refresh（随 weekStart 变化），
  // 每次翻周都先注销再异步重挂，间隙到达的事件被丢弃；回调经 ref 取最新
  const refreshRef = useRef(refresh)
  useEffect(() => { refreshRef.current = refresh })
  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    let active = true
    let stop: (() => void) | undefined
    void onBackendEvent(event => {
      if (active && (event.kind.startsWith('plan.') || event.kind === 'security.session_started' || (event.kind === 'state.changed' && ['tasks', 'preferences'].includes(String(event.payload.namespace))))) void refreshRef.current()
    }).then(unlisten => { if (active) stop = unlisten; else unlisten() }).catch(reportBackendError)
    return () => { active = false; stop?.() }
  }, [])

  const changeIntensity = async (intensity: string) => {
    if (busy || !ready) return
    setBusy('intensity')
    try {
      await planningBackend.applyIntensity(intensity, weekStart)
      notify(`已按「${intensity}」保存全部方案排期与任务时长`)
      // 60#1：写回值不得直写 setOverview——applyIntensity 在途时用户翻周，
      // 后到的 W1 响应会绕过 51#2 序号守卫盖掉 W2 视图（错周无自愈）；
      // 改经 refresh 统一过守卫取数
      await refresh()
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const showAlternatives = async () => {
    setBusy('alternatives')
    try {
      setAlternatives(await planningBackend.alternatives(weekStart))
      setAlternativesOpen(true)
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const openGoal = () => {
    if (!plan) return
    setGoal(plan.goal); setDeadline(plan.deadline); setWeeklyHours(overview?.weeklyHours ?? 15); setLevel(plan.level)
    setError(''); setGoalOpen(true)
  }
  const saveGoal = async () => {
    if (!plan || busy) return
    setBusy('goal'); setError('')
    try {
      const next = await planningBackend.updateGoal({ planId: plan.id, goal, deadline, weeklyHours, level })
      setGoalOpen(false)
      notify(next.status === 'running' || next.status === 'cancelling' ? '目标已保存，原生再规划已开始' : `目标已保存，再规划状态：${statusName(next.status)}`)
      await refresh()
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const addTask = async () => {
    if (!title.trim() || busy) return
    setBusy('task')
    try {
      const id = localId()
      // 后端合法区间 1..=10080 分钟：手输越界值在提交前钳制，避免整单被拒后输入丢失
      const minutes = Math.min(10080, Math.max(1, Math.round(duration) || 1))
      // 20#2：title 上限 500B、subject 上限 100B（后端同界）；科目留空回退
      // 方案目标时先截断，长目标不再令保存必败
      await setTasks(current => [...current, { id, title: title.trim(), subject: clampToBytes(subject.trim() || plan?.goal || '综合学习', 100), duration: minutes, done: false, kind: '学习' }])
      // A-7：成功后清除旧错误，重开弹窗不再显示上次的失败提示
      setError('')
      setTitle(''); setAddOpen(false)
      if (plan && newTaskDate) {
        try {
          await planningBackend.assignTaskDate(id, newTaskDate)
          notify('任务已保存并加入所选日期')
        } catch (cause) {
          // 51#7：任务本体已落库、仅排期失败——分段提示，用户不必重提整个表单
          // （原先错误提示不分段，重试会创建第二条同标题任务）
          notify(`任务已保存，但加入所选日期失败：${backendErrorMessage(cause)}。可稍后在待排期列表重选日期`)
        }
      } else notify('任务已保存，可在待排期列表中选择日期')
      await refresh()
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const saveTaskDate = async () => {
    if (!selectedTask?.taskId || !taskDate || busy) return
    setBusy('schedule')
    try {
      const next = await planningBackend.assignTaskDate(selectedTask.taskId, taskDate)
      // 60#1 同型：写回值不直写 overview——跨周由 setWeekStart 触发 effect
      // 过守卫重取；同周 effect 不重挂，显式刷新
      if (next.weekStart === weekStart) await refresh(next.weekStart)
      else setWeekStart(next.weekStart)
      setSelectedTask(null)
      notify('任务日期已保存到真实排期')
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const toggleTask = async () => {
    if (!selectedTask?.taskId || busy) return
    setBusy('toggle')
    try {
      // 32#9：以 updater 内的最新集合取反——弹窗快照在打开期间可能被事件写入改变
      await setTasks(current => current.map(task => task.id === selectedTask.taskId ? { ...task, done: !task.done } : task))
      setSelectedTask(null); await refresh()
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const openTask = (event: ScheduleEvent) => {
    setSelectedTask(event); setTaskDate(event.date ?? weekStart); setError('')
  }
  const weekEnd = overview ? shiftWeek(overview.weekStart, 1) : ''
  const endLabel = weekEnd ? localDate(new Date(new Date(`${weekEnd}T12:00:00`).getTime() - 86400000)) : ''
  return <div className="page plan-page">
    <div className="page-heading"><div><h1>学习计划</h1><p>让目标适配你的生活，而不是让生活追赶计划。</p></div><div className="heading-actions"><Button variant="secondary" disabled={!ready} onClick={() => setShareOpen(true)}><Share2 size={17} />分享</Button><Button variant="secondary" onClick={() => setExportOpen(true)}><ArrowDownToLine size={17} />导出</Button><Button onClick={() => onNavigate('generator')}><Sparkles size={17} />AI 生成方案</Button><Button variant="secondary" onClick={() => { setSubject(current => current || plan?.goal || ''); setError(''); setAddOpen(true) }}><Plus size={17} />添加任务</Button></div></div>
    {error && !addOpen && <div className="form-error" role="alert"><AlertTriangle size={16} />{error}</div>}
    {plan ? <section className="goal-strip">
      <div className="goal-main"><span className="goal-icon"><Target size={25} /></span><div><span>当前主目标 · {plan.id} · v{plan.version}</span><h2>{plan.goal}</h2><p>目标日 {plan.deadline} · 每周可投入 {overview?.weeklyHours ?? 0} 小时 · {plan.adoptedProjectId === null ? '尚未采用' : '已采用'}</p></div></div>
      <div className="goal-progress"><div><strong>{progress}%</strong><span>阶段阅读进度</span></div><ProgressBar value={progress} /></div>
      <Button variant="secondary" onClick={openGoal}><SlidersHorizontal size={17} />调整目标</Button>
    </section> : <section className="surface"><EmptyState title="尚未创建学习方案" description="从已解析资料生成真实方案，采用后会出现排期与阶段。你仍可以添加并保存自己的任务。" action={<Button onClick={() => onNavigate('generator')}>创建真实方案</Button>} /></section>}
    {plan && !ready && <div className="capacity-warning"><RefreshCw size={18} className={plan.status === 'running' ? 'spin' : ''} /><div><strong>方案状态：{statusName(plan.status)} · 生成进度 {plan.progress}%</strong><p>{plan.error ? `${plan.error.code}：${plan.error.message}` : plan.status === 'running' ? '正在等待原生步骤与真实事件，不会提前展示新的排期。' : '当前没有完整可采用的方案结果。'}</p><Button variant="secondary" size="sm" onClick={() => onNavigate('generator')}>查看真实生成步骤</Button></div></div>}

    <div className="plan-toolbar">
      <div className="segmented" aria-label="学习强度">{(['稳妥', '标准', '冲刺'] as const).map(item => <button key={item} disabled={!ready || busy === 'intensity'} className={overview?.intensity === item ? 'active' : ''} onClick={() => setIntensityTarget(item)}>{item}</button>)}</div>
      <div className="plan-toolbar-actions"><span><Clock3 size={16} />本周已排 {Math.round((overview?.weeklyMinutes ?? 0) / 6) / 10} 小时 / 可投入 {overview?.weeklyHours ?? 0} 小时</span><Button variant="secondary" size="sm" disabled={!ready || busy === 'intensity'} onClick={() => void changeIntensity(overview?.intensity ?? '标准')}><RefreshCw size={16} />按强度保存排期</Button></div>
    </div>

    <section className="calendar-surface">
      <SectionHeader title={overview ? `${overview.weekStart}—${endLabel}` : '正在读取真实周历'} description="仅显示已保存的日期、任务和预计分钟；未指定具体时间的任务不会虚构时刻。" action={<div className="calendar-nav"><button aria-label="上一周" onClick={() => setWeekStart(shiftWeek(weekStart, -1))}><ChevronLeft size={18} /></button><button className="today-button" onClick={() => setWeekStart(currentMonday())}>本周</button><button aria-label="下一周" onClick={() => setWeekStart(shiftWeek(weekStart, 1))}><ChevronRight size={18} /></button></div>} />
      <div className="week-calendar">{(overview?.days ?? []).map((day, index) => <div className={`day-column ${day.date === localDate(new Date()) ? 'is-today' : ''}`} key={day.date}><div className="day-head"><strong>{weekDays[index]}</strong><span>{day.date.slice(5)}</span></div><div className="day-events">{day.events.slice(0, 50).map((event, eventIndex) => <button className={`calendar-event event-${event.kind}`} key={`${event.kind}-${event.taskId ?? event.title}-${eventIndex}`} onClick={() => openTask(event)}><span>{event.minutes} 分钟{event.done ? ' · 已完成' : ''}</span><strong>{event.title}</strong><small>{event.kind}</small></button>)}</div></div>)}</div>
      {overview && !overview.days.some(day => day.events.length) && <EmptyState title="本周没有已排期任务" description="可从下方待排期任务选择真实日期，或切换周查看已有安排。" />}
    </section>

    {overview && overview.unscheduled.length > 0 && <section className="surface"><SectionHeader title="待排期任务" description="这些任务已保存，但当前方案中没有日期。选择任务即可安排或调整日期。" /><div className="milestone-list">{overview.unscheduled.map(event => <button className="milestone-row" key={event.taskId} onClick={() => openTask(event)}><span className="milestone-marker"><CalendarRange size={15} /></span><div><strong>{event.title}</strong><small>{event.kind} · {event.minutes} 分钟{event.done ? ' · 已完成' : ''}</small></div><Badge>选择日期</Badge></button>)}</div></section>}

    <div className="plan-bottom-grid">
      <section className="surface milestone-panel"><SectionHeader title="阶段里程碑" description="完成状态来自真实阅读记录；掌握证据请在知识地图与报告中查看。" /><div className="milestone-list">
        {overview?.stages.map((stage, index) => <div className="milestone-row" key={stage.id}><span className={`milestone-marker ${stage.completed ? 'done' : ''}`}>{stage.completed ? <Check size={15} /> : index + 1}</span><div><strong>{stage.title}</strong><small>{stage.minutes} 分钟 · 难度 {stage.difficulty}/5 · {stage.nodeIds.length} 个知识节点</small></div><Badge tone={stage.completed ? 'green' : 'neutral'}>{stage.completed ? '已完成' : '待完成'}</Badge></div>)}
        {overview && !overview.stages.length && <EmptyState title="没有完整阶段" description="方案生成并保存真实阶段后，才会显示里程碑。" />}
      </div></section>
      <section className="surface workload-panel"><SectionHeader title="学习负荷" description="按本周已排任务分钟数 ÷ 每周可投入时间计算。" /><div className="workload-number"><strong>{load}<small>%</small></strong><span>{loadText}</span></div><ProgressBar value={load} tone="blue" /><p>本周已排 {overview?.weeklyMinutes ?? 0} 分钟，可投入 {(overview?.weeklyHours ?? 0) * 60} 分钟。替代安排基于当前周的真实任务，不会自动移动日期。</p><Button variant="ghost" disabled={!ready || busy === 'alternatives'} onClick={() => void showAlternatives()}>查看替代安排<ArrowRight size={16} /></Button></section>
    </div>

    <Modal open={addOpen} onClose={() => setAddOpen(false)} title="添加学习任务"><div className="form-stack"><label>任务名称<input autoFocus value={title} onChange={event => setTitle(clampToBytes(event.target.value, 500))} placeholder="输入要实际完成的学习任务" /></label><label>科目或方向<input value={subject} onChange={event => setSubject(clampToBytes(event.target.value, 100))} placeholder="例如：当前方案主题" /></label><div className="form-grid"><label>预计时长（分钟）<input type="number" min={1} max={10080} value={duration} onChange={event => setDuration(Number(event.target.value))} /></label><label>安排日期<input type="date" value={newTaskDate} onChange={event => setNewTaskDate(event.target.value)} disabled={!plan} /></label></div>{!plan && <p className="form-help">尚无方案，任务会保存在待排期列表中。</p>}{error && <div className="form-error" role="alert"><AlertTriangle size={16} />{error}</div>}<Button onClick={() => void addTask()} disabled={!title.trim() || busy === 'task'}>{busy === 'task' ? '正在保存…' : '保存任务并排期'}</Button></div></Modal>
    <Modal open={Boolean(selectedTask)} onClose={() => setSelectedTask(null)} title="任务与排期"><div className="form-stack"><h3>{selectedTask?.title}</h3><p>{selectedTask?.kind} · {selectedTask?.minutes} 分钟 · {selectedTask?.done ? '已完成' : '未完成'}</p><label>实际安排日期<input type="date" value={taskDate} onChange={event => setTaskDate(event.target.value)} /></label>{error && <div className="form-error" role="alert"><AlertTriangle size={16} />{error}</div>}<Button disabled={!taskDate || busy === 'schedule'} onClick={() => void saveTaskDate()}>{busy === 'schedule' ? '正在保存…' : '保存日期'}</Button><Button variant="secondary" disabled={busy === 'toggle'} onClick={() => void toggleTask()}>{selectedTask?.done ? '标记为未完成' : '标记为已完成'}</Button></div></Modal>
    <Modal open={goalOpen} onClose={() => setGoalOpen(false)} title="调整学习目标"><div className="form-stack">{plan?.adoptedProjectId !== null ? <div className="modal-callout"><AlertTriangle size={18} /><span>已采用方案的学习记录必须保留，不能直接重跑。请通过 AI 生成方案创建新版本，采用新版本不会修改旧方案的完成记录。</span><Button variant="secondary" onClick={() => { setGoalOpen(false); onNavigate('generator') }}>创建新方案</Button></div> : <><label>目标名称<input value={goal} onChange={event => setGoal(clampToBytes(event.target.value, 200))} maxLength={200} /></label><div className="form-grid"><label>目标日期<input type="date" value={deadline} onChange={event => setDeadline(event.target.value)} /></label><label>每周可投入（小时）<input type="number" value={weeklyHours} onChange={event => setWeeklyHours(Number(event.target.value))} min={1} max={80} /></label></div><label>当前掌握情况<textarea rows={3} value={level} onChange={event => setLevel(clampToBytes(event.target.value, 1000))} /></label><div className="modal-callout"><CalendarRange size={18} /><span>保存后会真正从知识图谱步骤重新生成。现有版本先进入历史，真实生成完成后才能采用；失败会保留错误，不会显示虚假成功。</span></div>{error && <div className="form-error" role="alert"><AlertTriangle size={16} />{error}</div>}<Button onClick={() => void saveGoal()} disabled={busy === 'goal' || !goal.trim() || !deadline || !level.trim() || weeklyHours < 1 || weeklyHours > 80}>{busy === 'goal' ? '正在保存并启动…' : '保存并重新规划'}</Button></>}</div></Modal>
    <Modal open={alternativesOpen} onClose={() => setAlternativesOpen(false)} title="本周替代安排"><div className="form-stack">{alternatives.map(item => <button className="milestone-row" key={item.intensity} disabled={busy === 'intensity'} onClick={() => { setAlternativesOpen(false); void changeIntensity(item.intensity) }}><span className="milestone-marker"><Clock3 size={15} /></span><div><strong>{item.intensity} · {item.weeklyMinutes} 分钟</strong><small>占每周可投入时间的 {item.loadPercent}%，保留当前任务日期</small></div><Badge tone={overview?.intensity === item.intensity ? 'green' : 'neutral'}>{overview?.intensity === item.intensity ? '当前方案' : '采用此强度'}</Badge></button>)}</div></Modal>
    <ConfirmDialog open={intensityTarget !== null} onClose={() => setIntensityTarget(null)} onConfirm={() => { const next = intensityTarget; setIntensityTarget(null); if (next) void changeIntensity(next) }} title={`按「${intensityTarget ?? ''}」重排全部任务？`} description="会以该强度重新保存全部方案任务的时长与日期，手动调整过的排期也会被覆盖；此操作不可撤销。想先看各强度的工作量，可点「查看替代安排」预览。" confirmLabel="确认重排" busy={busy === 'intensity'} />
    <ExportCenter open={exportOpen} onClose={() => setExportOpen(false)} notify={notify} initialPlanId={ready ? plan?.id : undefined} />
    <SharePlanDialog open={shareOpen} onClose={() => setShareOpen(false)} notify={notify} initialPlanId={ready ? plan?.id : undefined} onPreview={token => { window.history.replaceState(null, '', localShareUrl(token)); setShareOpen(false); onNavigate('shared') }} />
  </div>
}

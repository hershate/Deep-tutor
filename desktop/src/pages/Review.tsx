import { useCallback, useEffect, useState } from 'react'
import { ArrowRight, Brain, CalendarClock, Check, ChevronRight, CircleHelp, Clock3, Layers3, Plus, RotateCcw, Sparkles } from 'lucide-react'
import { Badge, Button, Modal, ProgressBar, SectionHeader } from '../components/UI'
import { backend, onBackendEvent, reportBackendError, type LearningPlan, backendErrorMessage } from '../backend/client'
import { reviewBackend, type ReviewCard, type ReviewCardInput, type ReviewGrade, type ReviewHistoryEntry, type ReviewStats } from '../backend/review'
import { clampToBytes } from '../limits'

const emptyStats: ReviewStats = { dueToday: 0, retention: null, estimatedMinutes: 0, tomorrow: 0, calendar: [] }
const emptyInput: ReviewCardInput = { front: '', back: '', tag: '', planId: null, nodeId: null }
const gradeNames: Record<ReviewGrade, string> = { again: '再来一次', hard: '困难', good: '良好', easy: '轻松' }
const gradeDays: Record<ReviewGrade, string> = { again: '10 分钟后', hard: '首次 2 天', good: '首次 5 天', easy: '首次 12 天' }

export default function Review({ notify }: { notify: (message: string) => void }) {
  const [card, setCard] = useState<ReviewCard | null>(null)
  const [loadError, setLoadError] = useState('')
  const [cards, setCards] = useState<ReviewCard[]>([])
  const [plans, setPlans] = useState<LearningPlan[]>([])
  const [stats, setStats] = useState<ReviewStats>(emptyStats)
  const [flipped, setFlipped] = useState(false)
  const [reviewed, setReviewed] = useState(0)
  const [busy, setBusy] = useState(false)
  const [manageOpen, setManageOpen] = useState(false)
  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<ReviewCard | null>(null)
  const [input, setInput] = useState<ReviewCardInput>(emptyInput)
  const [historyCard, setHistoryCard] = useState<number | null>(null)
  const [history, setHistory] = useState<ReviewHistoryEntry[]>([])
  const [logicOpen, setLogicOpen] = useState(false)

  const refresh = useCallback(async () => {
    setLoadError('')
    const [next, allCards, nextStats, allPlans] = await Promise.all([
      reviewBackend.next(), reviewBackend.list(), reviewBackend.stats(), backend.listLearningPlans(),
    ])
    setCard(next)
    setCards(allCards)
    setStats(nextStats)
    setPlans(allPlans.filter(plan => plan.adoptedProjectId !== null && plan.result))
  }, [])

  useEffect(() => {
    // 加载失败保留错误横幅（含重试），与"现在没有到期卡片"空态区分
    void refresh().catch(error => { setLoadError(backendErrorMessage(error)); reportBackendError(error) })
    let unlisten: (() => void) | undefined
    const stop = onBackendEvent(event => {
      if (event.kind.startsWith('review.') || event.kind.startsWith('plan.')) void refresh().catch(reportBackendError)
    }).then(stopListening => { unlisten = stopListening }).catch(reportBackendError)
    return () => { unlisten?.(); void stop }
  }, [refresh])

  const grade = useCallback(async (level: ReviewGrade) => {
    if (!card || !flipped || busy) return
    setBusy(true)
    try {
      const result = await reviewBackend.grade(card.id, level)
      setReviewed(value => value + 1)
      setFlipped(false)
      await refresh()
      const days = result.card.intervalDays
      notify(level === 'again' ? '已记录，将在 10 分钟后再次到期' : `已记录为「${gradeNames[level]}」，下次间隔 ${days.toFixed(days < 1 ? 2 : 1)} 天`)
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }, [card, flipped, busy, refresh, notify])

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (editorOpen || manageOpen || !card || busy) return
      if (event.code === 'Space') { event.preventDefault(); setFlipped(value => !value) }
      const keys: Record<string, ReviewGrade> = { Digit1: 'again', Digit2: 'hard', Digit3: 'good', Digit4: 'easy' }
      if (flipped && keys[event.code]) { event.preventDefault(); void grade(keys[event.code]) }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [flipped, card, busy, editorOpen, manageOpen, grade])

  const edit = (item?: ReviewCard) => {
    setEditing(item ?? null)
    setInput(item ? { front: item.front, back: item.back, tag: item.tag, planId: item.planId, nodeId: item.nodeId } : emptyInput)
    setEditorOpen(true)
  }
  const save = async () => {
    if (!input.front.trim() || !input.back.trim() || busy) return
    setBusy(true)
    try {
      const payload = { ...input, front: input.front.trim(), back: input.back.trim(), tag: input.tag?.trim() || null, nodeId: input.planId ? input.nodeId : null }
      if (editing) await reviewBackend.update(editing.id, payload)
      else await reviewBackend.create(payload)
      setEditorOpen(false)
      await refresh()
      notify(editing ? '记忆卡修改已保存' : '记忆卡已加入复习队列')
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const remove = async (item: ReviewCard) => {
    try {
      await reviewBackend.remove(item.id)
      if (historyCard === item.id) { setHistoryCard(null); setHistory([]) }
      await refresh()
      notify('记忆卡已删除，历史记录仍可查询')
    } catch (error) { reportBackendError(error) }
  }
  const suspend = async (item: ReviewCard) => {
    try {
      await reviewBackend.suspend(item.id, !item.suspended)
      await refresh()
      notify(item.suspended ? '记忆卡已恢复到复习队列' : '记忆卡已暂停，不会出现在到期队列')
    } catch (error) { reportBackendError(error) }
  }
  const showHistory = async (id: number) => {
    if (historyCard === id) { setHistoryCard(null); return }
    try {
      setHistory(await reviewBackend.history(id))
      setHistoryCard(id)
    } catch (error) { reportBackendError(error) }
  }
  const nodes = plans.find(plan => plan.id === input.planId)?.result?.graph.nodes ?? []
  const maximum = Math.max(...stats.calendar.map(day => day.count), 1)

  return <div className="page review-page">
    {loadError && <div className="form-error" role="alert"><span>复习数据读取失败：{loadError}</span><Button variant="secondary" size="sm" onClick={() => { setLoadError(''); void refresh().catch(error => { setLoadError(backendErrorMessage(error)); reportBackendError(error) }) }}>重试</Button></div>}
    <div className="page-heading"><div><h1>记忆复习</h1><p>先努力回忆，再查看答案；每次反馈都会持久化并计算下次到期时间。</p></div><div className="heading-actions"><Button variant="secondary" onClick={() => setManageOpen(true)}>管理记忆卡</Button><Button onClick={() => edit()}><Plus size={17} />创建记忆卡</Button></div></div>
    <div className="review-stats"><div><Layers3 size={20} /><span>今日待复习</span><strong>{stats.dueToday}<small>张</small></strong></div><div><Brain size={20} /><span>历史反馈保持率</span><strong>{stats.retention ?? '—'}<small>{stats.retention === null ? '' : '%'}</small></strong></div><div><Clock3 size={20} /><span>预计用时</span><strong>{stats.estimatedMinutes}<small>分钟</small></strong></div><div><CalendarClock size={20} /><span>明日到期</span><strong>{stats.tomorrow}<small>张</small></strong></div></div>
    <div className="review-layout">
      <section className="review-workspace">
        {card ? <>
          <div className="review-progress"><span>本次已复习 {reviewed} 张 · 当前还有 {stats.dueToday} 张今日到期</span><ProgressBar value={reviewed / Math.max(reviewed + stats.dueToday, 1) * 100} /><Badge tone="neutral">{card.tag}</Badge></div>
          <button className={`flashcard ${flipped ? 'is-flipped' : ''}`} onClick={() => setFlipped(!flipped)} disabled={busy} aria-label={flipped ? '查看问题' : '查看答案'}>
            <div className="flashcard-face flashcard-front"><span>问题</span><h2>{card.front}</h2><small><CircleHelp size={15} />先在心里作答，再点击查看答案</small></div>
            <div className="flashcard-face flashcard-back"><span>参考答案</span><h2>{card.back}</h2><small>对照你的回答，再诚实选择记忆程度</small></div>
          </button>
          <div className="review-keyboard">Space 翻卡 · 1 再来一次 · 2 困难 · 3 良好 · 4 轻松</div>
          {!flipped ? <Button size="lg" onClick={() => setFlipped(true)}>显示答案<ChevronRight size={18} /></Button> : <div className="review-grade">{(['again', 'hard', 'good', 'easy'] as ReviewGrade[]).map(level => <button key={level} className={`grade-${level}`} onClick={() => void grade(level)} disabled={busy}><span>{gradeNames[level]}</span><small>{gradeDays[level]}</small></button>)}</div>}
        </> : <div className="review-complete"><span><Check size={32} /></span><h2>{reviewed ? '本轮到期卡片复习完成。' : '现在没有到期卡片。'}</h2><p>本次完成 {reviewed} 次主动回忆；明日到期 {stats.tomorrow} 张。暂停或未来到期的卡片不会提前出现。</p><div><Button onClick={() => { void refresh().catch(reportBackendError) }}><RotateCcw size={17} />刷新到期队列</Button><Button variant="secondary" onClick={() => setManageOpen(true)}>查看全部记忆卡</Button></div></div>}
      </section>
      <aside className="review-side"><section className="surface forgetting-panel"><SectionHeader title="记忆节奏" description="未来 7 天真实到期卡片" /><div className="review-calendar">{stats.calendar.map((day, index) => <div key={day.date}><span style={{ height: `${Math.max(2, day.count / maximum * 96)}px` }} className={index === 0 ? 'active' : ''} /><small>{day.label}</small><strong>{day.count}</strong></div>)}</div></section><section className="surface review-method"><Sparkles size={20} /><h3>为什么先显示问题？</h3><p>主动回忆会暴露“看得熟但想不起”的假性掌握。反馈会保存为复习历史，并作为关联知识节点的学习证据。</p><button onClick={() => setLogicOpen(!logicOpen)}>了解复习安排逻辑<ArrowRight size={15} /></button>{logicOpen && <p>首次反馈：困难 2 天、良好 5 天、轻松 12 天；之后按上次间隔与难度系数计算。再来一次为 10 分钟，并清零连续次数。保持率按困难 60%、良好 80%、轻松 100% 的历史反馈均值计算；预计每张 40 秒。</p>}</section></aside>
    </div>
    <Modal open={editorOpen} onClose={() => setEditorOpen(false)} title={editing ? '编辑记忆卡' : '创建记忆卡'}><div className="form-stack"><label>正面 · 问题<textarea rows={3} value={input.front} onChange={event => setInput({ ...input, front: clampToBytes(event.target.value, 8000) })} placeholder="尽量一次只检查一个知识点" /></label><label>背面 · 参考答案<textarea rows={4} value={input.back} onChange={event => setInput({ ...input, back: clampToBytes(event.target.value, 20000) })} placeholder="保留关键解释，避免整段复制" /></label><label>标签<input value={input.tag ?? ''} onChange={event => setInput({ ...input, tag: clampToBytes(event.target.value, 100) })} placeholder="例如：概率基础" /></label><label>关联学习方案<select value={input.planId ?? ''} onChange={event => setInput({ ...input, planId: event.target.value || null, nodeId: null })}><option value="">不关联方案</option>{plans.map(plan => <option key={plan.id} value={plan.id}>{plan.goal}</option>)}</select></label>{input.planId && <label>关联知识节点<select value={input.nodeId ?? ''} onChange={event => setInput({ ...input, nodeId: event.target.value || null })}><option value="">整个方案</option>{nodes.map(node => <option key={node.id} value={node.id}>{node.title}</option>)}</select></label>}<Button onClick={() => void save()} disabled={busy || !input.front.trim() || !input.back.trim()}>{editing ? '保存修改' : '加入复习队列'}</Button></div></Modal>
    <Modal open={manageOpen} onClose={() => setManageOpen(false)} title={`管理 ${cards.length} 张记忆卡`} wide><div className="review-card-list">{cards.length === 0 && <p>还没有记忆卡。关闭此窗口后可创建第一张。</p>}{cards.length >= 200 && <p className="form-help">列表按到期时间排序，最多显示前 200 张；更早到期的旧卡仍会正常进入复习队列。</p>}{cards.map(item => <div className="review-card-row" key={item.id}><div><Badge tone={item.suspended ? 'amber' : 'neutral'}>{item.suspended ? '已暂停' : item.tag}</Badge><h3>{item.front}</h3><p>到期：{new Date(item.dueAt).toLocaleString()} · 间隔 {item.intervalDays.toFixed(2)} 天 · 难度系数 {item.ease.toFixed(2)}</p><div className="row-actions"><Button size="sm" variant="secondary" onClick={() => edit(item)}>编辑</Button><Button size="sm" variant="ghost" onClick={() => void suspend(item)}>{item.suspended ? '恢复' : '暂停'}</Button><Button size="sm" variant="ghost" onClick={() => void showHistory(item.id)}>{historyCard === item.id ? '收起历史' : '历史'}</Button><Button size="sm" variant="ghost" onClick={() => void remove(item)}>删除</Button></div>{historyCard === item.id && <div>{history.length ? history.map(entry => <p key={entry.id}>{new Date(entry.reviewedAt).toLocaleString()} · {gradeNames[entry.grade]} · {entry.previousInterval.toFixed(2)} → {entry.nextInterval.toFixed(2)} 天</p>) : <p>这张卡片还没有复习历史。</p>}</div>}</div></div>)}</div></Modal>
  </div>
}

import { useState } from 'react'
import { BookOpen, Check, Clock3, Flame, LoaderCircle, NotebookPen, Target, TrendingUp } from 'lucide-react'
import { Badge, Button, EmptyState, MiniBars, Modal, ProgressBar, SectionHeader } from './UI'
import { reportBackendError } from '../backend/client'
import { reportsBackend } from '../backend/reports'
import { clampToBytes } from '../limits'
import type { CalendarReport as CalendarData, LearningReport, PlanStats } from '../backend/reports'
import type { PageId } from '../data'

export function CalendarReport({ data, onSaved, notify }: { data: CalendarData | null; onSaved: (data: CalendarData) => void; notify: (message: string) => void }) {
  const [selected, setSelected] = useState<number | null>(null)
  const [date, setDate] = useState('')
  const [minutes, setMinutes] = useState(30)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const open = (index: number) => {
    if (!data) return
    const day = data.days[index]
    setSelected(index)
    setDate(day.date)
    setMinutes(day.minutes || 30)
    setNote(day.note)
  }
  const save = async () => {
    if (!date || !note.trim() || busy) return
    setBusy(true)
    try {
      const result = await reportsBackend.saveCheckin({ date, minutes, note: note.trim() })
      onSaved(result)
      setSelected(null)
      notify('真实学习打卡已保存')
    } catch (error) { reportBackendError(error, 'write') } finally { setBusy(false) }
  }
  if (!data) return <div className="page-loading"><LoaderCircle size={22} className="spin" />正在读取真实打卡…</div>
  const latest = data.days.filter(day => day.minutes > 0).slice(-7).reverse()
  return <div className="calendar-report"><div className="checkin-metrics"><div><Flame size={22} /><span>连续记录</span><strong>{data.streak}<small>天</small></strong></div><div><Clock3 size={22} /><span>84 天记录</span><strong>{(data.totalMinutes / 60).toFixed(1)}<small>小时</small></strong></div><div><NotebookPen size={22} /><span>学习笔记</span><strong>{data.noteCount}<small>条</small></strong></div><div><Target size={22} /><span>打卡率</span><strong>{data.checkinRate}<small>%</small></strong></div></div><section className="surface heatmap-panel"><SectionHeader title="84 天学习热力图" description="分钟来自真实计时或你明确保存的打卡；点击可补充当日记录。" action={<Button onClick={() => open(data.days.length - 1)}><NotebookPen size={16} />记录今天</Button>} /><div className="learning-heatmap">{data.days.map((day, index) => <button key={day.date} className={`heat-${Math.min(4, Math.floor(day.minutes / 18))} ${day.minutes > 0 ? 'has-record' : ''}`} aria-label={`${day.date}，${day.minutes} 分钟，${day.source === 'self_report' ? '手动打卡' : day.source === 'timer' ? '专注计时' : '未记录'}`} onClick={() => open(index)} />)}</div><div className="heatmap-legend"><span>少</span>{[0,1,2,3,4].map(i=><i key={i} className={`heat-${i}`} />)}<span>多</span></div><div className="checkin-note"><BookOpen size={17} /><p>连续记录只是事实展示，不需要为了保持天数而补写不存在学习活动。</p></div></section><section className="surface daily-records"><SectionHeader title="最近学习打卡" description="自报打卡与真实专注均保留来源。" />{latest.length ? latest.map(day => <div key={day.date}><span>{day.date}</span><strong>{day.minutes} 分钟</strong><p>{day.note || '来自真实专注计时'}</p><Badge tone="green"><Check size={13} />{day.source === 'self_report' ? '手动打卡' : '专注计时'}</Badge></div>) : <EmptyState title="尚无学习打卡" description="完成一次专注或保存一条真实复盘后，这里会出现记录。" />}</section><Modal open={selected !== null} onClose={() => setSelected(null)} title="记录学习打卡"><div className="form-stack"><label>学习日期<input type="date" min={data.days[0]?.date} max={data.days[data.days.length - 1]?.date} value={date} onChange={event => setDate(event.target.value)} /><p className="form-help">日历与热力图只统计最近 84 天，窗口外日期不会展示。</p></label><label>有效学习分钟数<input type="number" min={1} max={1440} value={minutes} onChange={event => setMinutes(Number(event.target.value))} /></label><label>今日学习笔记<textarea rows={5} value={note} onChange={event => setNote(clampToBytes(event.target.value, 4000))} placeholder="今天理解了什么？还有什么疑问？" /></label><Button disabled={busy || !note.trim() || !date || minutes < 1 || minutes > 1440} onClick={() => void save()}>{busy ? '正在保存…' : '保存打卡'}</Button></div></Modal></div>
}

function PlanFacts({ plan }: { plan: PlanStats }) {
  return <div className="plan-report-facts"><span><Clock3 size={15} />{(plan.focusSeconds / 3600).toFixed(1)} 小时</span><span><BookOpen size={15} />{plan.practiceCount} 组练习</span><span><Target size={15} />{plan.completedStages} / {plan.totalStages} 阶段</span></div>
}

export function PlanReport({ report, onNavigate, onAdopt, busy }: { report: LearningReport; onNavigate: (page: PageId) => void; onAdopt: (ids: string[]) => Promise<void>; busy: boolean }) {
  const lowest = report.plans.length > 1 ? report.plans.reduce((left, right) => left.focusSeconds < right.focusSeconds ? left : right) : null
  return <div className="plan-report"><section className="surface plan-report-overview"><SectionHeader title="按学习方案统计" description="进度、阶段、练习和关联专注均来自当前工作区。" /><div className="plan-report-list">{report.plans.map(plan => <button key={plan.id} onClick={() => onNavigate('plan')}><div><Badge tone="neutral">学习方案</Badge><h2>{plan.title}</h2><p>{plan.status} · 生成步骤 {plan.progress}%</p></div><div className="plan-report-progress"><strong>{plan.progress}%</strong><ProgressBar value={plan.progress} /></div><PlanFacts plan={plan} /><Badge tone={plan.completedStages === plan.totalStages && plan.totalStages > 0 ? 'green' : 'amber'}>{plan.completedStages} / {plan.totalStages} 阶段已读</Badge></button>)}{!report.plans.length && <EmptyState title="尚未生成学习方案" description="创建并生成真实方案后，才会出现按方案的统计。" />}</div></section><div className="plan-report-bottom"><section className="surface"><SectionHeader title="各方案近 7 天专注" description="仅计算与方案任务关联的已完成专注，小时。" /><MiniBars values={report.weekly.map(item => item.seconds / 3600)} labels={report.weekly.map(item => item.kind)} /></section><section className="surface"><SectionHeader title="跨方案观察" /><div className="cross-plan-insight"><TrendingUp size={20} />{lowest ? <><h3>{lowest.title}的关联专注较少</h3><p>当前关联专注为 {(lowest.focusSeconds / 3600).toFixed(1)} 小时。可以依据实际证据选择下一步建议，不会自动修改方案。</p></> : <><h3>等待更多真实记录</h3><p>至少需要两个方案及其真实专注记录，才能比较投入差异。</p></>}<Button variant="secondary" disabled={busy || !report.recommendations.length} onClick={() => void onAdopt(report.recommendations.slice(0, 1).map(item => item.id))}>{busy ? '正在写入…' : '采纳首条真实建议'}</Button></div></section></div></div>
}

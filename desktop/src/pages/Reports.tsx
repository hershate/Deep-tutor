import { useCallback, useEffect, useState } from 'react'
import { BookOpenCheck, Brain, CircleAlert, Clock3, Download, LoaderCircle, Target, TrendingUp } from 'lucide-react'
import { Badge, Button, EmptyState, LineChart, MiniBars, ProgressBar, SectionHeader } from '../components/UI'
import { CalendarReport, PlanReport } from '../components/ReportViews'
import { backendErrorMessage, onBackendEvent, reportBackendError } from '../backend/client'
import { reportsBackend } from '../backend/reports'
import type { LearningReport, CalendarReport as CalendarData, TrendPoint } from '../backend/reports'
import type { PageId } from '../data'

const periods = [{ id: '4w', label: '近 4 周' }, { id: '8w', label: '近 8 周' }, { id: 'semester', label: '本学期' }]
const percent = (value: number | null) => value === null ? '—' : `${Math.round(value * 100)}%`
const hours = (seconds: number) => (seconds / 3600).toFixed(1)

function Trend({ points }: { points: TrendPoint[] }) {
  if (!points.length) return <EmptyState title="暂无掌握证据" description="完成真实练习或复习后，这里才会出现按日期统计的掌握估计。" />
  const values = points.map(point => Math.round((point.value ?? 0) * 100))
  const labels = points.map(point => point.label)
  return points.length > 1 ? <LineChart values={values} labels={labels} /> : <MiniBars values={values} labels={labels} />
}

export default function Reports({ notify, onNavigate }: { notify: (message: string) => void; onNavigate: (page: PageId) => void }) {
  const [period, setPeriod] = useState('8w')
  const [view, setView] = useState('总览')
  const [report, setReport] = useState<LearningReport | null>(null)
  const [calendar, setCalendar] = useState<CalendarData | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [selected, setSelected] = useState<string[]>([])

  const refresh = useCallback(async () => {
    try {
      const [next, days] = await Promise.all([reportsBackend.report(period), reportsBackend.calendar()])
      setReport(next)
      setCalendar(days)
      setError('')
    } catch (cause) { setError(backendErrorMessage(cause)); reportBackendError(cause) }
  }, [period])
  useEffect(() => {
    let active = true
    let stop: (() => void) | undefined
    void refresh()
    void onBackendEvent(event => {
      if (active && ['focus.', 'study.checkin', 'practice.', 'review.', 'plan.', 'report.', 'state.changed'].some(prefix => event.kind.startsWith(prefix))) void refresh()
    }).then(unlisten => { if (active) stop = unlisten; else unlisten() }).catch(reportBackendError)
    return () => { active = false; stop?.() }
  }, [refresh])
  // 后台事件频繁重拉报告：只清掉已不存在的建议勾选，保留仍然有效的选择
  useEffect(() => { setSelected(previous => previous.filter(id => report?.recommendations.some(item => item.id === id) ?? false)) }, [report])

  const exportReport = async () => {
    setBusy('export')
    try {
      const destination = await reportsBackend.save()
      if (!destination) return
      const result = await reportsBackend.export(destination, period)
      notify(`真实学习报告已导出：${result.bytes.toLocaleString()} 字节`)
    } catch (error) { reportBackendError(error) } finally { setBusy('') }
  }
  const adopt = async (ids: string[]) => {
    if (!ids.length || busy) return
    setBusy('adopt')
    try {
      const result = await reportsBackend.adopt(ids, period)
      notify(result.focus ? '已根据建议开始真实专注计时' : `已按真实建议加入 ${result.added} 项任务`)
      await refresh()
    } catch (error) { reportBackendError(error) } finally { setBusy('') }
  }
  const metrics = report?.metrics
  const difference = metrics && metrics.previousFocusSeconds > 0 ? (metrics.focusSeconds - metrics.previousFocusSeconds) / metrics.previousFocusSeconds : null
  return <div className="page reports-page">
    <div className="page-heading"><div><h1>学习报告</h1><p>只展示真实记录；没有证据时不以示例数字代替。</p></div><div className="heading-actions"><div className="segmented">{periods.map(item => <button key={item.id} className={period === item.id ? 'active' : ''} onClick={() => setPeriod(item.id)}>{item.label}</button>)}</div><Button variant="secondary" disabled={busy === 'export'} onClick={() => void exportReport()}>{busy === 'export' ? <LoaderCircle size={16} className="spin" /> : <Download size={17} />}导出</Button></div></div>
    <div className="report-view-tabs">{['总览', '日历', '按方案'].map(item => <button key={item} className={view === item ? 'active' : ''} onClick={() => setView(item)}>{item}</button>)}</div>
    {error && <div className="form-error" role="alert"><CircleAlert size={16} /><span>{error}</span><Button variant="ghost" size="sm" onClick={() => { setError(''); void refresh() }}>重新读取</Button></div>}
    {!report ? error ? null : <div className="page-loading"><LoaderCircle size={22} className="spin" />正在读取真实学习记录…</div> : <>
      {view === '日历' && <CalendarReport data={calendar} onSaved={setCalendar} notify={notify} />}
      {view === '按方案' && <PlanReport report={report} onNavigate={onNavigate} onAdopt={adopt} busy={busy === 'adopt'} />}
      {view === '总览' && <>
        <div className="report-metrics"><div><span><Clock3 size={17} />有效学习记录</span><strong>{hours(metrics!.focusSeconds)}<small>小时</small></strong><p>{difference === null ? '按真实专注与已完成任务时间计算' : `${difference >= 0 ? '较上一周期增加' : '较上一周期减少'} ${Math.abs(difference * 100).toFixed(1)}%`}</p></div><div><span><BookOpenCheck size={17} />主动练习</span><strong>{metrics!.practiceCount}<small>题</small></strong><p>正确率 {percent(metrics!.correctPercent)}</p></div><div><span><Brain size={17} />记忆保持率</span><strong>{percent(metrics!.retentionPercent)}</strong><p>仅统计本周期真实复习</p></div><div><span><Target size={17} />目标状态</span><strong>{metrics!.goalStatus}</strong><p>{metrics!.goalDetail}</p></div></div>
        <div className="report-main-grid"><section className="surface report-trend"><SectionHeader title="掌握度变化" description="按真实证据日期计算，不把学习时长当作掌握度。" action={<Badge tone="green"><TrendingUp size={13} />真实证据</Badge>} /><Trend points={report.trend} /></section><aside className="surface time-composition"><SectionHeader title="专注时间构成" description={`已完成专注共 ${hours(report.timeComposition.reduce((sum, item) => sum + item.seconds, 0))} 小时`} />{report.timeComposition.length ? <div className="donut-legend">{report.timeComposition.map(item => <div key={item.kind}><span>{item.kind}</span><strong>{hours(item.seconds)}h</strong><ProgressBar value={Math.round(item.seconds / Math.max(1, report.timeComposition.reduce((sum, entry) => sum + entry.seconds, 0)) * 100)} /></div>)}</div> : <EmptyState title="暂无已完成专注" description="开始并完成一次真实计时后，将按任务类型展示投入。" />}</aside></div>
        <div className="report-secondary-grid"><section className="surface mastery-table"><SectionHeader title="能力证据明细" description="掌握估计、练习与复习分别来自实际记录。" /><div className="table-wrap"><table><thead><tr><th>知识领域</th><th>掌握估计</th><th>练习正确率</th><th>复习保持</th><th>证据</th></tr></thead><tbody>{report.masteries.map(row => <tr key={`${row.planId}-${row.nodeId}`}><td><strong>{row.title}</strong></td><td><div className="table-progress"><ProgressBar value={Math.round((row.mastery ?? 0) * 100)} /><span>{percent(row.mastery)}</span></div></td><td>{percent(row.practiceAccuracy)}</td><td>{percent(row.retention)}</td><td>{row.evidenceCount} 条</td></tr>)}</tbody></table>{!report.masteries.length && <EmptyState title="没有能力证据" description="当前周期没有可计算的练习或复习记录。" />}</div></section><aside className="surface report-recommendations"><SectionHeader title="下一步建议" description="根据当前真实证据生成；采纳后才写入任务。" />{report.recommendations.map((item, index) => <label className="recommendation-item" key={item.id}><input type="checkbox" checked={selected.includes(item.id)} onChange={() => setSelected(previous => previous.includes(item.id) ? previous.filter(id => id !== item.id) : [...previous, item.id])} /><span>{String(index + 1).padStart(2, '0')}</span><div><h3>{item.title}</h3><p>{item.detail}</p></div></label>)}<Button variant="secondary" disabled={!selected.length || busy === 'adopt'} onClick={() => void adopt(selected)}>{busy === 'adopt' ? '正在写入真实任务…' : '采纳所选建议'}</Button></aside></div>
        <div className="report-method"><CircleAlert size={16} /><span>指标说明：专注使用实际秒数，已完成任务使用其保存的方案分钟；掌握估计仅来自练习与复习证据，不代表标准化考试成绩。</span></div>
      </>}
    </>}
  </div>
}

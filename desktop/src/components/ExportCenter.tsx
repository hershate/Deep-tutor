import { useEffect, useState } from 'react'
import { ArrowDownToLine, CalendarDays, Check, Code2, FileArchive, FileText, Layers3, LoaderCircle, Presentation, Table2 } from 'lucide-react'
import { Badge, Button, Modal } from './UI'
import { backend, backendErrorMessage } from '../backend/client'
import type { LearningPlan, Lecture } from '../backend/client'
import { exportBackend } from '../backend/export'
import type { ExportFormat, ExportResult, ExportScope } from '../backend/export'

const formats: { id: ExportFormat; name: string; description: string; icon: typeof FileText; ext: string }[] = [
  { id: 'md', name: 'Markdown', description: '真实方案、讲义与笔记结构', icon: FileText, ext: 'md' },
  { id: 'pdf', name: 'PDF', description: '原生分页并嵌入 Unicode 字体', icon: FileArchive, ext: 'pdf' },
  { id: 'ics', name: 'ICS 日历', description: '按真实排期生成全天日程', icon: CalendarDays, ext: 'ics' },
  { id: 'apkg', name: 'Anki APKG', description: '包含真实 SQLite 卡片库的归档', icon: Layers3, ext: 'apkg' },
  { id: 'html', name: 'HTML 文档', description: '无脚本、无外部依赖的静态页面', icon: Presentation, ext: 'html' },
  { id: 'csv', name: 'CSV', description: '按实际范围导出结构化内容', icon: Table2, ext: 'csv' },
  { id: 'txt', name: 'TXT', description: '保留实际内容的纯文本', icon: Code2, ext: 'txt' },
  { id: 'opml', name: 'OPML', description: '真实层级的大纲结构', icon: Code2, ext: 'opml' },
]
const scopes: { id: ExportScope; label: string }[] = [
  { id: 'plan', label: '完整学习方案' }, { id: 'lecture', label: '当前单元讲义' },
  { id: 'knowledge', label: '笔记与知识库' }, { id: 'review', label: '复习卡片' }, { id: 'schedule', label: '学习日程' },
]

function fileName(title: string, extension: string) {
  const safe = title.replace(/[\\/:*?"<>|]/g, '_').replace(/[.\s]+$/, '')
  return `${safe.slice(0, 80) || 'DeepTutor-export'}.${extension}`
}

export default function ExportCenter({ open, onClose, notify, initialPlanId, initialScope = 'plan' }: {
  open: boolean
  onClose: () => void
  notify: (message: string) => void
  initialPlanId?: string
  initialScope?: ExportScope
}) {
  const [scope, setScope] = useState<ExportScope>(initialScope)
  const [plans, setPlans] = useState<LearningPlan[]>([])
  const [planId, setPlanId] = useState(initialPlanId || '')
  const [stageId, setStageId] = useState('')
  const [lecture, setLecture] = useState<Lecture | null>(null)
  const [unitIndex, setUnitIndex] = useState<number | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [done, setDone] = useState<ExportResult | null>(null)

  useEffect(() => {
    if (!open) return
    let active = true
    setBusy('load')
    setError('')
    backend.listLearningPlans().then(items => {
      if (!active) return
      const ready = items.filter(plan => plan.result && ['done', 'degraded'].includes(plan.status))
      setPlans(ready)
      setPlanId(current => ready.some(plan => plan.id === current) ? current : ready.find(plan => plan.id === initialPlanId)?.id || ready[0]?.id || '')
    }).catch(cause => { if (active) setError(backendErrorMessage(cause)) }).finally(() => { if (active) setBusy('') })
    return () => { active = false }
  }, [open, initialPlanId])

  const plan = plans.find(item => item.id === planId) || null
  useEffect(() => {
    if (!plan?.result || scope !== 'lecture') { setStageId(''); return }
    setStageId(current => plan.result?.stages.some(stage => stage.id === current) ? current : plan.result?.stages[0]?.id || '')
  }, [plan, scope])

  useEffect(() => {
    if (!open || scope !== 'lecture' || !planId || !stageId) { setLecture(null); setUnitIndex(null); return }
    let active = true
    setBusy('lecture')
    backend.getLecture(planId, stageId).then(result => {
      if (!active) return
      setLecture(result)
      const current = result.units.find(unit => unit.index === result.currentUnit && unit.status === 'done')
      const first = current || result.units.find(unit => unit.status === 'done')
      setUnitIndex(first?.index ?? null)
    }).catch(cause => { if (active) { setLecture(null); setUnitIndex(null); setError(backendErrorMessage(cause)) } }).finally(() => { if (active) setBusy('') })
    return () => { active = false }
  }, [open, scope, planId, stageId])

  const available = formats.filter(format => format.id === 'ics' ? scope === 'schedule' : format.id === 'apkg' ? scope !== 'schedule' : true)
  const needsPlan = ['plan', 'lecture', 'schedule'].includes(scope)
  const title = scope === 'lecture' ? lecture?.units.find(unit => unit.index === unitIndex)?.content?.title || 'DeepTutor-lecture' : plan?.result?.goal || scopes.find(item => item.id === scope)?.label || 'DeepTutor-export'
  const canExport = (!needsPlan || Boolean(plan?.result)) && (scope !== 'lecture' || unitIndex !== null)

  const run = async (format: ExportFormat, extension: string) => {
    setBusy(format)
    setError('')
    setDone(null)
    try {
      const destination = await backend.saveMaterialFile(fileName(title, extension), extension)
      if (!destination) return
      const result = await exportBackend.content({ scope, format, planId: needsPlan ? planId : null, stageId: scope === 'lecture' ? stageId : null, unitIndex: scope === 'lecture' ? unitIndex : null, version: null }, destination)
      setDone(result)
      notify(`已原生导出「${result.title}」，共 ${result.bytes.toLocaleString()} 字节`)
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }

  return <Modal open={open} onClose={onClose} title="导出学习内容" wide><div className="export-center">
    <div className="export-scope"><label>导出范围<select value={scope} onChange={event => { setScope(event.target.value as ExportScope); setDone(null); setError('') }}>{scopes.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label><div><Badge tone="green">Rust 原生文件生成</Badge><p>内容来自 SQLite 和已保存讲义。PDF 不打开打印窗口；Anki 导出真实 APKG。</p></div></div>
    {needsPlan && <label>学习方案<select value={planId} onChange={event => { setPlanId(event.target.value); setDone(null) }} disabled={!plans.length}><option value="">请选择已完整生成的方案</option>{plans.map(item => <option key={item.id} value={item.id}>{item.goal} · v{item.version}</option>)}</select></label>}
    {scope === 'lecture' && <div className="form-grid"><label>学习阶段<select value={stageId} onChange={event => setStageId(event.target.value)} disabled={!plan?.result}><option value="">请选择阶段</option>{plan?.result?.stages.map(stage => <option key={stage.id} value={stage.id}>{stage.title}</option>)}</select></label><label>已保存单元<select value={unitIndex ?? ''} onChange={event => setUnitIndex(Number(event.target.value))} disabled={!lecture}><option value="">请选择已生成单元</option>{lecture?.units.filter(unit => unit.status === 'done').map(unit => <option key={unit.index} value={unit.index}>{unit.index + 1} · {unit.content?.title || unit.nodeId}</option>)}</select></label></div>}
    {error && <div className="form-error" role="alert">{error}</div>}
    <div className="export-grid">{available.map(format => <button key={format.id} onClick={() => void run(format.id, format.ext)} disabled={Boolean(busy) || !canExport}><format.icon size={22} /><div><strong>{format.name}</strong><span>{format.description}</span></div>{busy === format.id ? <LoaderCircle size={18} className="spin" /> : done?.format === format.id ? <Check size={18} /> : <ArrowDownToLine size={18} />}</button>)}</div>
    {done && <div className="export-receipt" role="status"><Check size={16} /><div><strong>{done.title}</strong><p>已写入 {done.bytes.toLocaleString()} 字节 · SHA-256 {done.sha256}</p></div></div>}
    <div className="export-foot"><span>动态文本按格式转义；HTML 不执行脚本。只有用户选择保存位置后才写入文件。</span><Button variant="secondary" onClick={onClose}>完成</Button></div>
  </div></Modal>
}

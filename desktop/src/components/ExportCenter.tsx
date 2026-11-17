import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowDownToLine, CalendarDays, Check, Code2, FileArchive, FileText, FolderOpen, Layers3, LoaderCircle, Presentation, Table2 } from 'lucide-react'
import { Badge, Button, Modal } from './UI'
import { backend, backendErrorMessage, onBackendEvent } from '../backend/client'
import type { LearningPlan, Lecture } from '../backend/client'
import { exportBackend } from '../backend/export'
import { revealInFileManager } from '../backend/shell'
import type { ExportFormat, ExportResult, ExportScope } from '../backend/export'

const formats: { id: ExportFormat; name: string; description: string; icon: typeof FileText; ext: string }[] = [
  { id: 'md', name: 'Markdown', description: '真实方案、讲义与笔记结构', icon: FileText, ext: 'md' },
  { id: 'pdf', name: 'PDF', description: '原生分页并嵌入 Unicode 字体', icon: FileArchive, ext: 'pdf' },
  { id: 'ics', name: 'ICS 日历', description: '按真实排期生成全天日程', icon: CalendarDays, ext: 'ics' },
  { id: 'apkg', name: 'Anki APKG', description: '包含真实 SQLite 记忆卡库的归档', icon: Layers3, ext: 'apkg' },
  { id: 'html', name: 'HTML 文档', description: '无脚本、无外部依赖的静态页面', icon: Presentation, ext: 'html' },
  { id: 'csv', name: 'CSV', description: '按实际范围导出结构化内容', icon: Table2, ext: 'csv' },
  { id: 'txt', name: 'TXT', description: '保留实际内容的纯文本', icon: Code2, ext: 'txt' },
  { id: 'opml', name: 'OPML', description: '真实层级的大纲结构', icon: Code2, ext: 'opml' },
]
const scopes: { id: ExportScope; label: string }[] = [
  { id: 'plan', label: '完整学习方案' }, { id: 'lecture', label: '当前单元讲义' },
  { id: 'knowledge', label: '笔记与知识库' }, { id: 'review', label: '记忆卡' }, { id: 'schedule', label: '学习日程' },
]

export default function ExportCenter({ open, onClose, notify, initialPlanId }: {
  open: boolean
  onClose: () => void
  notify: (message: string) => void
  initialPlanId?: string
}) {
  const [scope, setScope] = useState<ExportScope>('plan')
  const [plans, setPlans] = useState<LearningPlan[]>([])
  const [planId, setPlanId] = useState(initialPlanId || '')
  const [stageId, setStageId] = useState('')
  const [lecture, setLecture] = useState<Lecture | null>(null)
  const [unitIndex, setUnitIndex] = useState<number | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [done, setDone] = useState<ExportResult | null>(null)
  // 37#4：回执保留用户选择的落盘位置，供「在资源管理器中显示」定位
  const [donePath, setDonePath] = useState('')
  // 120#4：列表读取失败标记——错误横幅区分「读取失败可重试」与「导出失败」
  // 121#5：错误按源标记——读取失败后任何后续错误（导出/讲义读取）原先
  // 都挂着「重试读取」按钮误导；重试入口改由 loadFailed 独立承载
  const [loadFailed, setLoadFailed] = useState(false)
  const [errorSource, setErrorSource] = useState<'load' | 'lecture' | 'export' | 'reveal'>('export')
  const [reloadToken, setReloadToken] = useState(0)
  // 方案列表读取中标记——空态提示不得在列表返回前误报（见 planEmpty）
  const [readingPlans, setReadingPlans] = useState(false)
  // 130#6：plan.* 事件重拉的清错判定读最新值（订阅闭包挂载一次，state 快照会过期）
  const loadFailedRef = useRef(false)
  loadFailedRef.current = loadFailed

  // useLayoutEffect：读取标记必须在首帧绘制前置位——passive effect（useEffect）
  // 在绘制之后才执行，弹窗打开的首帧 busy 仍是 ''，会闪出一帧「尚无已完整
  // 生成的方案」的误报空态（Modal 在 open 时才渲染子树，该帧确实可见）
  useLayoutEffect(() => {
    if (!open) { setReadingPlans(false); return }
    let active = true
    setReadingPlans(true)
    setBusy('load')
    setError('')
    setLoadFailed(false)
    backend.listLearningPlans().then(items => {
      if (!active) return
      // 148#3：讲义导出范围额外要求已采用——后端 lecture::get 对未采用方案
      // 恒报 plan_not_ready，原先未采用方案也能选中「当前单元讲义」形成
      // 必然失败的死路（完整方案/日程范围不要求采用，保持原过滤）
      const lectureOnly = scope === 'lecture'
      const ready = items.filter(plan => plan.result && ['done', 'degraded'].includes(plan.status) && (!lectureOnly || plan.adoptedProjectId !== null))
      setPlans(ready)
      setPlanId(current => ready.some(plan => plan.id === current) ? current : ready.find(plan => plan.id === initialPlanId)?.id || ready[0]?.id || '')
    }).catch(cause => { if (active) { setError(backendErrorMessage(cause)); setErrorSource('load'); setLoadFailed(true) } }).finally(() => { if (active) { setReadingPlans(false); setBusy('') } })
    return () => { active = false }
  }, [open, initialPlanId, reloadToken, scope])
  useEffect(() => {
    // 信息项：弹窗开着期间新完成的方案实时进入列表（plan. 前缀事件触发重拉）
    // 121#5：重拉成功同步清读取失败态与「读取失败」横幅（列表已可用）。
    // 130#6：只清「读取失败」态——原先无条件清 error，正在阅读的导出/讲义/
    // reveal 失败横幅被无关 plan.* 事件重拉清掉（121#5 意图只覆盖读取失败）。
    // ref 取最新值：订阅挂载一次（51#9），闭包内的 loadFailed 是挂载时快照
    if (!open) return
    let unlisten: (() => void) | undefined
    let disposed = false
    void onBackendEvent(event => {
      if (disposed || !event.kind.startsWith('plan.')) return
      void backend.listLearningPlans().then(items => {
        if (disposed) return
        setPlans(items.filter(plan => plan.result && ['done', 'degraded'].includes(plan.status) && (scope !== 'lecture' || plan.adoptedProjectId !== null)))
        if (loadFailedRef.current) {
          loadFailedRef.current = false
          setLoadFailed(false)
          setErrorSource('export')
          setError('')
        }
      }).catch(() => {})
      // 136#2：订阅链补 .catch——事件系统故障时与全仓其余订阅同口径提示，
      // 不留 unhandled rejection
    }).then(remove => { if (disposed) remove(); else unlisten = remove }).catch(() => {})
    return () => { disposed = true; unlisten?.() }
  }, [open, scope])

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
    }).catch(cause => { if (active) { setLecture(null); setUnitIndex(null); setError(backendErrorMessage(cause)); setErrorSource('lecture') } }).finally(() => { if (active) setBusy('') })
    return () => { active = false }
  }, [open, scope, planId, stageId])

  const available = formats.filter(format => format.id === 'ics' ? scope === 'schedule' : format.id === 'apkg' ? scope !== 'schedule' : true)
  const needsPlan = ['plan', 'lecture', 'schedule'].includes(scope)
  const title = scope === 'lecture' ? lecture?.units.find(unit => unit.index === unitIndex)?.content?.title || 'DeepTutor-lecture' : plan?.result?.goal || scopes.find(item => item.id === scope)?.label || 'DeepTutor-export'
  const canExport = (!needsPlan || Boolean(plan?.result)) && (scope !== 'lecture' || unitIndex !== null)
  // P-121：空态只禁用了选择器与格式按钮，没有任何说明指出「尚无可导出的
  // 方案/单元」——用户无法分辨是功能不可用还是自身数据尚未就绪
  const doneUnits = lecture?.units.filter(unit => unit.status === 'done') ?? []
  // 读取中不报空态——方案列表尚未返回时提示「尚无可导出方案」是误报。
  // 判定读 readingPlans（读取发起时置位）而非 busy：busy 是与讲义读取、
  // 导出动作共用的单槽位，讲义读取期间会被覆盖成 'lecture'
  const planEmpty = needsPlan && !plans.length && !loadFailed && !readingPlans
  const unitEmpty = scope === 'lecture' && Boolean(lecture) && !doneUnits.length
  const planEmptyHint = scope === 'lecture'
    ? '尚无已采用并完整生成的方案：先采用方案并生成讲义后才能导出。'
    : '尚无已完整生成的方案：完成方案生成后即可导出。'

  const run = async (format: ExportFormat, extension: string) => {
    setBusy(format)
    setError('')
    try {
      const destination = await backend.saveMaterialFile(title, extension)
      if (!destination) return
      // 134#1：清旧回执挪到保存路径确认之后——取消保存对话框不再连带清掉
      // 上一次成功导出的回执与「在资源管理器中显示」入口（对齐 BackupPanel）
      setDone(null)
      setDonePath('')
      const result = await exportBackend.content({ scope, format, planId: needsPlan ? planId : null, stageId: scope === 'lecture' ? stageId : null, unitIndex: scope === 'lecture' ? unitIndex : null, version: null }, destination)
      setDone(result)
      setDonePath(destination)
      notify(`已原生导出「${result.title}」，共 ${result.bytes.toLocaleString()} 字节`)
    } catch (cause) { setError(backendErrorMessage(cause)); setErrorSource('export') } finally { setBusy('') }
  }
  const reveal = async () => {
    if (!donePath) return
    try { await revealInFileManager(donePath) } catch (cause) { setError(backendErrorMessage(cause)); setErrorSource('reveal') }
  }

  return <Modal open={open} onClose={onClose} title="导出学习内容" wide><div className="export-center">
    <div className="export-scope"><label>导出范围<select value={scope} onChange={event => { setScope(event.target.value as ExportScope); setDone(null); setError('') }}>{scopes.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label><div><Badge tone="green">Rust 原生文件生成</Badge><p>内容来自 SQLite 和已保存讲义。PDF 不打开打印窗口；Anki 导出真实 APKG。</p></div></div>
    {needsPlan && <label>学习方案<select value={planId} onChange={event => { setPlanId(event.target.value); setDone(null) }} disabled={!plans.length}><option value="">请选择已完整生成的方案</option>{plans.map(item => <option key={item.id} value={item.id}>{item.goal} · v{item.version}</option>)}</select></label>}
    {/* P-121：无可导出方案时的空态说明——原先只有禁用的选择器与置灰按钮 */}
    {planEmpty && <p className="form-help" role="status">{planEmptyHint}</p>}
    {/* 121#5：读取失败的重试入口独立于 error/scope 常驻——原先嵌在错误横幅内，
        切换导出范围清 error 时连唯一入口一起吞掉（plans 仍空、选择器禁用） */}
    {loadFailed && !plans.length && <div className="form-error" role="alert">方案列表读取失败，选择器暂不可用。<Button variant="ghost" size="sm" disabled={busy === 'load'} onClick={() => setReloadToken(token => token + 1)}>{busy === 'load' ? '正在读取…' : '重试读取'}</Button></div>}
    {scope === 'lecture' && <div className="form-grid"><label>学习阶段<select value={stageId} onChange={event => setStageId(event.target.value)} disabled={!plan?.result}><option value="">请选择阶段</option>{plan?.result?.stages.map(stage => <option key={stage.id} value={stage.id}>{stage.title}</option>)}</select></label><label>已保存单元<select value={unitIndex ?? ''} onChange={event => setUnitIndex(event.target.value === '' ? null : Number(event.target.value))} disabled={!lecture}><option value="">请选择已生成单元</option>{doneUnits.map(unit => <option key={unit.index} value={unit.index}>{unit.index + 1} · {unit.content?.title || unit.nodeId}</option>)}</select></label></div>}
    {/* P-121：该阶段尚无已生成单元时的空态说明——原先只有禁用的单元选择器 */}
    {unitEmpty && <p className="form-help" role="status">该阶段尚未生成任何单元：讲义生成完成后才能导出。</p>}
    {error && <div className="form-error" role="alert">{error}{errorSource === 'load' && <Button variant="ghost" size="sm" disabled={busy === 'load'} onClick={() => setReloadToken(token => token + 1)}>{busy === 'load' ? '正在读取…' : '重试读取'}</Button>}</div>}
    <div className="export-grid">{available.map(format => <button key={format.id} onClick={() => void run(format.id, format.ext)} disabled={Boolean(busy) || !canExport}><format.icon size={22} /><div><strong>{format.name}</strong><span>{format.description}</span></div>{busy === format.id ? <LoaderCircle size={18} className="spin" /> : done?.format === format.id ? <Check size={18} /> : <ArrowDownToLine size={18} />}</button>)}</div>
    {done && <div className="export-receipt" role="status"><Check size={16} /><div><strong>{done.title}</strong><p>已写入 {done.bytes.toLocaleString()} 字节 · SHA-256 {done.sha256}</p>{done.warning && <p className="export-warning" role="alert">{done.warning}</p>}</div>{donePath && <Button variant="ghost" size="sm" onClick={() => void reveal()}><FolderOpen size={15} />在资源管理器中显示</Button>}</div>}
    <div className="export-foot"><span>动态文本按格式转义；HTML 不执行脚本。只有用户选择保存位置后才写入文件。</span><Button variant="secondary" onClick={onClose}>完成</Button></div>
  </div></Modal>
}

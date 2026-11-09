import { useEffect, useState } from 'react'
import { AlertTriangle, Check, Database, LoaderCircle, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import { Badge, Button, Modal } from './UI'
import { onBackendEvent, reportBackendError } from '../backend/client'
import { maintenanceBackend } from '../backend/maintenance'
import type { CleanupResult, MaintenanceReport, VacuumResult } from '../backend/maintenance'

const categories = [
  ['originals', '无引用资料原件'],
  ['scratch', '无引用 Agent 临时文件'],
  ['plugins', '已卸载插件的清单'],
  ['tempdirs', '已结束的备份/导出临时项'],
  ['sync', '超量与中断的本地同步文件'],
] as const

export default function MaintenancePanel() {
  const [report, setReport] = useState<MaintenanceReport | null>(null)
  const [cleanup, setCleanup] = useState<CleanupResult | null>(null)
  const [vacuum, setVacuum] = useState<VacuumResult | null>(null)
  const [busy, setBusy] = useState('')
  const [confirmOpen, setConfirmOpen] = useState(false)
  // 74#9：整理数据库与清理一样会停止运行中任务——补确认层保持对称
  const [vacuumOpen, setVacuumOpen] = useState(false)
  const candidates = report ? categories.flatMap(([key, label]) => report.orphans[key].map(item => ({ ...item, label }))) : []

  useEffect(() => {
    // A-5：订阅运维事件——别的窗口/会话完成清理或体检时同步刷新，
    // 此前面板只靠命令返回值，跨窗口操作后显示旧状态
    let unlisten: (() => void) | undefined
    let disposed = false
    void onBackendEvent(event => {
      if (disposed || !event.kind.startsWith('maintenance.')) return
      void maintenanceBackend.report().then(setReport).catch(() => {})
    }).then(remove => { if (disposed) remove(); else unlisten = remove })
    return () => { disposed = true; unlisten?.() }
  }, [])

  const scan = async () => {
    setBusy('scan')
    try { setReport(await maintenanceBackend.report()) }
    catch (error) { reportBackendError(error) }
    finally { setBusy('') }
  }
  const clean = async () => {
    setBusy('cleanup')
    try {
      const result = await maintenanceBackend.cleanup()
      setCleanup(result)
      setVacuum(null)
      setConfirmOpen(false)
      setReport(await maintenanceBackend.report())
    } catch (error) { reportBackendError(error, 'write') }
    finally { setBusy('') }
  }
  const compact = async () => {
    setBusy('vacuum')
    try {
      setVacuum(await maintenanceBackend.vacuum())
      setReport(await maintenanceBackend.report())
    } catch (error) { reportBackendError(error, 'write') }
    finally { setBusy('') }
  }

  return <div className="maintenance-panel">
    <div className="backup-status"><ShieldCheck size={21} /><div><h3>本地数据检查与运维</h3><p>读取真实 SQLite 健康状态；只清理数据库不再引用的应用文件和可识别的临时项。</p></div>{report && <Badge tone="green">完整性 {report.database.integrity}</Badge>}</div>
    <div className="data-actions"><Button variant="secondary" disabled={Boolean(busy)} onClick={() => void scan()}>{busy === 'scan' ? <LoaderCircle size={16} className="spin" /> : <RefreshCw size={16} />}{report ? '重新检查' : '检查本地数据'}</Button></div>
    {report && <>
      <div className="maintenance-facts"><div><span>数据库</span><strong>{report.database.bytes.toLocaleString()} 字节</strong></div><div><span>WAL 日志</span><strong>{report.database.walBytes.toLocaleString()} 字节</strong></div><div><span>页面</span><strong>{report.database.pageCount} · 空闲 {report.database.freePages}</strong></div><div><span>结构版本</span><strong>{report.database.schemaVersion} · {report.database.journalMode.toUpperCase()}</strong></div></div>
      <p className="form-help">外键异常 {report.database.foreignKeyViolations} 条 · 页大小 {report.database.pageSize} 字节。检查发现损坏或失效外键时会拒绝清理和压缩。</p>
      {report.orphans.missingOriginals.length > 0 && <div className="maintenance-warning"><AlertTriangle size={17} /><div><strong>{report.orphans.missingOriginals.length} 条资料记录缺少原件</strong><p>请重新导入原件或删除对应资料记录；运维不会用其他文件替代，也不会把这些记录当作可清理项。</p>{report.orphans.missingOriginals.map(path => <code key={path}>{path}</code>)}</div></div>}
      {report.orphans.issues.map((issue, index) => <p className="form-error" role="alert" key={`${issue.code}-${index}`}>{issue.code}：{issue.message}</p>)}
      <div className="data-actions"><Button variant="secondary" disabled={Boolean(busy) || !candidates.length} onClick={() => setConfirmOpen(true)}><Trash2 size={16} />预演清理{candidates.length ? `（${candidates.length} 项）` : ''}</Button><Button variant="secondary" disabled={Boolean(busy)} onClick={() => setVacuumOpen(true)}>{busy === 'vacuum' ? <LoaderCircle size={16} className="spin" /> : <Database size={16} />}整理数据库</Button></div>
      {!candidates.length && <p className="form-help">当前未发现符合白名单的可清理文件。符号链接、未知插件清单和嵌套过深的路径会保留并报告。</p>}
    </>}
    {cleanup && <div className="export-receipt" role="status">{cleanup.failures.length ? <AlertTriangle size={17} /> : <Check size={17} />}<div><strong>已删除 {cleanup.deletedFiles} 个文件、{cleanup.deletedTempItems} 个临时项</strong><p>按删除前记录合计 {cleanup.freedBytes.toLocaleString()} 字节；请以上方重新扫描结果为准。</p>{cleanup.remaining > 0 && <p>本次批量上限已满，仍有 {cleanup.remaining} 项未处理；再次点击清理可继续。</p>}{cleanup.failures.map((failure, index) => <p className="form-error" key={`${failure.code}-${index}`}>{failure.code}：{failure.message}</p>)}</div></div>}
    {vacuum && <div className="export-receipt" role="status"><Check size={17} /><div><strong>数据库整理与 WAL 检查点已完成</strong><p>数据库 {vacuum.beforeBytes.toLocaleString()} → {vacuum.afterBytes.toLocaleString()} 字节 · {vacuum.journalMode.toUpperCase()}</p><p>检查点：{vacuum.walBusy === 0 ? '未阻塞' : `阻塞 ${vacuum.walBusy}`} · 已回收日志页 {vacuum.walCheckpointed} · 剩余日志页 {vacuum.walLog}。WAL 回填可能使主数据库暂时增大。</p></div></div>}
    <Modal open={vacuumOpen} onClose={() => { if (!busy) setVacuumOpen(false) }} title="确认整理数据库？"><div className="maintenance-confirm"><p>数据库整理会运行完整性检查并重写整库文件（VACUUM），期间正在运行的生成、模型对话和同步任务会被停止，完成后恢复自动同步调度。</p><div className="confirm-actions"><Button variant="secondary" disabled={Boolean(busy)} onClick={() => setVacuumOpen(false)}>取消</Button><Button disabled={Boolean(busy)} onClick={() => { setVacuumOpen(false); void compact() }}>{busy === 'vacuum' ? <LoaderCircle size={16} className="spin" /> : <Database size={16} />}{busy === 'vacuum' ? '正在整理…' : '确认整理'}</Button></div></div></Modal>
    <Modal open={confirmOpen} onClose={() => { if (!busy) setConfirmOpen(false) }} title="确认清理无引用文件？">
      <div className="maintenance-confirm"><p>后端会重新扫描，而不是信任这份旧清单。仅删除下列类别中仍无引用的文件；不会删除数据库记录或缺失的原件。</p><div className="modal-callout"><AlertTriangle size={18} />此操作与数据库整理会停止正在运行的生成、模型对话和同步任务，完成后恢复自动同步调度。</div><div className="maintenance-candidates">{candidates.map(item => <div key={`${item.label}-${item.path}`}><span>{item.label}</span><code>{item.path}</code><small>{item.bytes.toLocaleString()} 字节</small></div>)}</div><div className="confirm-actions"><Button variant="secondary" disabled={Boolean(busy)} onClick={() => setConfirmOpen(false)}>取消</Button><Button disabled={Boolean(busy)} onClick={() => void clean()}>{busy === 'cleanup' ? <LoaderCircle size={16} className="spin" /> : <Trash2 size={16} />}{busy === 'cleanup' ? '正在停止任务并清理…' : '确认清理'}</Button></div></div>
    </Modal>
  </div>
}

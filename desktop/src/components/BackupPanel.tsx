import { useState } from 'react'
import { AlertTriangle, Check, Database, Download, FileArchive, HardDriveDownload, LoaderCircle, Upload } from 'lucide-react'
import { Badge, Button, Modal } from './UI'
import { reportBackendError } from '../backend/client'
import type { AppSnapshot } from '../backend/client'
import { backupBackend } from '../backend/backup'
import type { BackupPreview, BackupResult } from '../backend/backup'
import MaintenancePanel from './MaintenancePanel'

const collectionNames: Record<string, string> = { tasks: '任务', notes: '笔记', projects: '项目', drafts: '草稿' }
const tableNames: Record<string, string> = { learning_plans: '学习方案', materials: '资料记录', review_cards: '复习卡', practice_sets: '练习组', agent_sessions: 'Agent 会话', shared_plans: '分享快照' }

export default function BackupPanel({ onImport, counts, notify }: { onImport: (path: string) => Promise<AppSnapshot>; counts: { tasks: number; notes: number; resources: number; projects: number }; notify: (message: string) => void }) {
  const [preview, setPreview] = useState<{ path: string; data: BackupPreview } | null>(null)
  const [exported, setExported] = useState<BackupResult | null>(null)
  const [busy, setBusy] = useState('')

  const exportBackup = async () => {
    setBusy('export')
    try {
      const destination = await backupBackend.save()
      if (!destination) return
      const result = await backupBackend.export(destination)
      setExported(result)
      notify(result.missingFiles.length
        ? `已保存全量备份：${result.records} 条记录、${result.files} 个文件；另有 ${result.missingFiles.length} 个已缺失文件被跳过`
        : `已保存全量备份：${result.records} 条记录、${result.files} 个文件`)
    } catch (error) { reportBackendError(error, 'write') } finally { setBusy('') }
  }
  const chooseBackup = async () => {
    setBusy('preview')
    try {
      const path = await backupBackend.open()
      if (!path) return
      const data = await backupBackend.preview(path)
      setPreview({ path, data })
    } catch (error) { reportBackendError(error) } finally { setBusy('') }
  }
  const confirmImport = async () => {
    if (!preview || busy) return
    setBusy('import')
    try {
      const snapshot = await onImport(preview.path)
      setPreview(null)
      // A-20：部分域 recover 失败被后端降级为警告——此前恒报成功 toast，
      // 用户不知道有功能待重启后由常规 recover 兜底
      const warnings = snapshot.backupImportWarnings ?? []
      notify(warnings.length
        ? `导入完成，但 ${warnings.length} 项恢复未完成（重启后自动重试）：${warnings[0]}`
        : '备份已重新校验并追加导入；现有数据未被覆盖')
    } catch (error) { reportBackendError(error, 'write') } finally { setBusy('') }
  }
  return <><div className="backup-panel"><div className="backup-status"><Database size={21} /><div><h3>Tauri 原生全量备份</h3><p>一致性 SQLite 快照、资料原件、声明式插件与 Agent 工作区由后端统一归档。</p></div><Badge tone="green">原生后端</Badge></div><div className="backup-counts">{Object.entries(counts).map(([key, value]) => <div key={key}><strong>{value}</strong><span>{{ tasks: '任务', notes: '笔记', resources: 'KB 文档', projects: '项目' }[key]}</span></div>)}</div><div className="data-actions"><Button variant="secondary" onClick={() => void exportBackup()} disabled={Boolean(busy)}>{busy === 'export' ? <LoaderCircle size={16} className="spin" /> : <Download size={17} />}导出全量备份</Button><Button variant="secondary" onClick={() => void chooseBackup()} disabled={Boolean(busy)}>{busy === 'preview' ? <LoaderCircle size={16} className="spin" /> : <Upload size={17} />}选择备份并预演</Button></div>{exported && <div className="export-receipt" role="status"><Check size={16} /><div><strong>备份已写入并核对</strong><p>{exported.bytes.toLocaleString()} 字节 · SHA-256 {exported.sha256}</p>{exported.missingFiles.length > 0 && <p className="form-error">备份时有 {exported.missingFiles.length} 个文件已缺失，按"只报告不替代"跳过：{exported.missingFiles.slice(0, 3).join('、')}{exported.missingFiles.length > 3 ? ' 等' : ''}</p>}</div></div>}<details className="backup-coverage"><summary>查看备份覆盖与密钥边界</summary><div><span><Check size={14} />原生领域表、任务、笔记、项目、偏好、草稿、历史与资料原件</span><span><Check size={14} />声明式插件清单、Agent scratch 产物；路径与逐文件哈希均校验</span><span><AlertTriangle size={14} />API Key、PAT 摘要、JWT 会话与分享原始令牌不进入备份；模型凭据需重新配置，登录与 PAT 需重新创建</span><span><AlertTriangle size={14} />校验用于发现损坏，不证明备份的发送者身份</span></div></details><MaintenancePanel /></div>
  <Modal open={Boolean(preview)} onClose={() => setPreview(null)} title="导入预演 · 不会覆盖现有内容">{preview && <div className="backup-preview"><div className="backup-file"><FileArchive size={22} /><div><strong>{preview.path.split(/[\\/]/).at(-1)}</strong><span>第三版全量备份 · 创建于 {new Date(preview.data.createdAt).toLocaleString()}</span></div></div><div className="backup-preview-grid">{[...Object.entries(preview.data.collectionCounts).map(([key, count]) => ({ key, count, label: collectionNames[key] || key })), ...Object.entries(tableNames).map(([key, label]) => ({ key, count: preview.data.tableCounts[key] || 0, label }))].map(item => <div key={item.key}><strong>{item.count}</strong><span>{item.label}</span></div>)}</div><p className="form-help">原件与工作区文件 {preview.data.files} 个，共 {preview.data.fileBytes.toLocaleString()} 字节；数据库快照 {preview.data.databaseBytes.toLocaleString()} 字节。</p><div className="modal-callout"><HardDriveDownload size={18} />确认后重新校验数据库与文件，只追加新记录。冲突 ID 会映射，已有偏好和路由选择保留，资料按内容哈希去重。</div><div className="confirm-actions"><Button variant="secondary" onClick={() => setPreview(null)}>取消</Button><Button disabled={Boolean(busy)} onClick={() => void confirmImport()}>{busy === 'import' ? '正在校验并追加…' : '确认追加导入'}</Button></div></div>}</Modal></>
}

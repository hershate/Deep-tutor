import { useRef, useState } from 'react'
import { AlertTriangle, Check, Database, Download, FileJson, HardDriveDownload, Upload } from 'lucide-react'
import { Badge, Button, Modal } from './UI'
import { backend, reportBackendError } from '../backend/client'
import type { AppSnapshot } from '../backend/client'

type BackupData = Record<string, unknown>

export default function BackupPanel({ onExport, onImport, counts, notify }: { onExport: () => Promise<void>; onImport: (data: BackupData) => Promise<AppSnapshot>; counts: { tasks: number; notes: number; resources: number; projects: number }; notify: (message: string) => void }) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [preview, setPreview] = useState<BackupData | null>(null)
  const [previewCounts, setPreviewCounts] = useState<Record<string, number>>({})
  const [fileMeta, setFileMeta] = useState('')
  const [importing, setImporting] = useState(false)
  const confirmImport = async () => {
    if (!preview || importing) return
    setImporting(true)
    try {
      await onImport(preview)
      setPreview(null)
      notify('备份已按预演结果追加导入')
    } catch (error) { reportBackendError(error) } finally { setImporting(false) }
  }
  const read = async (file: File) => {
    if (file.size > 256 * 1024 * 1024) { notify('备份超过 256 MB 上限，已拒绝导入'); return }
    try {
      const data = JSON.parse(await file.text())
      const result = await backend.previewBackup(data)
      setPreview(data); setPreviewCounts(result.counts); setFileMeta(`${file.name} · ${(file.size / 1024).toFixed(1)} KB`)
    } catch (error) { reportBackendError(error) }
  }
  return <><div className="backup-panel"><div className="backup-status"><Database size={21} /><div><h3>Tauri 原生 SQLite</h3><p>任务、笔记、资料元数据、项目和设置由原生后端持久化。</p></div><Badge tone="green">原生后端</Badge></div><div className="backup-counts">{Object.entries(counts).map(([key,value])=><div key={key}><strong>{value}</strong><span>{{tasks:'任务',notes:'笔记',resources:'KB 文档',projects:'项目'}[key]}</span></div>)}</div><div className="data-actions"><Button variant="secondary" onClick={onExport}><Download size={17} />导出本地数据</Button><Button variant="secondary" onClick={() => fileRef.current?.click()}><Upload size={17} />导入预演</Button><input ref={fileRef} className="visually-hidden" type="file" accept=".json" onChange={e => { const file = e.target.files?.[0]; if (file) read(file); e.target.value = '' }} /></div><details className="backup-coverage"><summary>查看当前备份覆盖面</summary><div><span><Check size={14} />任务、笔记、资料元数据、项目、偏好与未保存草稿</span><span><AlertTriangle size={14} />尚未接入原件、会话、记忆与云端同步，不会出现在导出中</span><span><AlertTriangle size={14} />密钥与访问令牌不得进入备份</span></div></details></div><Modal open={Boolean(preview)} onClose={() => setPreview(null)} title="导入预演 · 不会覆盖现有内容"><div className="backup-preview"><div className="backup-file"><FileJson size={22} /><div><strong>{fileMeta}</strong><span>追加式导入 · ID 冲突时保留双方</span></div></div><div className="backup-preview-grid">{['tasks','notes','resources','projects','drafts'].map(key=><div key={key}><strong>{previewCounts[key] ?? 0}</strong><span>{{tasks:'任务',notes:'笔记',resources:'KB 文档',projects:'项目',drafts:'草稿'}[key]}</span></div>)}</div><div className="modal-callout"><HardDriveDownload size={18} />确认后只追加新记录，不删除或覆盖当前学习数据。当前备份不含资料原件。</div><div className="confirm-actions"><Button variant="secondary" onClick={() => setPreview(null)}>取消</Button><Button disabled={importing} onClick={() => { void confirmImport() }}>{importing ? '正在导入…' : '确认追加导入'}</Button></div></div></Modal></>
}

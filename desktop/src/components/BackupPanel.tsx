import { useRef, useState } from 'react'
import { AlertTriangle, Check, Database, Download, FileJson, HardDriveDownload, Upload } from 'lucide-react'
import { Badge, Button, Modal } from './UI'

type BackupData = { tasks?: unknown[]; notes?: unknown[]; resources?: unknown[]; projects?: unknown[]; conversations?: unknown[]; memories?: unknown[]; [key: string]: unknown }

export default function BackupPanel({ onExport, onImport, counts, notify }: { onExport: () => void; onImport: (data: BackupData) => void; counts: { tasks: number; notes: number; resources: number; projects: number }; notify: (message: string) => void }) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [preview, setPreview] = useState<BackupData | null>(null)
  const [fileMeta, setFileMeta] = useState('')
  const read = async (file: File) => {
    if (file.size > 256 * 1024 * 1024) { notify('备份超过 256 MB 上限，已拒绝导入'); return }
    try { const data = JSON.parse(await file.text()); setPreview(data); setFileMeta(`${file.name} · ${(file.size / 1024).toFixed(1)} KB`) } catch { notify('无法读取备份：请选择有效的 JSON 全量备份') }
  }
  return <><div className="backup-panel"><div className="backup-status"><Database size={21} /><div><h3>浏览器本地存储</h3><p>任务、笔记、项目和设置只保存在此浏览器，不会跨设备同步。清除浏览器数据会删除它们。</p></div><Badge tone="amber">前端演示</Badge></div><div className="backup-counts">{Object.entries(counts).map(([key,value])=><div key={key}><strong>{value}</strong><span>{{tasks:'任务',notes:'笔记',resources:'KB 文档',projects:'项目'}[key]}</span></div>)}</div><div className="data-actions"><Button variant="secondary" onClick={onExport}><Download size={17} />全量导出</Button><Button variant="secondary" onClick={() => fileRef.current?.click()}><Upload size={17} />导入预演</Button><input ref={fileRef} className="visually-hidden" type="file" accept=".json" onChange={e => { const file = e.target.files?.[0]; if (file) read(file); e.target.value = '' }} /></div><details className="backup-coverage"><summary>查看备份覆盖面与未纳入内容</summary><div><span><Check size={14} />任务、笔记、KB 元数据、项目与偏好</span><span><Check size={14} />演示会话与记忆（若备份中包含则追加）</span><span><AlertTriangle size={14} />真实上传原件、密钥与 PAT 明文不纳入</span><span><AlertTriangle size={14} />云端数据库、多设备同步与真实认证不包含在前端演示中</span></div></details></div><Modal open={Boolean(preview)} onClose={() => setPreview(null)} title="导入预演 · 不会覆盖现有内容"><div className="backup-preview"><div className="backup-file"><FileJson size={22} /><div><strong>{fileMeta}</strong><span>追加式导入 · ID 冲突时保留双方</span></div></div><div className="backup-preview-grid">{['tasks','notes','resources','projects','conversations','memories'].map(key=><div key={key}><strong>{Array.isArray(preview?.[key]) ? preview[key].length : 0}</strong><span>{{tasks:'任务',notes:'笔记',resources:'KB 文档',projects:'项目',conversations:'会话',memories:'记忆'}[key]}</span></div>)}</div><div className="modal-callout"><HardDriveDownload size={18} />确认后只追加新记录，不删除或覆盖当前学习数据。真实 KB 原件需要随备份内联，当前仅导入元数据。</div><div className="confirm-actions"><Button variant="secondary" onClick={() => setPreview(null)}>取消</Button><Button onClick={() => { if (preview) onImport(preview); setPreview(null); notify('备份已按预演结果追加导入') }}>确认追加导入</Button></div></div></Modal></>
}

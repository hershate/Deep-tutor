import { useCallback, useEffect, useState } from 'react'
import { Folder, FolderOpen, RefreshCw, RotateCcw } from 'lucide-react'
import { Badge, Button, ConfirmDialog, SectionHeader } from './UI'
import { backendErrorMessage, reportBackendError } from '../backend/client'
import { storageBackend } from '../backend/storage'
import { revealInFileManager } from '../backend/shell'
import type { StorageOverview } from '../backend/storage'

export default function DataDirectoryPanel() {
  const [overview, setOverview] = useState<StorageOverview | null>(null)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState('')
  // 42#4：一键即重启加确认层（数据已落盘，但整窗上下文消失与其余全局操作
  // 的确认模型不一致）
  const [restartOpen, setRestartOpen] = useState(false)

  const apply = useCallback((next: StorageOverview) => {
    setOverview(next)
    setDraft(next.pendingDataRoot ?? next.dataRoot)
    setError('')
  }, [])
  const refresh = useCallback(async () => {
    try { apply(await storageBackend.overview()) }
    catch (cause) { setError(backendErrorMessage(cause)) }
  }, [apply])
  useEffect(() => { void refresh() }, [refresh])

  const run = async (action: string, operation: () => Promise<StorageOverview>) => {
    if (busy) return
    setBusy(action)
    setError('')
    setMessage('')
    try { apply(await operation()) }
    catch (cause) { setError(backendErrorMessage(cause)); reportBackendError(cause, 'write') }
    finally { setBusy('') }
  }
  const choose = async () => {
    if (busy) return
    setBusy('choose')
    setError('')
    try {
      const selected = await storageBackend.chooseDirectory(overview?.pendingDataRoot ?? overview?.dataRoot)
      if (selected) { setDraft(selected); setMessage('目录已选择，点击「保存目录」后才会写入配置。') }
    } catch (cause) { setError(backendErrorMessage(cause)) }
    finally { setBusy('') }
  }
  const save = () => run('save', async () => {
    const next = await storageBackend.save(draft.trim())
    setMessage('新目录已保存，重启后使用；原目录中的学习数据未移动或删除。')
    return next
  })
  const reset = () => run('reset', async () => {
    const next = await storageBackend.reset()
    setMessage('已恢复默认目录设置，原目录中的学习数据仍然保留。')
    return next
  })
  const retry = () => run('retry', async () => {
    const next = await storageBackend.retry()
    if (next.error) { throw next.error }
    if (next.ready) {
      if (next.pendingDataRoot) setMessage('初始化已就绪；新目录将在应用重启后生效，可点击「保存并立即重启」。')
      // A-6：ready 且无待切换目录时后端不做任何动作——此前整页 reload
      // 会白屏数秒并丢失全部本地状态，与「重试」的预期不符
      else setMessage('初始化状态正常，无需处理。')
    } else {
      setMessage('初始化仍在进行；已保存的新目录会在下次启动时生效，请稍后重试或重启应用。')
    }
    return next
  })
  const restart = async () => {
    if (busy) return
    setBusy('restart')
    setError('')
    try { await storageBackend.restart() }
    catch (cause) { setError(backendErrorMessage(cause)); setBusy('') }
  }
  // 37#4：数据目录只读展示的配套能力——一键在资源管理器中定位
  const reveal = async () => {
    if (!overview) return
    try { await revealInFileManager(overview.dataRoot) }
    // 62#8：opener 失败抛非 BackendError 的原生串（英文/系统级）——给出
    // 自救指引而非原样上屏；目录尚未创建时按钮本身也已禁用
    catch { setError('无法在资源管理器中打开：目录可能尚未创建或被其他程序占用。请先点击下方「重试初始化」创建目录后再试。') }
  }

  if (!overview) return <section className="surface setting-section storage-location-panel"><SectionHeader title="本地学习数据目录" description="目录设置由原生后端保存。" />{error ? <div className="form-error" role="alert"><span>{error}</span><Button size="sm" onClick={() => void refresh()}>重新读取</Button></div> : <p>正在读取目录设置…</p>}</section>
  const unchanged = draft.trim() === overview.dataRoot && overview.pendingDataRoot === null
  const pending = overview.pendingDataRoot
  return <section className="surface setting-section storage-location-panel">
    <SectionHeader title="本地学习数据目录" description="默认目录自动创建。你可以修改保存位置，新设置在下次启动时生效。" action={<Badge tone={pending ? 'amber' : overview.initializing ? 'neutral' : overview.ready ? 'green' : 'red'}>{pending ? '重启后生效' : overview.initializing ? '正在初始化' : overview.ready ? '当前使用' : '需要处理'}</Badge>} />
    <div className="storage-current"><Folder size={18} /><div><strong>{pending ? '当前目录' : '学习数据保存位置'}</strong><code>{overview.dataRoot}</code><small>默认目录：{overview.defaultDataRoot}</small><div>{/* 62#8：目录未就绪时可能尚未创建——直点必败，禁用并提示先初始化 */}<Button variant="ghost" size="sm" disabled={!overview.ready} title={overview.ready ? undefined : '数据目录尚未创建，请先重试初始化'} onClick={() => void reveal()}><FolderOpen size={15} />在资源管理器中显示</Button></div></div></div>
    {pending && <div className="modal-callout"><RefreshCw size={17} /><div><strong>下次启动使用：{pending}</strong><p>当前进程仍使用原目录。切换不会自动迁移或删除旧数据；需要原有内容时，请先通过备份功能导出，再在新目录中导入。重启会结束当前进程，请先完成或取消正在运行的任务。</p></div></div>}
    {overview.error && <div className="form-error" role="alert">{overview.error.code}：{overview.error.message}</div>}
    <label>新的保存位置<input aria-label="本地学习数据目录" value={draft} disabled={Boolean(busy)} onChange={event => { setDraft(event.target.value); setMessage('') }} placeholder="输入磁盘上的绝对目录" /></label>
    <div className="data-actions"><Button variant="secondary" disabled={Boolean(busy)} onClick={() => void choose()}><Folder size={16} />选择目录</Button><Button disabled={Boolean(busy) || !draft.trim() || (unchanged && overview.ready)} onClick={() => void save()}>{busy === 'save' ? '正在验证并保存…' : '保存目录'}</Button><Button variant="ghost" disabled={Boolean(busy)} onClick={() => void reset()}><RotateCcw size={16} />恢复默认目录</Button></div>
    <p className="form-help">保存时会检查目录和数据库。已有数据库会被打开；空目录会创建新数据库。不会覆盖或删除原目录中的学习数据。</p>
    {message && <p className="setting-note" role="status">{message}</p>}
    {error && <div className="form-error" role="alert">{error}</div>}
    <div className="data-actions"><Button variant="secondary" disabled={Boolean(busy)} onClick={() => void retry()}><RefreshCw size={16} />{busy === 'retry' ? '正在初始化…' : '重试初始化'}</Button>{pending && <Button disabled={Boolean(busy)} onClick={() => setRestartOpen(true)}>保存并立即重启</Button>}</div>
    <ConfirmDialog open={restartOpen} onClose={() => setRestartOpen(false)} onConfirm={() => { setRestartOpen(false); void restart() }} title="现在重启应用？" description="重启后新数据目录生效。打开的弹窗、滚动位置等界面状态会丢失（学习数据均已落盘，不会丢内容）。" confirmLabel="确认重启" />
  </section>
}

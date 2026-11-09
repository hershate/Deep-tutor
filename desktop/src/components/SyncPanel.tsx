import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, CloudCog, Download, Eye, LoaderCircle, RefreshCw, ShieldCheck, Square, Upload } from 'lucide-react'
import { Badge, Button, Modal, SectionHeader, Toggle } from './UI'
import { backend, backendErrorMessage, onBackendEvent, isCancelRace } from '../backend/client'
import { syncBackend } from '../backend/sync'
import type { SyncConfigInput, SyncJob, SyncOverview, SyncVersion } from '../backend/sync'

const statusNames: Record<string, string> = { running: '进行中', cancelling: '取消中', done: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' }
const jobNames: Record<string, string> = { check: '连接检测', upload: '上传配置', download: '下载配置', import: '下载并导入', restore: '恢复历史版本' }

// 67#4：远端时间串（WebDAV RFC1123 / S3 ISO8601 原样透传）解析失败时
// 原样展示服务器返回——不再显示 Invalid Date
function formatRemoteTime(raw: string) {
  const parsed = new Date(raw)
  return Number.isNaN(parsed.getTime()) ? raw : parsed.toLocaleString()
}

export default function SyncPanel({ notify }: { notify: (message: string) => void }) {
  const [overview, setOverview] = useState<SyncOverview | null>(null)
  const [form, setForm] = useState<SyncConfigInput>({ kind: 'webdav', endpoint: '', remotePath: 'deep-tutor/provider-config.json', region: '', bucket: '', authKind: 'none', username: '', allowPrivate: false, autoSync: false, password: null, accessKey: null, secretKey: null })
  const [password, setPassword] = useState('')
  const [accessKey, setAccessKey] = useState('')
  const [secretKey, setSecretKey] = useState('')
  const [clearCredentials, setClearCredentials] = useState(false)
  const [configOpen, setConfigOpen] = useState(false)
  const [versionsOpen, setVersionsOpen] = useState(false)
  const [versions, setVersions] = useState<SyncVersion[] | null>(null)
  // 43#7：远端列举失败单独下沉到弹窗内渲染——面板级 error 会被模态完全遮挡，
  // 原先弹窗里只有无限 spinner，无错误也无重试入口
  const [versionsError, setVersionsError] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const versionsOpenRef = useRef(false)

  const refresh = useCallback(async () => {
    try { setOverview(await syncBackend.overview()) } catch (cause) { setError(backendErrorMessage(cause)) }
  }, [])
  const loadVersions = useCallback(async () => {
    try { setVersions(await syncBackend.versions()); setVersionsError('') } catch (cause) { setVersionsError(backendErrorMessage(cause)) }
  }, [])
  useEffect(() => {
    void refresh()
    let mounted = true
    let unlisten: (() => void) | undefined
    const listening = onBackendEvent(event => {
      if (event.kind.startsWith('sync.')) void refresh()
      // 版本列表只在弹窗打开时跟随刷新；关闭状态下不额外发起远端列举请求
      if (event.kind === 'sync.completed' && versionsOpenRef.current) void loadVersions()
    })
    listening.then(remove => { if (mounted) unlisten = remove; else remove() }).catch(cause => setError(backendErrorMessage(cause)))
    // 28#5：卸载早于 listen() promise resolve 时立即注销
    return () => { mounted = false; unlisten?.(); void listening.then(remove => { if (!mounted) remove() }) }
  }, [refresh, loadVersions])
  // configOpen 声明在组件头部（28#10）：这里仅追加 ref 镜像供事件刷新判定
  const configOpenRef = useRef(false)
  useEffect(() => { configOpenRef.current = configOpen }, [configOpen])
  useEffect(() => {
    if (!overview?.config) return
    const config = overview.config.config
    // 28#10：编辑弹窗打开时不以外来事件覆盖正在输入的表单——仅在关闭
    // 状态或字段未被用户改动时跟随最新配置
    setForm(current => configOpenRef.current
      ? current
      : { ...current, ...config, password: null, accessKey: null, secretKey: null })
  }, [overview?.config])

  const update = <K extends keyof SyncConfigInput>(key: K, value: SyncConfigInput[K]) => setForm(current => ({ ...current, [key]: value }))
  const save = async (test: boolean) => {
    setBusy(test ? 'check' : 'save')
    setError('')
    try {
      const { updatedAt: _, ...config } = form as SyncConfigInput & { updatedAt?: string }
      void _
      const input = { ...config, password: clearCredentials ? '' : form.kind === 's3' ? null : password || null, accessKey: clearCredentials ? '' : form.kind === 's3' ? accessKey || null : null, secretKey: clearCredentials ? '' : form.kind === 's3' ? secretKey || null : null }
      await syncBackend.saveConfig(input)
      setPassword(''); setAccessKey(''); setSecretKey(''); setClearCredentials(false)
      await refresh()
      if (test) {
        const accepted = await syncBackend.start('check')
        notify(`连接检测任务 ${accepted.id} 已启动，结果将由原生任务返回`)
      } else {
        setConfigOpen(false)
        notify('同步配置已保存；远端密钥仅保存在系统密钥库')
      }
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const start = async (kind: SyncJob['kind']) => {
    setBusy(kind)
    setError('')
    try {
      const destination = kind === 'download' ? await backend.saveMaterialFile('DeepTutor-provider-config', 'json') : null
      if (kind === 'download' && !destination) return
      const accepted = await syncBackend.start(kind, destination)
      notify(`${jobNames[kind]}任务 ${accepted.id} 已启动，等待真实远端结果`)
      await refresh()
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const cancel = async (id: string) => {
    setBusy(`cancel-${id}`)
    try { await syncBackend.cancel(id); await refresh() } catch (cause) { if (!isCancelRace(cause)) setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const openVersions = async () => {
    setBusy('versions')
    await loadVersions()
    versionsOpenRef.current = true
    setVersionsOpen(true)
    setBusy('')
  }
  const restoreVersion = async (path: string) => {
    setBusy('restore')
    setError('')
    try {
      const accepted = await syncBackend.restore(path)
      notify(`历史版本恢复任务 ${accepted.id} 已启动，将按真实配置追加导入`)
      await refresh()
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const configured = Boolean(overview?.config)
  const checkIcon = busy === 'check' ? <LoaderCircle size={16} className="spin" /> : <Eye size={16} />
  const saveIcon = busy === 'save' ? <LoaderCircle size={16} className="spin" /> : <CheckCircle2 size={16} />
  return <section className="surface cloud-sync">
    <SectionHeader title="配置同步" description="只同步脱敏供应商档案与任务路由；不上传学习数据或密钥" action={<Badge tone={configured ? 'green' : 'neutral'}>{configured ? '原生同步' : '未配置'}</Badge>} />
    <p>WebDAV / S3 由 Rust 实际请求并回读校验。API Key、学习资料、笔记和会话不进入同步载荷。</p>
    <div className="data-actions">
      <Button variant="secondary" size="sm" onClick={() => setConfigOpen(true)}><CloudCog size={15} />配置与检测</Button>
      <Button variant="secondary" size="sm" disabled={!configured || Boolean(busy)} onClick={() => void start('upload')}><Upload size={15} />上传脱敏配置</Button>
      <Button variant="ghost" size="sm" disabled={!configured || Boolean(busy)} onClick={() => void start('download')}><Download size={15} />下载配置</Button>
      <Button variant="ghost" size="sm" disabled={!configured || Boolean(busy)} onClick={() => void start('import')}><RefreshCw size={15} />下载并追加导入</Button>
    </div>
    <div className="data-actions"><Button variant="ghost" size="sm" disabled={!configured || busy === 'versions'} onClick={() => void openVersions()}><RefreshCw size={15} />{busy === 'versions' ? '正在读取远端版本…' : '浏览远端历史版本'}</Button></div>
    <p className="form-help">自动同步开启后每 24 小时检查到期任务；上传同时保存历史版本，最多保留最近 10 个。</p>
    {error && <div className="form-error" role="alert">{error}</div>}
    <div className="sync-jobs">
      {overview?.jobs.map(job => <div className="sync-job" key={job.id}>
        <div>
          <strong>{jobNames[job.kind]} <Badge tone={job.status === 'done' ? 'green' : job.status === 'failed' ? 'red' : 'amber'}>{statusNames[job.status]}</Badge></strong>
          <p>{job.remotePath}</p>
          <small>{new Date(job.updatedAt).toLocaleString()} · {job.id}</small>
          {job.result?.sha256 && <small>SHA-256 {job.result.sha256}</small>}
          {job.error && <p className="form-error">{job.error.code}：{job.error.message}</p>}
        </div>
        {['running', 'cancelling'].includes(job.status) && <Button variant="ghost" size="sm" onClick={() => void cancel(job.id)} disabled={job.status === 'cancelling' || Boolean(busy)}><Square size={14} />取消</Button>}
      </div>)}
      {overview && !overview.jobs.length && <p className="form-help">尚无同步任务。先保存服务配置，再进行真实连接检测。</p>}
    </div>
    <Modal open={configOpen} onClose={() => setConfigOpen(false)} title="配置云同步" wide>
      <div className="form-stack">
        <div className="modal-callout"><ShieldCheck size={18} /><span>只同步脱敏档案与路由。凭据经一次性表单进入系统密钥库，不写入数据库、远端文件或任务日志。</span></div>
        <div className="form-grid">
          <label>同步方式<select value={form.kind} onChange={event => { const kind = event.target.value as SyncConfigInput['kind']; setForm(current => ({ ...current, kind, authKind: kind === 's3' ? 'aws4' : 'none', region: kind === 's3' ? current.region : '', bucket: kind === 's3' ? current.bucket : '' })) }}><option value="webdav">WebDAV</option><option value="s3">S3 兼容存储</option></select></label>
          <label>远端对象路径<input value={form.remotePath} onChange={event => update('remotePath', event.target.value)} /></label>
        </div>
        <label>服务地址<input value={form.endpoint} onChange={event => update('endpoint', event.target.value)} placeholder={form.kind === 's3' ? 'https://s3.example.com' : 'https://dav.example.com/DeepTutor'} /></label>
        {form.kind === 's3' && <div className="form-grid">
          <label>区域<input value={form.region} onChange={event => update('region', event.target.value)} placeholder="us-east-1" /></label>
          <label>Bucket<input value={form.bucket} onChange={event => update('bucket', event.target.value)} /></label>
        </div>}
        {form.kind === 'webdav' && <label>认证方式<select value={form.authKind} onChange={event => update('authKind', event.target.value as SyncConfigInput['authKind'])}><option value="none">无认证</option><option value="basic">Basic 账号密码</option><option value="bearer">Bearer 设备凭据</option></select></label>}
        {form.kind === 'webdav' && form.authKind === 'basic' && <label>账号<input autoComplete="off" value={form.username} onChange={event => update('username', event.target.value)} /></label>}
        {form.kind === 'webdav' && form.authKind !== 'none' && <label>{form.authKind === 'basic' ? '密码' : '设备专用 Bearer 凭据'}<input type="password" autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)} placeholder="留空则保留已有凭据" /></label>}
        {form.kind === 's3' && <div className="form-grid">
          <label>Access Key<input type="password" autoComplete="new-password" value={accessKey} onChange={event => setAccessKey(event.target.value)} placeholder="留空则保留已有凭据" /></label>
          <label>Secret Key<input type="password" autoComplete="new-password" value={secretKey} onChange={event => setSecretKey(event.target.value)} placeholder="留空则保留已有凭据" /></label>
        </div>}
        <Toggle checked={form.allowPrivate} onChange={value => update('allowPrivate', value)} label="允许本机或私网服务" description="仅用于明确的本地 MinIO / WebDAV。未开启时公网服务必须使用 HTTPS，禁止访问私网地址。" />
        <Toggle checked={form.autoSync} onChange={value => update('autoSync', value)} label="每 24 小时自动同步" description="保存后由原生调度器检查到期任务；每次上传保留历史版本，最近 10 个可恢复。" />
        <Toggle checked={clearCredentials} onChange={setClearCredentials} label="清除此服务的已保存凭据" description="保存后删除系统密钥库中对应的同步凭据，不影响模型供应商密钥。" />
        {error && <div className="form-error" role="alert">{error}</div>}
        <div className="share-actions">
          <Button variant="secondary" disabled={Boolean(busy)} onClick={() => void save(true)}>{checkIcon}测试连接并保存</Button>
          <Button disabled={Boolean(busy)} onClick={() => void save(false)}>{saveIcon}保存配置</Button>
        </div>
      </div>
    </Modal>
    <Modal open={versionsOpen} onClose={() => { versionsOpenRef.current = false; setVersionsOpen(false) }} title="远端历史版本" wide>
      <div className="form-stack">
        <div className="modal-callout"><ShieldCheck size={18} /><span>版本来自远端实际列表。恢复会读取所选对象并追加导入档案与路由，不覆盖本机系统凭据。</span></div>
        <div className="data-actions"><Button variant="secondary" size="sm" onClick={() => void loadVersions()}><RefreshCw size={15} />重新读取远端列表</Button></div>
        {versionsError ? <div className="form-error" role="alert"><p>远端版本列表读取失败：{versionsError}</p><Button size="sm" onClick={() => void loadVersions()}>重试读取</Button></div> : versions === null ? <LoaderCircle size={22} className="spin" /> : versions.length ? versions.map(version => <div className="sync-version" key={version.path}><div><strong>{version.path.split('/').at(-1)}</strong><p>{version.size === null ? '大小未提供' : `${version.size.toLocaleString()} 字节`} · {version.lastModified ? formatRemoteTime(version.lastModified) : '时间未提供'}</p><code>{version.path}</code></div><Button variant="secondary" size="sm" disabled={busy === 'restore'} onClick={() => void restoreVersion(version.path)}>恢复并追加</Button></div>) : <p>远端尚无历史版本。完成一次实际上传后可在这里恢复。</p>}
      </div>
    </Modal>
  </section>
}

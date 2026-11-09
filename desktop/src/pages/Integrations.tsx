import { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, AlertTriangle, ArrowDownToLine, Blocks, Code2, FileCode2, Layers3, Plug, RefreshCw, ShieldCheck, Trash2, Upload, Zap } from 'lucide-react'
import { Badge, Button, ConfirmDialog, EmptyState, IconButton, SectionHeader, Toggle } from '../components/UI'
import { backend, backendErrorMessage, onBackendEvent, reportBackendError } from '../backend/client'
import { pluginBackend, type PluginEvent, type PluginOverview } from '../backend/plugins'
import UsagePanel from '../components/UsagePanel'

// 61#1：与后端白名单同步——app 无对应原生命名空间事件，订阅恒空投递
const namespaces = ['plan', 'practice', 'review', 'material', 'agent', 'knowledge', 'community', 'provider', 'plugin', 'share', 'content', 'backup', 'sync', 'maintenance']

export default function Integrations({ notify }: { notify: (message: string) => void }) {
  const [tab, setTab] = useState('插件体系')
  const [data, setData] = useState<PluginOverview | null>(null)
  const [busy, setBusy] = useState(false)
  // 62#1：初载失败原先整块空白只弹 5 秒 toast——补错误横幅+重试，
  // 对齐 Practice/Review 等同类页三态
  const [loadError, setLoadError] = useState('')
  const refresh = useCallback(async () => {
    try {
      setData(await pluginBackend.overview())
      setLoadError('')
    } catch (cause) { setLoadError(backendErrorMessage(cause)) }
  }, [])
  // 14#18：事件记录游标分页——overview 只带最新 100 条，更早历史按需加载
  const [earlier, setEarlier] = useState<PluginEvent[]>([])
  const [earlierDone, setEarlierDone] = useState(false)
  const [loadingEarlier, setLoadingEarlier] = useState(false)
  const events = useMemo(() => {
    const seen = new Set((data?.events ?? []).map(event => `${event.pluginId}-${event.eventId}`))
    // B-19：earlier 快照可能含已卸载插件（plugin_deliveries 已 CASCADE 清空），
    // 对照当前插件集过滤，卸载后历史行不再残留
    const installed = new Set((data?.plugins ?? []).map(plugin => plugin.id))
    const merged = [...(data?.events ?? []), ...earlier.filter(event => installed.has(event.pluginId) && !seen.has(`${event.pluginId}-${event.eventId}`))]
    return merged.sort((a, b) => b.eventId - a.eventId)
  }, [data, earlier])
  const loadEarlier = async () => {
    if (loadingEarlier || !events.length) return
    setLoadingEarlier(true)
    try {
      const older = await pluginBackend.events(undefined, events[events.length - 1].eventId)
      setEarlier(current => [...current, ...older])
      if (older.length < 100) setEarlierDone(true)
    } catch (error) { reportBackendError(error) } finally { setLoadingEarlier(false) }
  }
  useEffect(() => {
    void refresh()
    let unlisten: (() => void) | undefined
    // 32#5：晚解析守卫——卸载早于 listen() resolve 时立即注销
    let disposed = false
    onBackendEvent(event => {
      // 32#22：过滤收窄——原先 `.completed` 等后缀命中 provider.chat_completed，
      // 任意模型请求完成都触发插件 overview 全量刷新
      if (event.kind.startsWith('plugin.') || event.kind.startsWith('material.') || event.kind.startsWith('practice.') || event.kind.startsWith('knowledge.')) void refresh()
    }).then(stop => { if (disposed) stop(); else unlisten = stop }).catch(reportBackendError)
    return () => { disposed = true; unlisten?.() }
  }, [refresh])

  const install = async (file: File | undefined) => {
    if (!file || busy) return
    setBusy(true)
    try {
      const plugin = await pluginBackend.install(await file.text())
      await refresh()
      notify(`插件「${plugin.name}」已安装，默认停用；审查清单后可启用`)
    } catch (error) { reportBackendError(error, 'write') } finally { setBusy(false) }
  }
  const toggle = async (id: string, enabled: boolean) => {
    if (busy) return
    setBusy(true)
    try { await pluginBackend.setEnabled(id, enabled); await refresh(); notify(enabled ? '插件已启用，事件订阅开始生效' : '插件已停用，历史投递仍保留') }
    catch (error) { reportBackendError(error, 'write') } finally { setBusy(false) }
  }
  const reload = async (id: string) => {
    if (busy) return
    setBusy(true)
    try { const plugin = await pluginBackend.reload(id); await refresh(); notify(`已重载「${plugin.name}」，当前版本 ${plugin.version}，第 ${plugin.generation} 代配置`) }
    catch (error) { reportBackendError(error, 'write') } finally { setBusy(false) }
  }
  // 20#5：卸载级联删除投递历史，不可恢复——补确认对话框
  const [uninstallId, setUninstallId] = useState<string | null>(null)
  const uninstall = async (id: string) => {
    if (busy) return
    setBusy(true)
    try { await pluginBackend.uninstall(id); await refresh(); setUninstallId(null); notify('插件记录与安装清单已删除') }
    catch (error) { reportBackendError(error, 'write') } finally { setBusy(false) }
  }
  const ping = async () => {
    try { const id = await pluginBackend.ping(); await refresh(); notify(`已发送真实原生事件 plugin.ping，事件 ID ${id}`) }
    catch (error) { reportBackendError(error, 'write') }
  }
  // 43#2：资产下载写盘期间禁用，防止连点叠出多个保存对话框
  const [downloading, setDownloading] = useState('')
  const download = async (id: string) => {
    if (downloading) return
    setDownloading(id)
    try {
      // 37#5：与其他导出一致——原生保存对话框选位置，由后端落盘
      const destination = await backend.saveMaterialFile(id, 'md')
      if (!destination) return
      const result = await pluginBackend.saveAsset(id, destination)
      notify(`已下载真实资产（${result.bytes.toLocaleString()} 字节）`)
    } catch (error) { reportBackendError(error) } finally { setDownloading('') }
  }

  // 62#1：data 未就绪时的两态呈现——失败给横幅+重试，在途给加载提示
  const loadPending = loadError
    ? <div className="form-error" role="alert"><AlertTriangle size={16} />{loadError}<div><Button variant="secondary" size="sm" onClick={() => void refresh()}>重试</Button></div></div>
    : <p className="form-help">正在读取插件与资产状态…</p>

  return <div className="page integrations-page">
    <div className="page-heading"><div><h1>插件与开发者</h1><p>声明式扩展、真实事件和可审查资产；不执行不受信任的脚本。</p></div><Badge tone="green"><ShieldCheck size={14} />原生 SQLite · 密钥入系统密钥库</Badge></div>
    <div className="integration-tabs">{['插件体系', '用量与成本', '开发者资源'].map(item => <button key={item} className={tab === item ? 'active' : ''} onClick={() => setTab(item)}>{item === '插件体系' ? <Blocks size={17} /> : item === '开发者资源' ? <Code2 size={17} /> : <Activity size={17} />}{item}</button>)}</div>

    {tab === '插件体系' && (data ? <><section className="plugin-summary"><div><Plug size={23} /><span>已启用插件</span><strong>{data.enabledCount}<small> / {data.plugins.length}</small></strong></div><div><Layers3 size={23} /><span>有效事件订阅</span><strong>{data.plugins.reduce((sum, plugin) => sum + plugin.subscriptions.length, 0)}</strong></div><div><Activity size={23} /><span>真实事件投递</span><strong>{data.eventCount}</strong></div><div className="plugin-upload"><input id="plugin-file" type="file" accept=".json,application/json" className="visually-hidden" disabled={busy} onChange={event => { void install(event.target.files?.[0]); event.target.value = '' }} /><label htmlFor="plugin-file" className="btn btn-secondary btn-md"><Upload size={16} />安装 JSON 事件插件</label></div></section><div className="integration-main-grid"><main className="plugin-grid">{data.plugins.map(plugin => <article className="plugin-card" key={plugin.id}><div className="plugin-card-head"><span><Blocks size={19} /></span><Badge tone={plugin.enabled ? 'green' : 'neutral'}>{plugin.enabled ? '已启用' : '已停用'}</Badge></div><h2>{plugin.name}</h2><p>{plugin.description}</p><small>声明式事件插件 · v{plugin.version} · 第 {plugin.generation} 代配置</small><div className="subscription-list">{plugin.subscriptions.map(pattern => <code key={pattern}>{pattern}</code>)}</div><small>{plugin.eventCount} 条真实投递</small><div>{/* 48#14：可访问名固定为用途——原先 label 随状态翻转（已启用/已停用），读屏播报把名称与 aria-checked 状态混在一起 */}
<Toggle checked={plugin.enabled} onChange={value => void toggle(plugin.id, value)} disabled={busy} label="启用此插件" /><IconButton label={`重载${plugin.name}`} onClick={() => void reload(plugin.id)} disabled={busy}><RefreshCw size={16} /></IconButton><IconButton label={`卸载${plugin.name}`} onClick={() => setUninstallId(plugin.id)} disabled={busy}><Trash2 size={16} /></IconButton></div></article>)}{!data.plugins.length && <EmptyState title="尚未安装声明式插件" description="选择包含 id、name、version、description、kind=events 与 subscriptions 的 JSON 清单。插件默认停用，不执行 Python 或任意脚本。" />}</main><aside className="surface host-panel"><SectionHeader title="原生事件订阅" description="只允许下列真实命名空间；每次投递按插件 ID 与事件 ID 去重。" /><div className="namespace-list">{namespaces.map(item => <span key={item}>{item}.*</span>)}</div><h3>真实事件记录</h3><div className="event-log">{events.map(event => <code key={`${event.pluginId}-${event.eventId}`}>{event.pluginName} · {event.kind} · #{event.eventId}</code>)}{!events.length && <span>尚无插件事件投递。</span>}</div>{events.length >= 100 && !earlierDone && <Button variant="ghost" size="sm" disabled={loadingEarlier} onClick={() => void loadEarlier()}>{loadingEarlier ? '正在加载更早事件…' : '加载更早事件'}</Button>}<Button variant="secondary" size="sm" onClick={() => void ping()}><Zap size={15} />发送原生 ping 事件</Button><p className="form-help">ping 由后端实际写入 outbox，不伪造方案完成或测验就绪事件。</p></aside></div></> : loadPending)}

    <ConfirmDialog open={uninstallId !== null} onClose={() => setUninstallId(null)} onConfirm={() => { const id = uninstallId; if (id !== null) void uninstall(id) }} title="卸载这个插件？" description="将删除插件记录、安装清单与全部投递历史；此操作不可恢复。" confirmLabel="确认卸载" busy={busy} />

    {tab === '用量与成本' && <UsagePanel notify={notify} />}

    {tab === '开发者资源' && (data ? <div className="developer-layout"><main><section className="surface skill-assets"><SectionHeader title="可审查的 Skill 与原生运维资产" description="历史 Skill 仅作方法论参考，不是当前原生运行时；内容按原始文件读取并校验 SHA-256。" />{data.assets.map(asset => <div key={asset.id}><FileCode2 size={18} /><div><strong>{asset.title}</strong><span>{asset.kind === 'legacy-skill' ? '历史方法论资产，不直接执行' : 'Tauri 2 原生应用运维'} · {asset.bytes} 字节</span></div><Button variant="ghost" size="sm" disabled={downloading !== ''} onClick={() => void download(asset.id)}>{downloading === asset.id ? '正在下载…' : <><ArrowDownToLine size={15} />下载</>}</Button></div>)}</section><section className="surface security-matrix"><SectionHeader title="当前原生扩展边界" description="仅展示实际实现，不宣称未提供的认证或在线安全能力。" />{[['清单校验', '仅接受声明式 JSON 事件插件；拒绝未知字段与脚本入口'], ['路径边界', '插件 ID 经过校验，安装文件限制在应用数据目录'], ['事件去重', '真实事件先写 outbox；插件与事件 ID 联合去重'], ['重载保护', '无效清单不改变当前启用配置，并记录失败审计'], ['密钥边界', '插件清单不接受 API Key；密钥由系统密钥库管理'], ['生命周期', '启停、重载、卸载和重置均有真实持久化结果']].map(([name, description]) => <div key={name}><ShieldCheck size={17} /><strong>{name}</strong><span>{description}</span><Badge tone="neutral">原生实现</Badge></div>)}</section></main><aside><section className="surface startup-card"><Code2 size={23} /><h3>Tauri 2 原生运维</h3><div><span>交付方式</span><strong>NSIS 应用安装包</strong></div><div><span>持久化</span><strong>SQLite WAL · 原子事务 · 事件 outbox</strong></div><div><span>插件边界</span><strong>声明式清单 · 无脚本执行</strong></div><Button variant="secondary" disabled={downloading !== ''} onClick={() => void download('native-operations')}>{downloading === 'native-operations' ? '正在下载…' : '下载原生运维说明'}</Button></section></aside></div> : loadPending)}
  </div>
}

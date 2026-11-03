import { useCallback, useEffect, useState } from 'react'
import { Activity, ArrowDownToLine, Blocks, Code2, FileCode2, Layers3, Plug, RefreshCw, ShieldCheck, Trash2, Upload, Zap } from 'lucide-react'
import { Badge, Button, EmptyState, IconButton, SectionHeader, Toggle } from '../components/UI'
import { onBackendEvent, reportBackendError } from '../backend/client'
import { pluginBackend, type PluginOverview } from '../backend/plugins'

const namespaces = ['plan', 'practice', 'review', 'material', 'agent', 'knowledge', 'community', 'provider', 'plugin', 'app']

export default function Integrations({ notify }: { notify: (message: string) => void }) {
  const [tab, setTab] = useState('插件体系')
  const [data, setData] = useState<PluginOverview | null>(null)
  const [busy, setBusy] = useState(false)
  const refresh = useCallback(async () => setData(await pluginBackend.overview()), [])
  useEffect(() => {
    void refresh().catch(reportBackendError)
    let unlisten: (() => void) | undefined
    const listening = onBackendEvent(event => {
      if (event.kind.startsWith('plugin.') || ['completed', 'created', 'graded', 'parsed', 'failed', 'cancelled'].some(suffix => event.kind.endsWith(`.${suffix}`))) void refresh().catch(reportBackendError)
    }).then(stop => { unlisten = stop }).catch(reportBackendError)
    return () => { unlisten?.(); void listening }
  }, [refresh])

  const install = async (file: File | undefined) => {
    if (!file || busy) return
    setBusy(true)
    try {
      const plugin = await pluginBackend.install(await file.text())
      await refresh()
      notify(`插件「${plugin.name}」已安装，默认停用；审查清单后可启用`)
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const toggle = async (id: string, enabled: boolean) => {
    if (busy) return
    setBusy(true)
    try { await pluginBackend.setEnabled(id, enabled); await refresh(); notify(enabled ? '插件已启用，事件订阅开始生效' : '插件已停用，历史投递仍保留') }
    catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const reload = async (id: string) => {
    if (busy) return
    setBusy(true)
    try { const plugin = await pluginBackend.reload(id); await refresh(); notify(`已重载「${plugin.name}」，当前版本 ${plugin.version}，第 ${plugin.generation} 代配置`) }
    catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const uninstall = async (id: string) => {
    if (busy) return
    setBusy(true)
    try { await pluginBackend.uninstall(id); await refresh(); notify('插件记录与安装清单已删除') }
    catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const ping = async () => {
    try { const id = await pluginBackend.ping(); await refresh(); notify(`已发送真实原生事件 plugin.ping，事件 ID ${id}`) }
    catch (error) { reportBackendError(error) }
  }
  const download = async (id: string) => {
    try {
      const asset = await pluginBackend.asset(id)
      const url = URL.createObjectURL(new Blob([asset.content], { type: 'text/markdown;charset=utf-8' }))
      const anchor = document.createElement('a')
      anchor.href = url; anchor.download = asset.fileName; document.body.appendChild(anchor); anchor.click(); anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      notify(`已下载真实资产 ${asset.fileName}`)
    } catch (error) { reportBackendError(error) }
  }

  return <div className="page integrations-page">
    <div className="page-heading"><div><h1>插件与开发者</h1><p>声明式扩展、真实事件和可审查资产；不执行不受信任的脚本。</p></div><Badge tone="green"><ShieldCheck size={14} />原生 SQLite · 密钥入系统密钥库</Badge></div>
    <div className="integration-tabs">{['插件体系', '用量与成本', '开发者资源'].map(item => <button key={item} className={tab === item ? 'active' : ''} onClick={() => setTab(item)}>{item === '插件体系' ? <Blocks size={17} /> : item === '开发者资源' ? <Code2 size={17} /> : <Activity size={17} />}{item}</button>)}</div>

    {tab === '插件体系' && data && <><section className="plugin-summary"><div><Plug size={23} /><span>已启用插件</span><strong>{data.enabledCount}<small> / {data.plugins.length}</small></strong></div><div><Layers3 size={23} /><span>有效事件订阅</span><strong>{data.plugins.reduce((sum, plugin) => sum + plugin.subscriptions.length, 0)}</strong></div><div><Activity size={23} /><span>真实事件投递</span><strong>{data.eventCount}</strong></div><div className="plugin-upload"><input id="plugin-file" type="file" accept=".json,application/json" className="visually-hidden" disabled={busy} onChange={event => { void install(event.target.files?.[0]); event.target.value = '' }} /><label htmlFor="plugin-file" className="btn btn-secondary btn-md"><Upload size={16} />安装 JSON 事件插件</label></div></section><div className="integration-main-grid"><main className="plugin-grid">{data.plugins.map(plugin => <article className="plugin-card" key={plugin.id}><div className="plugin-card-head"><span><Blocks size={19} /></span><Badge tone={plugin.enabled ? 'green' : 'neutral'}>{plugin.enabled ? '已启用' : '已停用'}</Badge></div><h2>{plugin.name}</h2><p>{plugin.description}</p><small>声明式事件插件 · v{plugin.version} · 第 {plugin.generation} 代配置</small><div className="subscription-list">{plugin.subscriptions.map(pattern => <code key={pattern}>{pattern}</code>)}</div><small>{plugin.eventCount} 条真实投递</small><div><Toggle checked={plugin.enabled} onChange={value => void toggle(plugin.id, value)} label={plugin.enabled ? '已启用' : '已停用'} /><IconButton label={`重载${plugin.name}`} onClick={() => void reload(plugin.id)} disabled={busy}><RefreshCw size={16} /></IconButton><IconButton label={`卸载${plugin.name}`} onClick={() => void uninstall(plugin.id)} disabled={busy}><Trash2 size={16} /></IconButton></div></article>)}{!data.plugins.length && <EmptyState title="尚未安装声明式插件" description="选择包含 id、name、version、description、kind=events 与 subscriptions 的 JSON 清单。插件默认停用，不执行 Python 或任意脚本。" />}</main><aside className="surface host-panel"><SectionHeader title="原生事件订阅" description="只允许下列真实命名空间；每次投递按插件 ID 与事件 ID 去重。" /><div className="namespace-list">{namespaces.map(item => <span key={item}>{item}.*</span>)}</div><h3>真实事件记录</h3><div className="event-log">{data.events.map(event => <code key={`${event.pluginId}-${event.eventId}`}>{event.pluginName} · {event.kind} · #{event.eventId}</code>)}{!data.events.length && <span>尚无插件事件投递。</span>}</div><Button variant="secondary" size="sm" onClick={() => void ping()}><Zap size={15} />发送原生 ping 事件</Button><p className="form-help">ping 由后端实际写入 outbox，不伪造方案完成或测验就绪事件。</p></aside></div></>}

    {tab === '用量与成本' && <EmptyState title="原生用量与成本台账正在接入" description="将按真实供应商返回的 tokens、完成／取消／异常终态和实际费率计算，不展示固定金额或虚构趋势。" />}

    {tab === '开发者资源' && data && <div className="developer-layout"><main><section className="surface skill-assets"><SectionHeader title="可审查的 Skill 与原生运维资产" description="历史 Skill 仅作方法论参考，不是当前原生运行时；内容按原始文件读取并校验 SHA-256。" />{data.assets.map(asset => <div key={asset.id}><FileCode2 size={18} /><div><strong>{asset.title}</strong><span>{asset.kind === 'legacy-skill' ? '历史方法论资产，不直接执行' : 'Tauri 2 原生应用运维'} · {asset.bytes} 字节</span></div><Button variant="ghost" size="sm" onClick={() => void download(asset.id)}><ArrowDownToLine size={15} />下载</Button></div>)}</section><section className="surface security-matrix"><SectionHeader title="当前原生扩展边界" description="仅展示实际实现，不宣称未提供的认证或在线安全能力。" />{[['清单校验', '仅接受声明式 JSON 事件插件；拒绝未知字段与脚本入口'], ['路径边界', '插件 ID 经过校验，安装文件限制在应用数据目录'], ['事件去重', '真实事件先写 outbox；插件与事件 ID 联合去重'], ['重载保护', '无效清单不改变当前启用配置，并记录失败审计'], ['密钥边界', '插件清单不接受 API Key；密钥由系统密钥库管理'], ['生命周期', '启停、重载、卸载和重置均有真实持久化结果']].map(([name, description]) => <div key={name}><ShieldCheck size={17} /><strong>{name}</strong><span>{description}</span><Badge tone="neutral">原生实现</Badge></div>)}</section></main><aside><section className="surface startup-card"><Code2 size={23} /><h3>Tauri 2 原生运维</h3><div><span>交付方式</span><strong>NSIS 应用安装包</strong></div><div><span>持久化</span><strong>SQLite WAL · 原子事务 · 事件 outbox</strong></div><div><span>插件边界</span><strong>声明式清单 · 无脚本执行</strong></div><Button variant="secondary" onClick={() => void download('native-operations')}>下载原生运维说明</Button></section></aside></div>}
  </div>
}

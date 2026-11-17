import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, AlertTriangle, ArrowDownToLine, Blocks, Code2, FileCode2, Layers3, Plug, RefreshCw, ShieldCheck, Trash2, Upload, Zap } from '../components/icons'
import { Badge, Button, ConfirmDialog, EmptyState, IconButton, RevealExport, SectionHeader, Toggle } from '../components/UI'
import { backend, backendErrorMessage, onBackendEvent, reportBackendError } from '../backend/client'
import { pluginBackend, type PluginEvent, type PluginOverview, type PluginRecord } from '../backend/plugins'
import { deliversTo, pluginSetShrunk, subscriptionTargetsInternalNamespace } from '../pluginEventScope'
import UsagePanel from '../components/UsagePanel'

// 61#1/117#3：与后端 EVENT_NAMESPACES 白名单同步（后端一致性测试校验两侧一致；
// state.changed 为高频内部状态同步，不开放订阅）
const namespaces = ['plan', 'practice', 'review', 'material', 'agent', 'knowledge', 'community', 'provider', 'plugin', 'share', 'content', 'backup', 'sync', 'maintenance', 'focus', 'study', 'lecture', 'report', 'security', 'notifications', 'usage', 'project']

// P-140/P-024：投递判定与分页缓存判据下沉到 pluginEventScope.ts——原先内联
// 在页面里，后端 plugins::matches 已对 state/draft/application 提前返回
// false，前端却仍把 `*` 视为命中任意 kind，口径分叉使内部事件（state.changed
// 是打字保存的高频写路径）继续触发全量刷新。现在页面与回归测试共用同一份
// 实现，判据改动由测试发现。

export default function Integrations({ notify }: { notify: (message: string) => void }) {
  const [tab, setTab] = useState('插件体系')
  const [data, setData] = useState<PluginOverview | null>(null)
  const [busy, setBusy] = useState(false)
  // 62#1：初载失败原先整块空白只弹 5 秒 toast——补错误横幅+重试，
  // 对齐 Practice/Review 等同类页三态
  const [loadError, setLoadError] = useState('')
  // 144#4：补序号守卫（51#2 家族）——原先成功与失败分支均无守卫，旧请求
  // 失败晚到会覆盖新请求成功后的清错，成功分支还可能以旧快照覆盖新快照
  const refreshSeq = useRef(0)
  // P-027：已加载的「更早投递记录」分页缓存只在插件集变化时清空——原先每次
  // 成功刷新都清空，而事件驱动的 250ms 节流刷新会被高频落库事件反复触发
  // （store.rs 每次落库都调 plugins::dispatch，provider.chat_delta 等流式
  // 事件落库即产生投递），用户已翻页的历史记录每次刷新都被丢弃
  const installedIds = useRef<string[]>([])
  // P-163：插件安装的文件输入引用——可访问路径由真实按钮承担（label 不可
  // 聚焦，键盘用户原先只能 Tab 到那个无焦点反馈的隐藏输入）
  const pluginFileRef = useRef<HTMLInputElement>(null)
  const refresh = useCallback(async () => {
    const ticket = ++refreshSeq.current
    try {
      const overview = await pluginBackend.overview()
      if (ticket !== refreshSeq.current) return
      setData(overview)
      setLoadError('')
      // 156#5：插件集变化（卸载/重装）时清空 earlier 分页缓存——服务端
      // plugin_deliveries 已随卸载 CASCADE 清空，内存旧行原先在「卸载后
      // 重装同 id」时复活为幽灵投递记录；B-19 的过滤只对「当前未安装」
      // 生效，重装同 id 后不可辨。判据为「已安装插件 id 有减少」——
      // 单纯新增插件不使既有历史失效，无需丢弃已翻页的记录
      const nextIds = overview.plugins.map(plugin => plugin.id).sort()
      if (pluginSetShrunk(installedIds.current, nextIds)) {
        setEarlier(current => current.length ? [] : current)
        setEarlierDone(false)
      }
      installedIds.current = nextIds
    } catch (cause) { if (ticket === refreshSeq.current) setLoadError(backendErrorMessage(cause)) }
  }, [])
  // P-140：事件回调需要最新的插件订阅清单——用 ref 取当前快照，避免因 data
  // 变化重建事件订阅（重建窗口会丢事件）
  const pluginsRef = useRef<PluginRecord[]>([])
  useEffect(() => { pluginsRef.current = data?.plugins ?? [] }, [data])
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
    // 80#7b：事件刷新尾沿节流（全站唯一漏配页）——插件批量投递风暴
    // 不再逐事件全量重拉 overview
    let refreshTimer: number | undefined
    onBackendEvent(event => {
      // P-140：按插件实际订阅判定是否会产生投递——原先只匹配 4 个命名空间
      // 前缀，其余白名单命名空间（community./provider./agent./focus. 等）
      // 的投递不刷新记录列表；32#22 的「无关事件不触发全量刷新」意图保留
      if (!deliversTo(pluginsRef.current, event.kind)) return
      if (refreshTimer !== undefined) window.clearTimeout(refreshTimer)
      refreshTimer = window.setTimeout(() => { refreshTimer = undefined; void refresh() }, 250)
    }).then(stop => { if (disposed) stop(); else unlisten = stop }).catch(reportBackendError)
    return () => { disposed = true; unlisten?.(); if (refreshTimer !== undefined) window.clearTimeout(refreshTimer) }
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
    try {
      await pluginBackend.uninstall(id)
      // P-027：服务端已随卸载 CASCADE 清掉该插件的 plugin_deliveries，
      // 内存里的 earlier 行同步丢弃——卸载与「重装同 id」之间若没有一次
      // 成功的 overview 快照（刷新失败），id 集合对比看不出变化，旧行会
      // 以幽灵投递记录复活
      setEarlier(current => current.length ? [] : current)
      setEarlierDone(false)
      await refresh()
      setUninstallId(null)
      notify('插件记录与安装清单已删除')
    }
    catch (error) { reportBackendError(error, 'write') } finally { setBusy(false) }
  }
  const ping = async () => {
    // 74#12：轻写操作也配 busy——双击连写两条 ping outbox 事件
    if (busy) return
    setBusy(true)
    try { const id = await pluginBackend.ping(); await refresh(); notify(`已发送真实原生事件 plugin.ping，事件 ID ${id}`) }
    catch (error) { reportBackendError(error, 'write') } finally { setBusy(false) }
  }
  // 43#2：资产下载写盘期间禁用，防止连点叠出多个保存对话框
  const [downloading, setDownloading] = useState('')
  // 134#10：最近一次资产落盘路径——供「在资源管理器中显示」定位（后端已登记回执）
  const [downloadPath, setDownloadPath] = useState('')
  const download = async (id: string) => {
    if (downloading) return
    setDownloading(id)
    try {
      // 37#5：与其他导出一致——原生保存对话框选位置，由后端落盘
      const destination = await backend.saveMaterialFile(id, 'md')
      if (!destination) return
      const result = await pluginBackend.saveAsset(id, destination)
      setDownloadPath(destination)
      notify(`已下载真实资产（${result.bytes.toLocaleString()} 字节）`)
    } catch (error) { reportBackendError(error) } finally { setDownloading('') }
  }

  // 62#1：data 未就绪时的两态呈现——失败给横幅+重试，在途给加载提示
  const loadPending = loadError
    ? <div className="form-error" role="alert"><AlertTriangle size={16} />{loadError}<div><Button variant="secondary" size="sm" onClick={() => void refresh()}>重试</Button></div></div>
    : <p className="form-help" role="status">正在读取插件与资产状态…</p>
  // 115#7：事件刷新失败原先完全静默（loadError 仅在 data 为空时渲染、连
  // toast 都没有）——插件启停/事件记录停留旧值零提示；成功刷新即清除
  const staleBanner = loadError
    ? <div className="form-error" role="alert"><AlertTriangle size={16} />{loadError}<div><Button variant="secondary" size="sm" onClick={() => void refresh()}>重试</Button></div></div>
    : null

  return <div className="page integrations-page">
    <div className="page-heading"><div><h1>插件与开发者</h1><p>声明式扩展、真实事件和可审查资产；不执行不受信任的脚本。</p></div><Badge tone="green"><ShieldCheck size={14} />原生 SQLite · 密钥入系统密钥库</Badge></div>
    {data && staleBanner}
    <div className="integration-tabs">{['插件体系', '用量与成本', '开发者资源'].map(item => <button key={item} className={tab === item ? 'active' : ''} aria-pressed={tab === item} onClick={() => setTab(item)}>{item === '插件体系' ? <Blocks size={17} /> : item === '开发者资源' ? <Code2 size={17} /> : <Activity size={17} />}{item}</button>)}</div>

    {tab === '插件体系' && (data ? <><section className="plugin-summary">{/* P-118：有效事件订阅只统计已启用插件——后端 dispatch 只对 plugins.enabled = 1 的插件投递（plugins/mod.rs:772），停用插件的订阅不生效；NEW 4：指向内部命名空间的订阅（state/draft/application）后端不再投递，也不计入「有效」 */}<div><Plug size={23} /><span>已启用插件</span><strong>{data.enabledCount}<small> / {data.plugins.length}</small></strong></div><div><Layers3 size={23} /><span>有效事件订阅</span><strong>{data.plugins.reduce((sum, plugin) => sum + (plugin.enabled ? plugin.subscriptions.filter(pattern => !subscriptionTargetsInternalNamespace(pattern)).length : 0), 0)}</strong></div><div><Activity size={23} /><span>真实事件投递</span><strong>{data.eventCount}</strong></div><div className="plugin-upload">{/* P-163：文件输入退出 Tab 序（.visually-hidden 只裁剪不脱离 Tab 序，
    全局 :focus-visible 描边又画在 1px 裁剪区之外，焦点无视觉反馈）；可访问
    路径改由真实按钮承担——原生 label 不可聚焦，键盘用户原先只能 Tab 到那个
    无反馈的隐藏输入，输入自身也补上可访问名 */}
<input id="plugin-file" ref={pluginFileRef} type="file" accept=".json,application/json" className="visually-hidden" tabIndex={-1} aria-label="安装 JSON 事件插件" disabled={busy} onChange={event => { void install(event.target.files?.[0]); event.target.value = '' }} /><button type="button" className="btn btn-secondary btn-md" disabled={busy} onClick={() => pluginFileRef.current?.click()}><Upload size={16} />安装 JSON 事件插件</button></div></section><div className="integration-main-grid"><main className="plugin-grid">{data.plugins.map(plugin => <article className="plugin-card" key={plugin.id}><div className="plugin-card-head"><span><Blocks size={19} /></span><Badge tone={plugin.enabled ? 'green' : 'neutral'}>{plugin.enabled ? '已启用' : '已停用'}</Badge></div><h2>{plugin.name}</h2><p>{plugin.description}</p><small>声明式事件插件 · v{plugin.version} · 第 {plugin.generation} 代配置</small><div className="subscription-list">{plugin.subscriptions.map(pattern => <code key={pattern} title={subscriptionTargetsInternalNamespace(pattern) ? '内部命名空间，不对插件开放，不会投递' : undefined}>{pattern}{subscriptionTargetsInternalNamespace(pattern) ? '（内部命名空间，不投递）' : ''}</code>)}</div><small>{plugin.eventCount} 条真实投递</small><div>{/* 48#14：可访问名固定为用途——原先 label 随状态翻转（已启用/已停用），读屏播报把名称与 aria-checked 状态混在一起 */}
<Toggle checked={plugin.enabled} onChange={value => void toggle(plugin.id, value)} disabled={busy} label="启用此插件" /><IconButton label={`重载${plugin.name}`} onClick={() => void reload(plugin.id)} disabled={busy}><RefreshCw size={16} /></IconButton><IconButton label={`卸载${plugin.name}`} onClick={() => setUninstallId(plugin.id)} disabled={busy}><Trash2 size={16} /></IconButton></div></article>)}{!data.plugins.length && <EmptyState title="尚未安装声明式插件" description="选择包含 id、name、version、description、kind=events 与 subscriptions 的 JSON 清单。插件默认停用，不执行 Python 或任意脚本。" />}</main><aside className="surface host-panel"><SectionHeader title="原生事件订阅" description="只允许下列真实命名空间；每次投递按插件 ID 与事件 ID 去重。" /><div className="namespace-list">{namespaces.map(item => <span key={item}>{item}.*</span>)}</div>{/* NEW 4：内部命名空间不对插件开放（后端 plugins::matches 提前返回 false）——通配订阅 `*` 与显式订阅（如 draft.*）都不投递；存量插件若订阅了它们，投递计数不再增长，此处给出可见说明，避免订阅静默失效 */}<p className="form-help">state / draft / application 是应用内部事件命名空间，不对插件开放：通配订阅 `*` 与显式订阅（如 draft.*）都不会收到这些事件，存量订阅它们的插件「真实投递」计数不会再增长。</p><h3>真实事件记录</h3><div className="event-log">{events.map(event => <code key={`${event.pluginId}-${event.eventId}`}>{event.pluginName} · {event.kind} · #{event.eventId}</code>)}{!events.length && <span>尚无插件事件投递。</span>}</div>{events.length >= 100 && !earlierDone && <Button variant="ghost" size="sm" disabled={loadingEarlier} onClick={() => void loadEarlier()}>{loadingEarlier ? '正在加载更早事件…' : '加载更早事件'}</Button>}<Button variant="secondary" size="sm" disabled={busy} onClick={() => void ping()}><Zap size={15} />发送原生 ping 事件</Button><p className="form-help">ping 由后端实际写入 outbox，不伪造方案完成或测验就绪事件。</p></aside></div></> : loadPending)}

    <ConfirmDialog open={uninstallId !== null} onClose={() => setUninstallId(null)} onConfirm={() => { const id = uninstallId; if (id !== null) void uninstall(id) }} title="卸载这个插件？" description="将删除插件记录、安装清单与全部投递历史；此操作不可恢复。" confirmLabel="确认卸载" busy={busy} />

    {tab === '用量与成本' && <UsagePanel notify={notify} />}

    {tab === '开发者资源' && (data ? <div className="developer-layout"><main><section className="surface skill-assets"><SectionHeader title="可审查的 Skill 与原生运维资产" description="历史 Skill 仅作方法论参考，不是当前原生运行时；内容按原始文件读取并校验 SHA-256。" action={downloadPath ? <RevealExport path={downloadPath} /> : undefined} />{data.assets.map(asset => <div key={asset.id}><FileCode2 size={18} /><div><strong>{asset.title}</strong><span>{asset.kind === 'legacy-skill' ? '历史方法论资产，不直接执行' : 'Tauri 2 原生应用运维'} · {asset.bytes.toLocaleString()} 字节</span></div><Button variant="ghost" size="sm" disabled={downloading !== ''} onClick={() => void download(asset.id)}>{downloading === asset.id ? '正在等待选择保存位置…' : <><ArrowDownToLine size={15} />下载</>}</Button></div>)}</section><section className="surface security-matrix"><SectionHeader title="当前原生扩展边界" description="仅展示实际实现，不宣称未提供的认证或在线安全能力。" />{[['清单校验', '仅接受声明式 JSON 事件插件；拒绝未知字段与脚本入口'], ['路径边界', '插件 ID 经过校验，安装文件限制在应用数据目录'], ['事件去重', '真实事件先写 outbox；插件与事件 ID 联合去重'], ['重载保护', '无效清单不改变当前启用配置，并记录失败审计'], ['密钥边界', '插件清单不接受 API Key；密钥由系统密钥库管理'], ['生命周期', '启停、重载、卸载和重置均有真实持久化结果']].map(([name, description]) => <div key={name}><ShieldCheck size={17} /><strong>{name}</strong><span>{description}</span><Badge tone="neutral">原生实现</Badge></div>)}</section></main><aside><section className="surface startup-card"><Code2 size={23} /><h3>Tauri 2 原生运维</h3><div><span>交付方式</span><strong>NSIS 应用安装包</strong></div><div><span>持久化</span><strong>SQLite WAL · 原子事务 · 事件 outbox</strong></div><div><span>插件边界</span><strong>声明式清单 · 无脚本执行</strong></div><Button variant="secondary" disabled={downloading !== ''} onClick={() => void download('native-operations')}>{downloading === 'native-operations' ? '正在等待选择保存位置…' : '下载原生运维说明'}</Button></section></aside></div> : loadPending)}
  </div>
}

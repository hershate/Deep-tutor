import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, CheckCircle2, CircleAlert, Cloud, Copy, Download, Eye, EyeOff, FileJson, Gauge, Link2, ListFilter, LoaderCircle, LockKeyhole, Network, Plus, RefreshCw, Route, Search, Server, Settings2, ShieldCheck, Square, SquareArrowOutUpRight, Trash2, Upload, Zap } from 'lucide-react'
import { Badge, Button, IconButton, Modal, SectionHeader } from '../components/UI'
import ProviderRouting, { AggregateModels, ProviderRouteLogs, emptyRouting } from '../components/ProviderRouting'
import SyncPanel from '../components/SyncPanel'
import { backend, backendErrorMessage, deltaThrottle, isCancelRace, onBackendEvent } from '../backend/client'
import type { ProviderExport, ProviderKind, ProviderProfile, ProviderProfileInput, ProviderProtocol, ProviderRequest, ProviderTestResult, ProviderRouteLog, RoutingSettings, CircuitSnapshot } from '../backend/client'

type Preset = { name: string; preset: string; kind: ProviderKind; protocol: ProviderProtocol; baseUrl: string; models: string[]; defaultModel: string; docs?: string }
type EditorForm = { name: string; preset: string; kind: ProviderKind; protocol: ProviderProtocol; baseUrl: string; models: string[]; defaultModel: string; key: string; note: string }
type View = '供应商' | '任务路由' | '故障转移' | '配置与同步'

const presets: Preset[] = [
  { name: 'DeepSeek', preset: 'deepseek', kind: '官方', protocol: 'OpenAI Chat', baseUrl: 'https://api.deepseek.com/v1', models: ['deepseek-chat', 'deepseek-reasoner'], defaultModel: 'deepseek-chat', docs: 'https://api-docs.deepseek.com/' },
  { name: 'Moonshot Kimi', preset: 'moonshot', kind: '官方', protocol: 'OpenAI Chat', baseUrl: 'https://api.moonshot.cn/v1', models: ['moonshot-v1-128k', 'kimi-k2-0711-preview'], defaultModel: 'moonshot-v1-128k', docs: 'https://platform.moonshot.cn/docs' },
  { name: 'Anthropic', preset: 'anthropic', kind: '官方', protocol: 'Anthropic Messages', baseUrl: 'https://api.anthropic.com', models: ['claude-sonnet-4-20250514', 'claude-3-5-haiku-latest'], defaultModel: 'claude-sonnet-4-20250514', docs: 'https://docs.anthropic.com/en/api/messages' },
  { name: 'OpenAI', preset: 'openai', kind: '官方', protocol: 'OpenAI Responses', baseUrl: 'https://api.openai.com/v1', models: ['gpt-4.1', 'gpt-4o-mini', 'o4-mini'], defaultModel: 'gpt-4.1', docs: 'https://platform.openai.com/docs/api-reference' },
  { name: 'Google Gemini', preset: 'gemini', kind: '官方', protocol: 'Gemini Native', baseUrl: 'https://generativelanguage.googleapis.com/v1beta', models: ['gemini-2.5-pro', 'gemini-2.0-flash'], defaultModel: 'gemini-2.0-flash', docs: 'https://ai.google.dev/gemini-api/docs' },
  { name: '通义千问', preset: 'qwen', kind: '官方', protocol: 'OpenAI Chat', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', models: ['qwen-plus', 'qwen-max'], defaultModel: 'qwen-plus' },
  { name: '智谱 GLM', preset: 'zhipu', kind: '官方', protocol: 'OpenAI Chat', baseUrl: 'https://open.bigmodel.cn/api/paas/v4', models: ['glm-4-plus', 'glm-4-air'], defaultModel: 'glm-4-plus' },
  { name: 'OpenRouter', preset: 'openrouter', kind: '兼容服务', protocol: 'OpenAI Chat', baseUrl: 'https://openrouter.ai/api/v1', models: ['openrouter/auto', 'openai/gpt-4o-mini', 'anthropic/claude-sonnet-4'], defaultModel: 'openrouter/auto', docs: 'https://openrouter.ai/docs' },
  { name: 'SiliconFlow 硅基流动', preset: 'siliconflow', kind: '兼容服务', protocol: 'OpenAI Chat', baseUrl: 'https://api.siliconflow.cn/v1', models: ['deepseek-ai/DeepSeek-V3', 'Qwen/Qwen2.5-72B-Instruct'], defaultModel: 'deepseek-ai/DeepSeek-V3' },
  { name: 'Mistral AI', preset: 'mistral', kind: '官方', protocol: 'OpenAI Chat', baseUrl: 'https://api.mistral.ai/v1', models: ['mistral-large-latest', 'mistral-small-latest'], defaultModel: 'mistral-large-latest' },
  { name: 'xAI Grok', preset: 'xai', kind: '官方', protocol: 'OpenAI Chat', baseUrl: 'https://api.x.ai/v1', models: ['grok-3', 'grok-3-mini'], defaultModel: 'grok-3' },
  { name: 'Groq', preset: 'groq', kind: '兼容服务', protocol: 'OpenAI Chat', baseUrl: 'https://api.groq.com/openai/v1', models: ['llama-3.3-70b-versatile', 'llama-3.1-8b-instant'], defaultModel: 'llama-3.3-70b-versatile' },
  { name: 'Azure OpenAI', preset: 'azure', kind: '官方', protocol: 'OpenAI Chat', baseUrl: 'https://YOUR-RESOURCE.openai.azure.com/openai/v1', models: ['YOUR-DEPLOYMENT'], defaultModel: 'YOUR-DEPLOYMENT', docs: 'https://learn.microsoft.com/azure/ai-services/openai/' },
  { name: 'OpenAI 兼容网关', preset: 'compatible', kind: '兼容服务', protocol: 'OpenAI Chat', baseUrl: 'https://api.example.com/v1', models: ['custom-chat-model'], defaultModel: 'custom-chat-model' },
  { name: 'Anthropic 兼容网关', preset: 'anthropic-compatible', kind: '兼容服务', protocol: 'Anthropic Messages', baseUrl: 'https://api.example.com', models: ['custom-claude-model'], defaultModel: 'custom-claude-model' },
  { name: 'Ollama', preset: 'ollama', kind: '本地模型', protocol: 'OpenAI Chat', baseUrl: 'http://127.0.0.1:11434/v1', models: ['qwen2.5:7b', 'llama3.1:8b', 'deepseek-r1:7b'], defaultModel: 'qwen2.5:7b', docs: 'https://github.com/ollama/ollama/blob/main/docs/api.md' },
  { name: 'LM Studio', preset: 'lmstudio', kind: '本地模型', protocol: 'OpenAI Chat', baseUrl: 'http://127.0.0.1:1234/v1', models: ['local-model'], defaultModel: 'local-model' },
  { name: 'vLLM / 私有部署', preset: 'vllm', kind: '本地模型', protocol: 'OpenAI Chat', baseUrl: 'http://127.0.0.1:8000/v1', models: ['private-model'], defaultModel: 'private-model' },
]
const emptyForm: EditorForm = { name: '', preset: '', kind: '官方', protocol: 'OpenAI Chat', baseUrl: '', models: [], defaultModel: '', key: '', note: '' }

function downloadJson(name: string, data: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function importedProfiles(data: unknown): ProviderProfileInput[] {
  const items = Array.isArray(data) ? data : (data as { providers?: unknown })?.providers
  if (!Array.isArray(items) || items.length > 200) throw new Error('invalid providers')
  return items.map(item => {
    if (!item || typeof item !== 'object') throw new Error('invalid provider')
    const value = item as Record<string, unknown>
    if (typeof value.name !== 'string' || typeof value.baseUrl !== 'string' || typeof value.defaultModel !== 'string') throw new Error('missing provider fields')
    const kind = value.kind || '兼容服务'
    const protocol = value.protocol || 'OpenAI Chat'
    if (!['官方', '兼容服务', '本地模型'].includes(String(kind)) || !['OpenAI Chat', 'OpenAI Responses', 'Anthropic Messages', 'Gemini Native'].includes(String(protocol))) throw new Error('unsupported provider')
    if (!Array.isArray(value.models) || value.models.some(model => typeof model !== 'string')) throw new Error('invalid models')
    return {
      name: value.name,
      preset: typeof value.preset === 'string' ? value.preset : 'custom',
      kind: kind as ProviderKind,
      protocol: protocol as ProviderProtocol,
      baseUrl: value.baseUrl,
      models: value.models,
      defaultModel: value.defaultModel,
      enabled: typeof value.enabled === 'boolean' ? value.enabled : true,
      note: typeof value.note === 'string' ? value.note : null,
    }
  })
}

export default function ProviderHub({ notify, embedded = false }: { notify: (message: string) => void; embedded?: boolean }) {
  const [profiles, setProfiles] = useState<ProviderProfile[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [routing, setRouting] = useState<RoutingSettings>(emptyRouting)
  const [routeLogs, setRouteLogs] = useState<ProviderRouteLog[]>([])
  const [circuits, setCircuits] = useState<CircuitSnapshot[]>([])
  const [requestModel, setRequestModel] = useState('')
  const [loading, setLoading] = useState(true)
  const [pageError, setPageError] = useState('')
  const [view, setView] = useState<View>('供应商')
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('全部')
  const [editorOpen, setEditorOpen] = useState(false)
  const [editorStep, setEditorStep] = useState(0)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState<EditorForm>(emptyForm)
  const [formError, setFormError] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [clearKey, setClearKey] = useState(false)
  const [busy, setBusy] = useState('')
  const [testingId, setTestingId] = useState('')
  const [testingAll, setTestingAll] = useState(false)
  const [testStage, setTestStage] = useState('')
  const [importPreview, setImportPreview] = useState<ProviderProfileInput[] | null>(null)
  const [deepLinkOpen, setDeepLinkOpen] = useState(false)
  const [deepLink, setDeepLink] = useState('')
  const [configPreview, setConfigPreview] = useState<ProviderExport | null>(null)
  const [prompt, setPrompt] = useState('请用一句话说明贝叶斯定理。')
  const [maxTokens, setMaxTokens] = useState(256)
  const [liveRequest, setLiveRequest] = useState<ProviderRequest | null>(null)
  const [streamBusy, setStreamBusy] = useState(false)
  const [streamError, setStreamError] = useState('')
  const requestIdRef = useRef('')
  const upstreamRequestIdRef = useRef('')
  const importRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    try {
      const [items, active, settings, logs, circuitStates] = await Promise.all([backend.listProviderProfiles(), backend.getActiveProvider(), backend.getRoutingSettings(), backend.listProviderRouteLogs(), backend.listProviderCircuits()])
      setProfiles(items)
      setActiveId(active)
      setRouting(settings)
      setRouteLogs(logs)
      setCircuits(circuitStates)
      setPageError('')
    } catch (error) {
      setPageError(backendErrorMessage(error))
    } finally {
      setLoading(false)
    }
  }, [])

  const saveRouting = useCallback(async (settings: RoutingSettings): Promise<boolean> => {
    setBusy('routing')
    try {
      setRouting(await backend.saveRoutingSettings(settings))
      await refresh()
      return true
    } catch (error) {
      setPageError(backendErrorMessage(error))
      return false
    } finally { setBusy('') }
  }, [refresh])
  const changeMode = (mode: RoutingSettings['mode']) => {
    let next = { ...routing, mode }
    if (mode === 'aggregate' && !next.aggregateProviderIds.length) {
      const fallback = profiles.find(profile => profile.id === activeId && profile.enabled) ?? profiles.find(profile => profile.enabled)
      if (!fallback) { notify('请先添加并启用一个供应商档案'); return }
      next = { ...next, aggregateProviderIds: [fallback.id], aggregateDefault: fallback.id }
    }
    if (mode === 'aggregate' && !next.aggregateDefault) next.aggregateDefault = next.aggregateProviderIds[0]
    void saveRouting(next).then(saved => { if (saved) notify(mode === 'direct' ? '已启用当前供应商直连' : mode === 'routing' ? '已启用进程内任务路由，不监听 HTTP 端口' : '已启用聚合模型选择，不自动故障转移') })
  }

  useEffect(() => {
    void refresh()
    let disposed = false
    let unlisten: (() => void) | undefined
    // 03#4：每条 delta 都全量重放是 O(n²)——150ms 窗口内合并为一次拉取
    const throttledChatRefresh = deltaThrottle()
    void onBackendEvent(event => {
      if (disposed) return
      if (event.kind === 'provider.route_attempt' && event.payload.requestId === requestIdRef.current && typeof event.payload.upstreamRequestId === 'string') upstreamRequestIdRef.current = event.payload.upstreamRequestId
      if (event.kind.startsWith('provider.') && !event.kind.startsWith('provider.chat_')) void refresh()
      if (event.kind.startsWith('provider.chat_')) {
        const requestId = event.payload.requestId
        if (typeof requestId === 'string' && (requestId === requestIdRef.current || requestId === upstreamRequestIdRef.current)) {
          throttledChatRefresh(() => {
            void backend.getProviderChat(requestId).then(setLiveRequest).catch(error => setStreamError(backendErrorMessage(error)))
          })
        }
      }
    }).then(remove => {
      if (disposed) remove()
      else unlisten = remove
    })
    return () => { disposed = true; unlisten?.() }
  }, [refresh])

  useEffect(() => {
    if (view !== '配置与同步') return
    let current = true
    void backend.exportProviderConfig().then(config => { if (current) setConfigPreview(config) }).catch(error => { if (current) setPageError(backendErrorMessage(error)) })
    return () => { current = false }
  }, [view, profiles, activeId, routing])
  useEffect(() => { setRequestModel('') }, [activeId, routing.mode])

  const filtered = useMemo(() => profiles.filter(profile => (filter === '全部' || profile.kind === filter) && `${profile.name}${profile.baseUrl}${profile.models.join(' ')}`.toLowerCase().includes(query.toLowerCase())), [profiles, filter, query])
  const activeProfile = profiles.find(profile => profile.id === activeId)
  const requestModels = useMemo(() => {
    if (routing.mode === 'aggregate') return profiles.filter(profile => profile.enabled && routing.aggregateProviderIds.includes(profile.id)).flatMap(profile => profile.models)
    const provider = profiles.find(profile => profile.id === routing.routes.agent?.providerId && profile.enabled) ?? activeProfile
    return provider?.models ?? []
  }, [profiles, routing, activeProfile])
  const toggleAggregate = (id: string) => {
    const included = routing.aggregateProviderIds.includes(id)
    if (included && routing.aggregateDefault === id) { notify('聚合默认供应商不能移出，请先更换默认项'); return }
    const aggregateProviderIds = included ? routing.aggregateProviderIds.filter(item => item !== id) : [...routing.aggregateProviderIds, id]
    void saveRouting({ ...routing, aggregateProviderIds, aggregateDefault: routing.aggregateDefault ?? aggregateProviderIds[0] ?? null })
  }
  const openAdd = () => {
    setEditingId(null)
    setEditorStep(0)
    setForm(emptyForm)
    setFormError('')
    setTestStage('')
    setClearKey(false)
    setEditorOpen(true)
  }
  const choosePreset = (preset: Preset) => {
    setForm({ name: preset.name, preset: preset.preset, kind: preset.kind, protocol: preset.protocol, baseUrl: preset.baseUrl, models: preset.models, defaultModel: preset.defaultModel, key: '', note: '' })
    setFormError('')
    setEditorStep(1)
  }
  const openEdit = (profile: ProviderProfile) => {
    setEditingId(profile.id)
    setEditorStep(1)
    setForm({ name: profile.name, preset: profile.preset, kind: profile.kind, protocol: profile.protocol, baseUrl: profile.baseUrl, models: profile.models, defaultModel: profile.defaultModel, key: '', note: profile.note || '' })
    setFormError('')
    setTestStage('')
    setClearKey(false)
    setEditorOpen(true)
  }

  const saveProfile = async (close = true): Promise<ProviderProfile | null> => {
    if (!form.name.trim() || !form.baseUrl.trim() || !form.defaultModel.trim()) {
      setFormError('请填写档案名称、Base URL 与默认模型。')
      return null
    }
    if (clearKey && form.key) {
      setFormError('输入新密钥与清除原密钥不能同时执行。')
      return null
    }
    setBusy('save')
    setFormError('')
    const apiKey = clearKey ? '' : form.key || undefined
    setForm(current => ({ ...current, key: '' }))
    try {
      const saved = await backend.upsertProviderProfile({ id: editingId || undefined, name: form.name, preset: form.preset || 'custom', kind: form.kind, protocol: form.protocol, baseUrl: form.baseUrl, models: form.models, defaultModel: form.defaultModel, enabled: profiles.find(profile => profile.id === editingId)?.enabled ?? true, note: form.note || null, apiKey })
      setEditingId(saved.id)
      setClearKey(false)
      setForm(current => ({ ...current, models: saved.models, key: '' }))
      setEditorOpen(!close)
      if (close) notify('供应商档案已保存；密钥由系统密钥库保管')
      await refresh()
      return saved
    } catch (error) {
      setFormError(backendErrorMessage(error))
      return null
    } finally {
      setBusy('')
    }
  }

  const testProfile = async (id: string, realRequest: boolean): Promise<ProviderTestResult | null> => {
    setTestingId(id)
    setTestStage(realRequest ? '正在发送最小真实模型请求…' : '正在读取上游 /models…')
    try {
      const result = await backend.testProvider(id, realRequest)
      setTestStage(result.kind === 'models' ? `真实 /models 返回 ${result.models?.length || 0} 个模型 · ${result.latencyMs} ms` : `真实模型请求已返回 · ${result.latencyMs} ms`)
      notify(result.kind === 'models' ? '模型列表读取成功；这不代表聊天请求已验证' : '真实模型请求成功，端点、凭据与模型名均已验证')
      await refresh()
      return result
    } catch (error) {
      const message = backendErrorMessage(error)
      setTestStage(message)
      notify(message)
      await refresh()
      return null
    } finally {
      setTestingId('')
    }
  }
  const testAll = async () => {
    setTestingAll(true)
    for (const profile of profiles) await testProfile(profile.id, false)
    setTestingAll(false)
  }
  const switchProvider = async (id: string) => {
    setBusy(`active-${id}`)
    try {
      await backend.setActiveProvider(id)
      await refresh()
      notify(routing.mode === 'direct' ? '当前供应商已切换；后续直连请求使用新档案' : '默认档案已切换；显式任务路由与聚合成员配置保持不变')
    } catch (error) { notify(backendErrorMessage(error)) } finally { setBusy('') }
  }
  const deleteProvider = async (id: string) => {
    setBusy(`delete-${id}`)
    try {
      await backend.deleteProviderProfile(id)
      await refresh()
      notify('档案及其系统密钥库凭据已删除；学习数据未改变')
    } catch (error) { notify(backendErrorMessage(error)) } finally { setBusy('') }
  }
  const fetchModels = async () => {
    const saved = await saveProfile(false)
    if (!saved) return
    const result = await testProfile(saved.id, false)
    const models = result?.models
    if (!models?.length) return
    setBusy('models')
    try {
      const defaultModel = models.includes(saved.defaultModel) ? saved.defaultModel : models[0]
      const updated = await backend.upsertProviderProfile({ ...saved, models, defaultModel })
      setForm(current => ({ ...current, models: updated.models, defaultModel: updated.defaultModel }))
      setTestStage(`已保存 /models 返回的 ${updated.models.length} 个模型；请重新检测以验证更新后的配置`)
      await refresh()
    } catch (error) { setFormError(backendErrorMessage(error)) } finally { setBusy('') }
  }
  const testFromEditor = async (realRequest: boolean) => {
    setEditorStep(2)
    const saved = await saveProfile(false)
    if (saved) await testProfile(saved.id, realRequest)
  }
  const applyImport = async (file: File) => {
    try {
      if (file.size > 1024 * 1024) throw new Error('file too large')
      setImportPreview(importedProfiles(JSON.parse(await file.text())))
    } catch { notify('导入失败：需要不超过 1 MB 且包含有效 providers 数组的 JSON 配置') }
  }
  const confirmImport = async () => {
    if (!importPreview) return
    setBusy('import')
    try {
      for (const profile of importPreview) await backend.upsertProviderProfile(profile)
      setImportPreview(null)
      await refresh()
      notify('脱敏档案已追加导入；密钥字段未读取，请逐档案配置')
    } catch (error) { notify(backendErrorMessage(error)) } finally { setBusy('') }
  }
  const importDeepLink = async () => {
    setBusy('deep-link')
    try {
      await backend.importProviderDeepLink(deepLink)
      setDeepLink('')
      setDeepLinkOpen(false)
      await refresh()
      notify('Deep Link 已导入；任何密钥参数均未保存')
    } catch (error) { notify(backendErrorMessage(error)) } finally { setBusy('') }
  }
  const exportConfig = async () => {
    try {
      const config = await backend.exportProviderConfig()
      setConfigPreview(config)
      downloadJson('DeepTutor-provider-config.json', config)
      notify('已从原生数据层导出脱敏配置，不包含 API Key')
    } catch (error) { notify(backendErrorMessage(error)) }
  }
  const startChat = async () => {
    if ((!activeId && routing.mode !== 'aggregate') || !prompt.trim() || streamBusy) return
    const requestId = `manual-${crypto.randomUUID()}`
    requestIdRef.current = requestId
    upstreamRequestIdRef.current = requestId
    setLiveRequest(null)
    setStreamError('')
    setStreamBusy(true)
    try {
      setLiveRequest(await backend.routeProviderChat(requestId, 'agent', activeId, requestModel || null, [{ role: 'user', content: prompt.trim() }], maxTokens))
    } catch (error) { setStreamError(backendErrorMessage(error)) } finally { setStreamBusy(false) }
  }
  const cancelChat = async () => {
    if (!requestIdRef.current) return
    // 03#5：流已结束但 invoke 未返回时点取消会报 no_active_request——
    // 请求实际已正常完成，按取消竞态静默处理
    try { await backend.cancelProviderChat(requestIdRef.current) } catch (error) { if (!isCancelRace(error)) setStreamError(backendErrorMessage(error)) }
  }

  return <div className={embedded ? 'provider-hub is-embedded' : 'page provider-hub'}>
    {!embedded && <div className="page-heading"><div><h1>模型接入中心</h1><p>原生管理供应商档案、系统凭据与真实模型请求。预设只是配置模板，不代表账号已经接入。</p></div><div className="heading-actions"><Button variant="secondary" onClick={() => void exportConfig()}><Download size={17} />导出脱敏配置</Button><Button onClick={openAdd}><Plus size={18} />添加供应商</Button></div></div>}
    <div className="provider-view-tabs">{(['供应商', '任务路由', '故障转移', '配置与同步'] as View[]).map(item => <button key={item} className={view === item ? 'active' : ''} onClick={() => setView(item)}>{item === '供应商' ? <Cloud size={17} /> : item === '任务路由' ? <Route size={17} /> : item === '故障转移' ? <Network size={17} /> : <Settings2 size={17} />}{item}</button>)}</div>
    {pageError && <div className="form-error"><CircleAlert size={16} />{pageError}<Button variant="ghost" size="sm" onClick={() => void refresh()}>重新读取</Button></div>}

    {view === '供应商' && <>
      <div className="provider-mode-bar"><div className="segmented" aria-label="接入模式">{([['direct', '直连模式'], ['routing', '本地路由'], ['aggregate', '聚合模式']] as const).map(([mode, label]) => <button key={mode} className={routing.mode === mode ? 'active' : ''} disabled={busy === 'routing'} onClick={() => changeMode(mode)}>{label}</button>)}</div><div className={`mode-description mode-${routing.mode}`}><span>{routing.mode === 'direct' ? `当前供应商直连：${activeProfile?.name ?? '尚未配置'}；不启动本地网络服务。` : routing.mode === 'routing' ? '任务路由在应用进程内执行，按任务选择真实上游，不监听 HTTP 端口。' : `${requestModels.length} 个上游模型已合并；按模型名选择供应商，不自动故障转移。`}</span>{routing.mode !== 'direct' && <Button variant="ghost" size="sm" onClick={() => setView('任务路由')}>配置路由</Button>}</div></div>
      {routing.mode === 'aggregate' && <AggregateModels settings={routing} profiles={profiles} onSave={saveRouting} saving={busy === 'routing'} />}
      <section className="surface native-chat-panel">
        <SectionHeader title="真实模型流式请求" description={`${routing.mode === 'direct' ? '当前供应商直连' : routing.mode === 'routing' ? '原生任务路由' : '聚合模型选择'} · 实际协议由上游档案决定`} action={<Badge tone={liveRequest?.status === 'done' ? 'green' : 'neutral'}>{liveRequest ? `原生状态：${liveRequest.status}` : '尚未请求'}</Badge>} />
        <label>请求内容<textarea rows={3} maxLength={16000} value={prompt} onChange={event => setPrompt(event.target.value)} /></label>
        <div className="form-grid"><label>请求模型<select value={requestModel} onChange={event => setRequestModel(event.target.value)}><option value="">使用路由与默认模型</option>{[...new Set(requestModels)].map(model => <option key={model} value={model}>{model}</option>)}</select></label><label>最大输出 Token<input type="number" min={1} max={8192} value={maxTokens} onChange={event => setMaxTokens(Math.max(1, Math.min(8192, Number(event.target.value) || 1)))} /></label><div className="editor-actions"><Button variant="secondary" onClick={() => void cancelChat()} disabled={!streamBusy}><Square size={15} />取消请求</Button><Button onClick={() => void startChat()} disabled={(!activeId && routing.mode !== 'aggregate') || !prompt.trim() || streamBusy}>{streamBusy ? <LoaderCircle size={16} className="spin" /> : <Zap size={16} />}发送真实请求</Button></div></div>
        {streamError && <div className="form-error"><CircleAlert size={16} />{streamError}</div>}
        {liveRequest && <><pre className="native-chat-output">{liveRequest.output || (liveRequest.status === 'running' ? '等待上游返回文本…' : '没有已保存的文本输出')}</pre><div className="provider-health-summary"><span>实际上游：{profiles.find(profile => profile.id === liveRequest.providerId)?.name ?? liveRequest.providerId}</span><span>模型：{liveRequest.model}</span><span>实际尝试：{liveRequest.attempts} 次</span><span>{liveRequest.usage ? `输入 ${liveRequest.usage.inputTokens ?? '未报告'} · 输出 ${liveRequest.usage.outputTokens ?? '未报告'} Token` : '上游未报告用量'}</span><span>{liveRequest.latencyMs === null ? '等待终态' : `${liveRequest.latencyMs} ms`}</span></div>{liveRequest.error && <div className="form-error"><CircleAlert size={16} />{liveRequest.error.code}：{liveRequest.error.message}</div>}</>}
      </section>
      <div className="provider-toolbar"><div className="search-field"><Search size={17} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索已保存的供应商、地址或模型…" /></div><div className="filter-row">{['全部', '官方', '兼容服务', '本地模型'].map(item => <button key={item} className={filter === item ? 'active' : ''} onClick={() => setFilter(item)}>{item}</button>)}</div><Button variant="secondary" size="sm" onClick={() => void testAll()} disabled={!profiles.length || Boolean(testingId)}>{testingAll ? <LoaderCircle size={16} className="spin" /> : <Gauge size={16} />}全部检测</Button><IconButton label="导入 JSON 配置" onClick={() => importRef.current?.click()}><Upload size={17} /></IconButton><IconButton label="从 Deep Link 导入" onClick={() => setDeepLinkOpen(true)}><Link2 size={17} /></IconButton><input ref={importRef} type="file" accept=".json" className="visually-hidden" onChange={event => { const file = event.target.files?.[0]; if (file) void applyImport(file); event.target.value = '' }} /></div>
      <div className="provider-health-summary"><span><CheckCircle2 size={16} />{profiles.filter(profile => profile.health === 'healthy').length} 个档案最近检测正常</span><span><CircleAlert size={16} />{profiles.filter(profile => profile.health === 'error').length} 个需要处理</span><span><LockKeyhole size={16} />密钥只存在系统密钥库，不进入档案或导出</span></div>
      {loading ? <div className="provider-empty"><LoaderCircle size={25} className="spin" /><h2>正在读取原生供应商档案</h2></div> : <div className="provider-card-list">{filtered.map(profile => { const active = profile.id === activeId; const testing = testingId === profile.id; return <article className={`provider-card ${active ? 'is-active' : ''} ${!profile.enabled ? 'is-disabled' : ''}`} key={profile.id}>
        <div className="provider-card-main"><span className={`provider-monogram kind-${profile.kind}`}>{profile.name.slice(0, 1)}</span><div className="provider-identity"><div><h2>{profile.name}</h2>{active && routing.mode !== 'aggregate' && <Badge tone="green">当前使用</Badge>}{routing.mode === 'aggregate' && routing.aggregateDefault === profile.id && <Badge tone="green">聚合默认</Badge>}<Badge tone="neutral">{profile.kind}</Badge></div><p><code>{profile.baseUrl}</code></p><div className="provider-model-line"><span>{profile.protocol}</span><strong>{profile.defaultModel}</strong><span>{profile.models.length} 个模型</span></div></div></div>
        <div className="provider-health"><span className={`health-${testing ? 'testing' : profile.health}`}>{testing ? <LoaderCircle size={15} className="spin" /> : profile.health === 'healthy' ? <CheckCircle2 size={15} /> : profile.health === 'error' ? <CircleAlert size={15} /> : <Activity size={15} />}{testing ? '检测中' : profile.health === 'healthy' ? `${profile.latencyMs ?? '已测'} ms` : profile.health === 'error' ? '最近检测失败' : '未检测'}</span><small>{profile.note || (profile.hasKey ? '凭据已存入系统密钥库' : profile.kind === '本地模型' ? '本地服务可留空密钥' : '未配置密钥')}</small></div>
        <div className="provider-card-actions">{routing.mode === 'aggregate' ? <><Button variant={routing.aggregateProviderIds.includes(profile.id) ? 'secondary' : 'primary'} size="sm" disabled={!profile.enabled || busy === 'routing'} onClick={() => toggleAggregate(profile.id)}>{routing.aggregateProviderIds.includes(profile.id) ? '移出聚合' : '加入聚合'}</Button>{routing.aggregateProviderIds.includes(profile.id) && <Button variant="ghost" size="sm" disabled={routing.aggregateDefault === profile.id || busy === 'routing'} onClick={() => void saveRouting({ ...routing, aggregateDefault: profile.id })}>{routing.aggregateDefault === profile.id ? '聚合默认' : '设为默认'}</Button>}</> : <Button variant={active ? 'secondary' : 'primary'} size="sm" onClick={() => void switchProvider(profile.id)} disabled={active || !profile.enabled || Boolean(busy)}>{active ? '正在使用' : '切换使用'}</Button>}<Button variant="secondary" size="sm" onClick={() => void testProfile(profile.id, false)} disabled={Boolean(testingId)}><Gauge size={15} />检测</Button><Button variant="ghost" size="sm" onClick={() => void testProfile(profile.id, true)} disabled={Boolean(testingId)}>真实请求</Button><Button variant="ghost" size="sm" onClick={() => openEdit(profile)}>编辑</Button><IconButton label={`删除${profile.name}`} onClick={() => void deleteProvider(profile.id)} disabled={Boolean(busy)}><Trash2 size={16} /></IconButton></div>
      </article>})}{!filtered.length && <div className="provider-empty"><Search size={25} /><h2>{profiles.length ? '没有匹配的档案' : '尚未配置供应商'}</h2><p>{profiles.length ? '清除搜索或切换类型。' : '选择一个预设或添加自定义档案；预设不会自动生成健康账号。'}</p><Button onClick={openAdd}><Plus size={16} />添加供应商</Button></div>}</div>}
      <div className="provider-security-note"><ShieldCheck size={17} /><p><strong>密钥处理：</strong>API Key 通过一次性 IPC 参数交给 Rust，写入当前用户的系统密钥库。界面不显示密钥掩码，数据库、事件、导出与日志均不保存密钥明文。</p></div>
    </>}

    {view === '任务路由' && <ProviderRouting settings={routing} profiles={profiles} activeId={activeId} onSave={saveRouting} saving={busy === 'routing'} />}
    {view === '故障转移' && <ProviderRouteLogs logs={routeLogs} settings={routing} profiles={profiles} circuits={circuits} onSave={saveRouting} saving={busy === 'routing'} />}
    {view === '配置与同步' && <div className="provider-config-layout"><main className="surface config-preview"><SectionHeader title="原生脱敏配置" description="内容直接来自 SQLite 中的真实档案；hasKey 只表示系统密钥库中是否存在凭据。" action={<div className="heading-actions"><Button variant="ghost" size="sm" disabled={!configPreview} onClick={() => { if (!configPreview) return; void navigator.clipboard.writeText(JSON.stringify(configPreview, null, 2)).then(() => notify('脱敏配置已复制')).catch(() => notify('复制失败，请使用下载')) }}><Copy size={15} />复制</Button><Button variant="secondary" size="sm" onClick={() => void exportConfig()}><Download size={15} />下载</Button></div>} /><pre>{configPreview ? JSON.stringify(configPreview, null, 2) : '正在读取原生脱敏配置…'}</pre><div className="config-import-actions"><Button variant="secondary" onClick={() => importRef.current?.click()}><Upload size={16} />导入并预演</Button><Button variant="ghost" onClick={() => setDeepLinkOpen(true)}><Link2 size={16} />从 Deep Link 添加</Button></div></main><aside><section className="surface config-safety"><ShieldCheck size={21} /><h2>原生数据处理</h2><ul><li>档案与事件保存在应用 SQLite 数据库</li><li>密钥写入系统密钥库，不进入备份</li><li>导入仅追加白名单中的非敏感字段</li><li>当前供应商须先切换才能删除</li></ul></section><SyncPanel notify={notify} /></aside></div>}

    <Modal open={editorOpen} onClose={() => setEditorOpen(false)} title={editingId ? '编辑供应商档案' : '添加供应商'} wide>
      <div className="provider-editor"><div className="editor-step-line">{['选择预设', '连接配置', '检测与完成'].map((name, index) => <span key={name} className={editorStep >= index ? 'active' : ''}>{index + 1} · {name}</span>)}</div>
        {editorStep === 0 && <><div className="preset-search"><ListFilter size={17} /><span>预设只填充连接字段，不创建账号、不伪造凭据或健康状态。</span></div><div className="preset-grid">{presets.map(preset => <button key={preset.preset} onClick={() => choosePreset(preset)}><span className={`provider-monogram kind-${preset.kind}`}>{preset.name.slice(0, 1)}</span><div><strong>{preset.name}</strong><small>{preset.protocol}</small></div><Badge tone="neutral">{preset.kind}</Badge></button>)}</div></>}
        {editorStep === 1 && <div className="provider-form"><div className="form-grid"><label>档案名称<input value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} placeholder="例如：DeepSeek · 工作账号" /></label><label>接入类型<select value={form.kind} onChange={event => setForm({ ...form, kind: event.target.value as ProviderKind })}><option>官方</option><option>兼容服务</option><option>本地模型</option></select></label></div><div className="form-grid"><label>上游协议<select value={form.protocol} onChange={event => setForm({ ...form, protocol: event.target.value as ProviderProtocol })}><option>OpenAI Chat</option><option>OpenAI Responses</option><option>Anthropic Messages</option><option>Gemini Native</option></select></label><label>默认模型<input value={form.defaultModel} onChange={event => setForm({ ...form, defaultModel: event.target.value })} placeholder="model-name" /></label></div><label>Base URL<input value={form.baseUrl} onChange={event => setForm({ ...form, baseUrl: event.target.value })} placeholder="https://api.example.com/v1" /></label>{presets.find(preset => preset.preset === form.preset)?.docs && <a className="provider-doc-link" href={presets.find(preset => preset.preset === form.preset)?.docs} target="_blank" rel="noreferrer">查看官方接入文档<SquareArrowOutUpRight size={14} /></a>}<label>API Key{form.kind === '本地模型' && <span className="optional-label">本地服务通常留空</span>}<div className="secret-input"><input type={showKey ? 'text' : 'password'} value={form.key} onChange={event => setForm({ ...form, key: event.target.value })} autoComplete="new-password" placeholder={editingId ? '留空保留原密钥；输入新值则替换' : '仅本次传给原生系统密钥库'} /><IconButton label={showKey ? '隐藏密钥' : '显示密钥'} onClick={() => setShowKey(!showKey)}>{showKey ? <EyeOff size={17} /> : <Eye size={17} />}</IconButton></div></label>{editingId && <label><input type="checkbox" checked={clearKey} onChange={event => setClearKey(event.target.checked)} /> 删除系统密钥库中的现有凭据</label>}<div className="model-fetch-row"><div><strong>可用模型</strong><span>{form.models.length ? `${form.models.length} 个 · 默认 ${form.defaultModel}` : '尚未读取真实模型列表'}</span></div><Button variant="secondary" size="sm" onClick={() => void fetchModels()} disabled={Boolean(testingId) || Boolean(busy)}><RefreshCw size={15} />保存并获取模型</Button></div><div className="model-check-list">{form.models.map(model => <label key={model}><input type="checkbox" checked={form.defaultModel === model} onChange={() => setForm({ ...form, defaultModel: model })} /><span>{model}</span>{form.defaultModel === model && <Badge tone="green">默认</Badge>}</label>)}</div><label>备注<textarea rows={2} value={form.note} onChange={event => setForm({ ...form, note: event.target.value })} placeholder="账号用途、部署区域或兼容注意事项…" /></label><div className="modal-callout"><Server size={17} />远程档案要求 HTTPS 与公网地址；本地 HTTP 仅允许回环或私网服务。OpenAI Chat、OpenAI Responses、Anthropic Messages 与 Gemini Native 均已接入原生流式请求和工具调用；实际可用性取决于端点、模型与凭据。</div>{formError && <div className="form-error"><CircleAlert size={16} />{formError}</div>}<div className="editor-actions"><Button variant="secondary" onClick={() => editingId ? setEditorOpen(false) : setEditorStep(0)}>{editingId ? '关闭' : '返回预设'}</Button><div><Button variant="ghost" onClick={() => setEditorStep(2)}>检测与完成</Button><Button onClick={() => void saveProfile()} disabled={Boolean(busy)}>{busy === 'save' ? <LoaderCircle size={15} className="spin" /> : null}保存档案</Button></div></div></div>}
        {editorStep === 2 && <div className="connection-check"><div className="connection-summary"><span className={`provider-monogram kind-${form.kind}`}>{form.name.slice(0, 1) || 'M'}</span><div><h3>{form.name}</h3><code>{form.baseUrl}</code><p>{form.protocol} · {form.defaultModel}</p></div></div><div className="check-explainer"><Gauge size={20} /><div><strong>两种检测验证的内容不同</strong><p>读取 /models 只验证端点与模型列表；真实聊天请求还验证凭据、模型名与协议。检测前会保存当前档案，结果来自实际上游响应。</p></div></div><div className="connection-test-actions"><Button variant="secondary" onClick={() => void testFromEditor(false)} disabled={Boolean(testingId) || Boolean(busy)}><Gauge size={16} />读取模型列表</Button><Button onClick={() => void testFromEditor(true)} disabled={Boolean(testingId) || Boolean(busy)}><Zap size={16} />发送真实测试请求</Button></div>{testStage && <div className="test-stage">{testingId ? <LoaderCircle size={17} className="spin" /> : <Activity size={17} />}<span>{testStage}</span></div>}{formError && <div className="form-error"><CircleAlert size={16} />{formError}</div>}<div className="local-cors-note"><Server size={17} /><p>请求由 Tauri 原生后端发送，不使用浏览器跨域访问。远程服务必须使用 HTTPS；本地服务不应暴露到公网。</p></div><div className="editor-actions"><Button variant="secondary" onClick={() => setEditorStep(1)}>返回配置</Button><Button onClick={() => void saveProfile()} disabled={Boolean(busy)}>保存并关闭</Button></div></div>}
      </div>
    </Modal>
    <Modal open={Boolean(importPreview)} onClose={() => setImportPreview(null)} title="导入预演 · 脱敏供应商配置"><div className="import-provider-preview"><div className="modal-callout"><FileJson size={18} />检测到 {importPreview?.length || 0} 个供应商。仅追加白名单中的非敏感字段，不读取或导入任何密钥。</div>{importPreview?.map((profile, index) => <div key={`${profile.name}-${index}`}><Cloud size={17} /><div><strong>{profile.name}</strong><code>{profile.baseUrl}</code></div><Badge tone="neutral">{profile.protocol}</Badge></div>)}<div className="confirm-actions"><Button variant="secondary" onClick={() => setImportPreview(null)}>取消</Button><Button onClick={() => void confirmImport()} disabled={busy === 'import'}>{busy === 'import' ? '正在写入…' : '确认追加导入'}</Button></div></div></Modal>
    <Modal open={deepLinkOpen} onClose={() => setDeepLinkOpen(false)} title="从 Deep Link 添加供应商"><div className="form-stack"><label>Deep Link<textarea rows={4} value={deepLink} onChange={event => setDeepLink(event.target.value)} placeholder="deeptutor://provider?name=My%20API&baseUrl=https%3A%2F%2Fapi.example.com%2Fv1&protocol=OpenAI%20Chat&model=custom-model" /></label><p className="form-help">原生后端只读取 name、baseUrl、protocol、model；链接中的密钥参数会被忽略。请勿在链接中放置 API Key。</p><Button onClick={() => void importDeepLink()} disabled={!deepLink.trim() || busy === 'deep-link'}><Link2 size={16} />解析并添加</Button></div></Modal>
  </div>
}

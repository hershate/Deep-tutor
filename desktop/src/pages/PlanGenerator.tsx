import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ArrowLeft, ArrowRight, BookOpen, Brain, Check, CheckCircle2, ChevronRight, Circle, Clipboard, Clock3, CloudUpload, File, FileText, Globe2, Hash, LoaderCircle, RefreshCw, Route, ShieldCheck, Sparkles, Square, Trash2, Upload, Zap } from 'lucide-react'
import type { PageId } from '../data'
import { Badge, Button, IconButton, Modal, ProgressBar, SectionHeader } from '../components/UI'
import SharePlanDialog from '../components/SharePlanDialog'
import ExportCenter from '../components/ExportCenter'
import { localShareUrl } from '../backend/sharing'
import { backend, backendErrorMessage, isCancelRace, onBackendEvent, deltaThrottle } from '../backend/client'
import type { LearningCheckName, LearningGraph, LearningPlan, Material, ProviderRequest } from '../backend/client'
import { webBackend } from '../backend/web'
import { usageBackend } from '../backend/usage'
import { mediaBackend } from '../backend/media'
import { clampToBytes, utf8Bytes } from '../limits'

const phaseNames = ['输入资料', '澄清目标', '实时生成', '确认方案']
const sourceTabs = ['文件上传', '纯文本', 'URL 抓取', '浏览器剪藏']
const regenerationNames: Record<number, string> = { 2: '知识图谱', 3: '阶段设计', 4: '资源检索', 5: '学习排期', 6: '五维校验' }
const validationNames: [LearningCheckName, string][] = [
  ['dependencyClosure', '依赖闭包'],
  ['difficultyStep', '难度阶梯'],
  ['cognitiveLoad', '认知负荷'],
  ['milestoneFeasibility', '里程碑可达'],
  ['gapAnalysis', '缺口分析'],
]
const statusNames: Record<string, string> = { pending: '等待', running: '处理中', done: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' }
const planStatusNames: Record<string, string> = { running: '正在生成', cancelling: '正在取消', done: '已生成', degraded: '降级交付', failed: '生成失败', cancelled: '已取消', interrupted: '已中断' }
const parsedStatuses = ['parsed', 'degraded']
const materialStatusNames: Record<string, string> = { staged: '已暂存', queued: '等待解析', parsing: '解析中', parsed: '已解析', degraded: '部分解析', failed: '解析失败', cancelled: '已取消', metadata_only: '原件不可用' }

function localDeadline() {
  const date = new Date()
  date.setDate(date.getDate() + 30)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

export default function PlanGenerator({ onNavigate, notify }: { onNavigate: (page: PageId) => void; notify: (message: string) => void }) {
  const [step, setStep] = useState(0)
  const [materials, setMaterials] = useState<Material[]>([])
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [plans, setPlans] = useState<LearningPlan[]>([])
  const [plan, setPlan] = useState<LearningPlan | null>(null)
  const [sourceTab, setSourceTab] = useState('文件上传')
  const [text, setText] = useState('')
  const [textTitle, setTextTitle] = useState('')
  const [url, setUrl] = useState('')
  const [goal, setGoal] = useState('')
  const [level, setLevel] = useState('')
  const [minutes, setMinutes] = useState(45)
  const [deadline, setDeadline] = useState(localDeadline)
  const [milestone, setMilestone] = useState('')
  const [milestoneDate, setMilestoneDate] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [lastEventId, setLastEventId] = useState<number | null>(null)
  const [liveRequest, setLiveRequest] = useState<ProviderRequest | null>(null)
  const [regenOpen, setRegenOpen] = useState(false)
  const [regenScope, setRegenScope] = useState(3)
  const [shareOpen, setShareOpen] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const planRef = useRef<LearningPlan | null>(null)
  const routeUpstreams = useRef<Record<string, string>>({})
  useEffect(() => { planRef.current = plan }, [plan])
  // 局部重生成上限由用量设置决定（默认 2，可配 1–3），与后端 regeneration_limit 一致
  const [regenLimit, setRegenLimit] = useState(2)
  useEffect(() => { let active = true; usageBackend.overview().then(data => { if (active) setRegenLimit(data.settings.regenerationLimit) }).catch(() => { /* 读取失败时按默认上限展示 */ }); return () => { active = false } }, [])

  const refreshMaterials = useCallback(async () => {
    try {
      const items = await backend.listMaterials()
      setMaterials(items)
      setSelectedIds(current => current.filter(id => items.some(material => material.id === id)))
    } catch (cause) { setError(backendErrorMessage(cause)) }
  }, [])
  const refreshPlans = useCallback(async () => {
    try {
      const items = await backend.listLearningPlans()
      setPlans(items)
      setPlan(current => current ? items.find(item => item.id === current.id) || null : items[0] || null)
      if (!items.length) setStep(0)
      return items
    } catch (cause) {
      setError(backendErrorMessage(cause))
      return []
    }
  }, [])
  const selectPlan = useCallback(async (id: string) => {
    try {
      const next = await backend.getLearningPlan(id)
      setPlan(next)
      setStep(next.result ? 3 : 2)
      setLiveRequest(null)
      setError('')
    } catch (cause) { setError(backendErrorMessage(cause)) }
  }, [])

  useEffect(() => {
    void refreshMaterials()
    void refreshPlans().then(items => { if (items[0]) setStep(items[0].result ? 3 : 2) })
    let disposed = false
    let unlisten: (() => void) | undefined
    const throttledFetch = deltaThrottle()
    // 32#17：批量导入时 material.* 逐文件事件合并为一次全量拉取
    const throttledMaterials = deltaThrottle()
    void onBackendEvent(event => {
      if (disposed) return
      if (event.kind.startsWith('material.')) throttledMaterials(() => void refreshMaterials())
      if (event.kind.startsWith('plan.')) {
        const id = event.payload.planId
        if (typeof id !== 'string') return
        if (!planRef.current && event.kind === 'plan.started') void selectPlan(id)
        if (planRef.current?.id === id) {
          setLastEventId(event.id)
          void backend.getLearningPlan(id).then(next => {
            // 32#3：响应返回时用户可能已手动切换到历史方案——过期响应
            // 不得覆盖当前选择或强行拉回第 3 步
            if (disposed || planRef.current?.id !== next.id) return
            setPlan(next)
            if (next.result) setStep(3)
          }).catch(cause => setError(backendErrorMessage(cause)))
        }
      }
      if (event.kind === 'provider.route_attempt' && typeof event.payload.requestId === 'string' && typeof event.payload.upstreamRequestId === 'string') {
        // failover 后 delta 携带 {requestId}-fN 上游 id：记录映射再对齐步骤的逻辑 id
        routeUpstreams.current[event.payload.upstreamRequestId] = event.payload.requestId
      }
      if (event.kind === 'provider.chat_delta') {
        const requestId = event.payload.requestId
        const logicalId = typeof requestId === 'string' ? routeUpstreams.current[requestId] ?? requestId : undefined
        const active = planRef.current?.steps.some(item => item.requestId === logicalId && item.status === 'running')
        if (active && typeof logicalId === 'string') {
          setLastEventId(event.id)
          // 多条 delta 合并为一次全量拉取，避免长回答期间逐条重放
          throttledFetch(() => void backend.getProviderChat(logicalId).then(setLiveRequest).catch(cause => setError(backendErrorMessage(cause))))
        }
      }
    }).then(remove => { if (disposed) remove(); else unlisten = remove })
    return () => { disposed = true; unlisten?.() }
  }, [refreshMaterials, refreshPlans, selectPlan])

  const selected = useMemo(() => materials.filter(material => selectedIds.includes(material.id) && mediaBackend.isText(material.kind)), [materials, selectedIds])
  const ready = selected.length > 0 && selected.every(material => parsedStatuses.includes(material.status))
  const running = plan?.status === 'running' || plan?.status === 'cancelling'
  const graph: LearningGraph | undefined = plan?.artifacts.step2?.graph || plan?.result?.graph
  const totalTokens = plan?.steps.reduce((sum, item) => sum + (item.usage?.inputTokens || 0) + (item.usage?.outputTokens || 0), 0) || 0
  const currentRequestId = plan?.steps.find(item => item.status === 'running' && item.requestId)?.requestId || plan?.steps.filter(item => item.requestId).at(-1)?.requestId
  const liveOutput = liveRequest?.requestId === currentRequestId ? liveRequest : null
  const result = plan?.result || null

  const toggleMaterial = (id: number) => {
    const material = materials.find(item => item.id === id)
    if (!material) return
    if (!mediaBackend.isText(material.kind)) {
      notify('音视频与图片仅支持原件预览，不能作为文字模型的方案资料')
      return
    }
    if (selectedIds.includes(id)) setSelectedIds(selectedIds.filter(item => item !== id))
    else if (selectedIds.length >= 8) notify('每个方案最多选择 8 份资料')
    else setSelectedIds([...selectedIds, id])
  }
  const importFiles = async () => {
    setBusy('files')
    setError('')
    try {
      const paths = await backend.openMaterialFiles()
      if (!paths) return
      // 19#5：后端单次最多 32 个文件——超过时分批提交，避免一次选择
      // >32 个文件整批被拒（与资料库页的导入行为一致）
      const regular = paths.filter(path => !path.toLowerCase().endsWith('.zip'))
      const outcomes = []
      for (let index = 0; index < regular.length; index += 32) {
        outcomes.push(...await backend.importMaterialFiles(regular.slice(index, index + 32)))
      }
      for (const path of paths.filter(path => path.toLowerCase().endsWith('.zip'))) {
        const archive = await backend.importMaterialArchive(path)
        outcomes.push(...archive.outcomes)
        if (archive.skipped.length) notify(`资料包中 ${archive.skipped.length} 个不支持条目已跳过`)
      }
      for (const outcome of outcomes) {
        if (outcome.material && mediaBackend.isText(outcome.material.kind)) setSelectedIds(current => current.includes(outcome.material!.id) ? current : [...current, outcome.material!.id])
        if (outcome.error) notify(backendErrorMessage(outcome.error))
      }
      await refreshMaterials()
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const importText = async () => {
    if (!text.trim()) return
    setBusy('text')
    try {
      const result = await backend.importMaterialText(textTitle.trim() || `文本资料_${materials.length + 1}`, text)
      setSelectedIds(current => current.includes(result.material.id) ? current : [...current, result.material.id])
      setText('')
      setTextTitle('')
      await refreshMaterials()
      notify(result.deduplicated ? '内容哈希相同，已复用现有资料' : '文本已暂存到原生资料库')
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const importUrl = async (source?: string) => {
    const target = (source ?? url).trim()
    if (!target || busy) return
    setBusy('url')
    setError('')
    try {
      const result = await webBackend.import(target)
      setSelectedIds(current => current.includes(result.material.id) ? current : [...current, result.material.id])
      setUrl('')
      await refreshMaterials()
      notify(result.deduplicated ? `内容相同，已复用「${result.material.title}」` : `已抓取「${result.material.title}」并保存真实正文与来源`)
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const parseSelected = async () => {
    setBusy('parse')
    try {
      for (const material of selected) {
        if (!parsedStatuses.includes(material.status)) await backend.enqueueMaterialParse(material.id)
      }
      await refreshMaterials()
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const removeMaterial = async (id: number) => {
    try {
      await backend.deleteMaterial(id)
      setSelectedIds(current => current.filter(item => item !== id))
      await refreshMaterials()
    } catch (cause) { setError(backendErrorMessage(cause)) }
  }
  const start = async () => {
    if (!goal.trim() || !level.trim() || !deadline || !ready) {
      setError('请先选择并完成资料解析，再填写目标、当前基础与截止日期。')
      return
    }
    setBusy('start')
    setError('')
    try {
      const next = await backend.startLearningPlan({ goal: goal.trim(), level: level.trim(), dailyMinutes: minutes, deadline, materialIds: selectedIds, milestone: milestone.trim() || null, milestoneDate: milestoneDate || null })
      setPlan(next)
      setPlans(current => [next, ...current.filter(item => item.id !== next.id)])
      setLiveRequest(null)
      setStep(2)
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const cancel = async () => {
    if (!plan) return
    setBusy('cancel')
    try { setPlan(await backend.cancelLearningPlan(plan.id)) } catch (cause) { if (!isCancelRace(cause)) setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const resume = async () => {
    if (!plan) return
    setBusy('resume')
    try {
      setPlan(await backend.resumeLearningPlan(plan.id))
      setStep(2)
      await refreshPlans()
    } catch (cause) { if (!isCancelRace(cause)) setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const regenerate = async () => {
    if (!plan) return
    setBusy('regenerate')
    try {
      setPlan(await backend.regenerateLearningPlan(plan.id, regenScope))
      setRegenOpen(false)
      setStep(2)
      notify(`已开始重跑「${regenerationNames[regenScope]}」及其下游步骤`)
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const adopt = async () => {
    if (!plan) return
    setBusy('adopt')
    try {
      await backend.adoptLearningPlan(plan.id)
      const next = await backend.getLearningPlan(plan.id)
      setPlan(next)
      notify(next.adoptedProjectId !== null ? `方案已采用，已创建项目 #${next.adoptedProjectId} 与原生学习任务` : '方案已采用，已创建原生学习任务')
      onNavigate('plan')
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }

  return <div className="page generator-page">
    <div className="page-heading"><div><h1>AI 学习方案生成</h1><p>资料先暂存，目标再澄清；每一步都由原生后端执行并保存。</p></div><div className="heading-actions"><select aria-label="查看历史方案" value={plan?.id || ''} onChange={event => { if (event.target.value) void selectPlan(event.target.value); else { setPlan(null); setStep(0) } }}><option value="">新建方案</option>{plans.map(item => <option key={item.id} value={item.id}>{item.id} · {item.goal.slice(0, 36)} · v{item.version}</option>)}</select><Badge tone="green"><ShieldCheck size={14} />原生任务与事件</Badge></div></div>
    {error && <div className="form-error"><AlertTriangle size={16} />{error}<Button variant="ghost" size="sm" onClick={() => setError('')}>关闭</Button></div>}
    <div className="generator-stepper">{phaseNames.map((name, index) => <button key={name} className={`${step === index ? 'active' : ''} ${step > index ? 'done' : ''}`} onClick={() => { if (index < step) setStep(index) }} disabled={index > step}><span>{step > index ? <Check size={15} /> : index + 1}</span><strong>{name}</strong>{index < 3 && <ChevronRight size={15} />}</button>)}</div>

    {step === 0 && <div className="generator-source-layout"><main className="surface source-workspace"><SectionHeader title="资料输入" description="文件和纯文本先暂存，确认后由原生解析器处理。" action={<Badge tone="neutral">{selected.length} / 8 份已选择</Badge>} /><div className="source-tabs">{sourceTabs.map(item => <button className={sourceTab === item ? 'active' : ''} key={item} onClick={() => setSourceTab(item)}>{item === '文件上传' ? <Upload size={16} /> : item === '纯文本' ? <FileText size={16} /> : item === 'URL 抓取' ? <Globe2 size={16} /> : <Clipboard size={16} />}{item}</button>)}</div>
      {sourceTab === '文件上传' && <div className="source-dropzone"><CloudUpload size={28} /><strong>选择本地学习资料</strong><p>PDF / DOCX / PPTX / EPUB / TXT / Markdown，单文件不超过 50 MB</p><span>文档由原生文本解析；音视频与图片仅作原件预览，不提供 OCR 或内容识别。ZIP 由 Rust 预检后逐项暂存。</span><Button onClick={() => void importFiles()} disabled={Boolean(busy)}>{busy === 'files' ? <LoaderCircle size={16} className="spin" /> : <Upload size={16} />}选择文件</Button></div>}
      {sourceTab === '纯文本' && <div className="source-text-input"><label>资料标题<input value={textTitle} maxLength={200} onChange={event => setTextTitle(clampToBytes(event.target.value, 200))} placeholder={`文本资料_${materials.length + 1}`} /></label><textarea rows={8} value={text} onChange={event => setText(clampToBytes(event.target.value, 500000))} placeholder="粘贴教材章节、课程提纲或已有笔记…" /><div><span>{utf8Bytes(text).toLocaleString()} / 500,000 字节</span><Button onClick={() => void importText()} disabled={!text.trim() || Boolean(busy)}>加入暂存</Button></div></div>}
      {sourceTab === 'URL 抓取' && <div className="source-url-input"><label>公开网页地址<div><Globe2 size={17} /><input value={url} maxLength={900} onChange={event => setUrl(clampToBytes(event.target.value, 900))} placeholder="https://example.com/chapter" /></div></label><p>仅抓取公网 HTTP/HTTPS HTML 或 UTF-8 纯文本；逐次校验重定向并限制内容大小。保存的是实际提取正文与最终来源，不执行网页脚本。</p><Button onClick={() => void importUrl()} disabled={!url.trim() || Boolean(busy)}>{busy === 'url' ? <LoaderCircle size={16} className="spin" /> : <Globe2 size={16} />}抓取并暂存</Button></div>}
      {sourceTab === '浏览器剪藏' && <div className="clipper-panel"><Clipboard size={25} /><h3>粘贴剪藏文字</h3><p>旧浏览器扩展已停用。可在用户点击后读取剪贴板文本，再按纯文本资料导入原生资料库。</p><div><Button variant="secondary" onClick={async () => { try { setText(await navigator.clipboard.readText()); setSourceTab('纯文本') } catch { notify('无法读取剪贴板，请直接粘贴文本') } }}><Clipboard size={16} />读取剪贴板</Button></div></div>}
      <div className="parse-options"><label>解析深度<select value="fast" disabled><option value="fast">fast · 原生文本解析</option></select></label><span><Hash size={15} />SHA-256 内容去重</span><Button variant="secondary" onClick={() => void parseSelected()} disabled={!selected.length || Boolean(busy)}>确认并解析所选资料</Button></div>
    </main><aside className="surface staging-panel"><SectionHeader title="暂存与解析" description="状态来自 SQLite 与原生任务事件" /><div className="source-list">{materials.map(material => <div className="source-item" key={material.id}><input type="checkbox" aria-label={`选择${material.title}`} checked={selectedIds.includes(material.id)} disabled={!material.originalAvailable || !mediaBackend.isText(material.kind)} onChange={() => toggleMaterial(material.id)} /><span className="source-file-icon">{material.status === 'parsing' || material.status === 'queued' ? <LoaderCircle size={18} className="spin" /> : <File size={18} />}</span><div><strong>{material.title}</strong><span>{material.kind.toUpperCase()} · {(material.size / 1024 / 1024).toFixed(2)} MB · {material.progress}%</span><small>sha256:{material.sha256.slice(0, 16)}…</small>{material.sourceUrl && <small>来源：{material.sourceUrl}</small>}{material.warning && <small>{material.warning}</small>}</div><Badge tone={material.status === 'parsed' ? 'green' : material.status === 'staged' ? 'amber' : 'neutral'}>{materialStatusNames[material.status] || material.status}</Badge><IconButton label={`删除${material.title}`} onClick={() => void removeMaterial(material.id)}><Trash2 size={15} /></IconButton></div>)}{!materials.length && <p>还没有资料。请选择文件或粘贴文本。</p>}</div><div className="staging-bottom"><span>{selected.filter(material => parsedStatuses.includes(material.status)).length} / {selected.length} 份已就绪</span><Button onClick={() => setStep(1)} disabled={!ready}>继续澄清目标<ArrowRight size={16} /></Button></div></aside></div>}

    {step === 1 && <div className="clarify-layout"><section className="surface clarify-form"><SectionHeader title="先确认目标，再开始生成" description="这些结构化输入会直接覆盖模型对时间、水平与里程碑的猜测。" /><div className="form-stack"><label>我想学会什么？<textarea rows={3} maxLength={1000} value={goal} onChange={event => setGoal(clampToBytes(event.target.value, 1000))} placeholder="例如：掌握贝叶斯定理，并能应用到简单分类问题" /></label><label>我的当前基础<textarea rows={3} maxLength={1000} value={level} onChange={event => setLevel(clampToBytes(event.target.value, 1000))} placeholder="例如：会计算基本概率，但没接触过条件概率" /></label><div className="form-grid"><label>每日可投入（分钟）<input type="number" min={10} max={600} value={minutes} onChange={event => setMinutes(Math.max(10, Math.min(600, Number(event.target.value) || 10)))} /></label><label>希望完成日期<input type="date" value={deadline} onChange={event => setDeadline(event.target.value)} /></label></div><label>里程碑说明（可选）<input maxLength={1000} value={milestone} onChange={event => setMilestone(clampToBytes(event.target.value, 1000))} placeholder="例如：完成一次综合测验" /></label><label>硬里程碑日期（可选）<input type="date" value={milestoneDate} onChange={event => setMilestoneDate(event.target.value)} /></label><div className="clarify-summary"><Brain size={19} /><div><strong>排期与校验规则</strong><p>目标为 3–6 个渐进阶段，每阶段建议 3–6 个节点。设置硬里程碑日期后，最后阶段必须在此前完成，否则明确标记未满足。</p></div></div><div className="generator-actions"><Button variant="secondary" onClick={() => setStep(0)}><ArrowLeft size={16} />返回资料</Button><Button onClick={() => void start()} disabled={busy === 'start' || !ready}>{busy === 'start' ? <LoaderCircle size={16} className="spin" /> : <Sparkles size={17} />}开始生成方案</Button></div></div></section><aside className="surface clarify-side"><h3>实际执行方式</h3>{[['知识图谱','真实模型生成，引用必须来自所选原文'],['阶段设计','检查依赖、难度与节点覆盖'],['资源检索','公开检索逐条实际抓取验证，失败时仅保留本地资料'],['排期校验','Rust 确定性排期，硬约束不通过会降级显示']].map(([name, desc], index) => <div key={name}><span>0{index + 1}</span><p><strong>{name}</strong>{desc}</p></div>)}</aside></div>}

    {step === 2 && <div className="generation-layout"><section className="surface generation-live">{plan ? <><div className="generation-status"><div><span className="live-indicator"><i />{running ? '原生实时事件' : planStatusNames[plan.status]}</span><h2>{running ? '正在构建你的学习方案' : planStatusNames[plan.status]}</h2><p>{lastEventId ? `事件 #${lastEventId}` : `最近保存：${plan.updatedAt}`} · 重新读取不会重复执行已完成请求</p></div><strong>{plan.progress}%</strong></div><ProgressBar value={plan.progress} /><div className="tool-run-list">{plan.steps.map(item => <div className={item.status === 'done' ? 'done' : item.status === 'running' ? 'running' : ''} key={item.index}><span>{item.status === 'done' ? <Check size={15} /> : item.status === 'running' ? <LoaderCircle size={15} className="spin" /> : <Circle size={15} />}</span><strong>{item.name}</strong><small>{statusNames[item.status] || item.status} · 第 {item.attempt} 次</small>{item.usage ? <code>{item.usage.inputTokens ?? '?'} → {item.usage.outputTokens ?? '?'} tok</code> : <code>原生步骤</code>}{item.latencyMs !== null && <small>{item.latencyMs} ms</small>}</div>)}</div><div className="generation-controls">{running ? <Button variant="secondary" onClick={() => void cancel()} disabled={plan.status === 'cancelling' || busy === 'cancel'}><Square size={16} />{plan.status === 'cancelling' ? '正在取消' : '取消生成'}</Button> : ['failed', 'cancelled', 'interrupted'].includes(plan.status) ? <Button onClick={() => void resume()} disabled={busy === 'resume'}><RefreshCw size={16} />从已保存步骤恢复</Button> : null}{plan.error && <div className="form-error"><AlertTriangle size={16} />{plan.error.code}：{plan.error.message}</div>}</div><section className="native-stream-preview"><h3>真实模型增量</h3>{liveOutput ? <><pre className="native-chat-output">{liveOutput.output || '等待模型返回答案…'}</pre>{liveOutput.reasoning && <details><summary>查看独立保存的推理内容</summary><pre className="native-chat-output">{liveOutput.reasoning}</pre></details>}</> : <p>等待当前模型步骤产生真实输出。</p>}</section></> : <div className="provider-empty"><Sparkles size={25} /><h2>尚未创建学习方案</h2><p>返回资料与目标步骤，选择资料并填写目标后开始。</p><Button onClick={() => setStep(0)}>返回资料输入</Button></div>}</section><aside className="surface generation-preview"><SectionHeader title="部分方案预览" description="只展示已经持久化的产物。" /><div className="preview-metrics"><div><strong>{graph?.nodes.length || 0}</strong><span>知识节点</span></div><div><strong>{plan?.artifacts.step3?.stages.length || result?.stages.length || 0}</strong><span>学习阶段</span></div><div><strong>{totalTokens.toLocaleString()}</strong><span>真实 Token</span></div></div>{plan?.artifacts.step3?.stages.map(stage => <div className="partial-outline" key={stage.id}><span>{stage.title}</span><p>{stage.nodeIds.map(id => graph?.nodes.find(node => node.id === id)?.title || id).join(' → ')}</p></div>)}{plan?.artifacts.step5 && <div className="cost-ledger"><Clock3 size={15} />{plan.artifacts.step5.schedule.startDate} 至 {plan.artifacts.step5.schedule.endDate} · 每日 {plan.dailyMinutes} 分钟</div>}<div className="cost-ledger"><Zap size={15} />Token 来自上游用量；不计算虚构费用，取消与失败也保留真实记录。</div></aside></div>}

    {step === 3 && result && plan && <div className="plan-result"><section className="result-banner"><div>{plan.status === 'done' ? <CheckCircle2 size={28} /> : <AlertTriangle size={28} />}<div><h2>{plan.status === 'done' ? '学习方案已生成，可以采用。' : '方案已生成，但存在需要复核的降级项。'}</h2><p>版本 v{plan.version} · {plan.adoptedProjectId ? `已采用到项目 #${plan.adoptedProjectId}` : '尚未采用'} · {plan.providerId} · 真实产物已落盘</p></div></div><Badge tone={plan.status === 'done' ? 'green' : 'amber'}>{planStatusNames[plan.status]}</Badge></section>{result.warnings.length > 0 && <div className="capacity-warning"><AlertTriangle size={18} /><div><strong>需要复核</strong>{result.warnings.map(warning => <p key={warning}>{warning}</p>)}</div></div>}<div className="result-grid"><main><section className="surface result-stages"><SectionHeader title="渐进学习路径" description={`${result.schedule.startDate} 至 ${result.schedule.endDate} · 每日 ${plan.dailyMinutes} 分钟`} action={<Button variant="secondary" size="sm" onClick={() => setRegenOpen(true)} disabled={plan.version > regenLimit || plan.adoptedProjectId !== null || running}><RefreshCw size={15} />局部重生成（剩余 {Math.max(0, regenLimit - plan.version + 1)} 次）</Button>} />{result.stages.map((stage, index) => <div className="result-stage" key={stage.id}><span className="stage-index">0{index + 1}</span><div><h3>{stage.title}</h3><p>{stage.nodeIds.map(id => result.graph.nodes.find(node => node.id === id)?.title || id).join('、')}</p><small>{stage.minutes} 分钟 · {stage.startDate} 至 {stage.endDate} · 难度 {stage.difficulty}/5</small></div><Badge tone="neutral">阶段 {index + 1}</Badge></div>)}</section><section className="surface validation-panel"><SectionHeader title="五维校验" description="结构检查由 Rust 执行；里程碑与缺口由真实模型判断，降级项不会自动变成通过。" /><div className="validation-grid">{validationNames.map(([key, name]) => { const check = result.validation[key]; return <div key={key}>{check.status === 'pass' ? <Check size={16} /> : <AlertTriangle size={16} />}<span>{name}</span><Badge tone={check.status === 'pass' ? 'green' : check.status === 'warn' ? 'amber' : 'red'}>{check.status === 'pass' ? '通过' : check.status === 'warn' ? '警告' : '未通过'}</Badge>{check.details && <small>{check.details}</small>}</div>})}</div>{result.validation.fixes.length > 0 && <div className="capacity-warning"><AlertTriangle size={18} /><div><strong>修复建议</strong>{result.validation.fixes.map(fix => <p key={fix}>{fix}</p>)}</div></div>}</section></main><aside><section className="surface result-resources"><SectionHeader title="筛选后的资源" description="本地资料与已实际抓取验证的公开网页。" />{result.resources.items.map(item => <button key={item.materialId} onClick={() => onNavigate('library')}><BookOpen size={17} /><div><strong>{item.title}</strong><span>本地资料 · 内容哈希已校验</span></div><ChevronRight size={15} /></button>)}{(result.resources.externalSearch.results ?? []).map(item => <button key={item.url} disabled={Boolean(busy)} onClick={() => void importUrl(item.url)}><Globe2 size={17} /><div><strong>{item.title}</strong><span>{item.url} · 已实际抓取验证{busy === 'url' ? ' · 正在导入' : ' · 点击导入'}</span></div><ChevronRight size={15} /></button>)}<p>{result.resources.externalSearch.reason}</p></section><section className="surface result-actions"><Button onClick={() => void adopt()} disabled={plan.adoptedProjectId !== null || busy === 'adopt'}>{plan.adoptedProjectId ? '已采用' : '采用此方案'}<ArrowRight size={16} /></Button><Button variant="secondary" onClick={() => setExportOpen(true)}>导出方案</Button><Button variant="ghost" onClick={() => setShareOpen(true)}>创建只读分享</Button></section></aside></div></div>}
    <Modal open={regenOpen} onClose={() => setRegenOpen(false)} title="局部重生成"><div className="form-stack"><p className="regen-description">保留已确认资料与目标，旧版本会保留。仅重跑所选步骤及其下游；已采用的方案不能直接改写。</p><label>重生成范围<select value={regenScope} onChange={event => setRegenScope(Number(event.target.value))}>{Object.entries(regenerationNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label><div className="modal-callout"><Route size={17} />「{regenerationNames[regenScope]}」及其下游将使用新的真实模型请求与排期。</div><Button onClick={() => void regenerate()} disabled={busy === 'regenerate'}><RefreshCw size={16} />确认局部重生成</Button></div></Modal>
    <ExportCenter open={exportOpen} onClose={() => setExportOpen(false)} notify={notify} initialPlanId={plan?.id} />
    <SharePlanDialog open={shareOpen} onClose={() => setShareOpen(false)} notify={notify} initialPlanId={plan?.id} onPreview={token => { window.history.replaceState(null, '', localShareUrl(token)); setShareOpen(false); onNavigate('shared') }} />
  </div>
}

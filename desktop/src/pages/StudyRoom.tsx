import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, BookMarked, BookOpen, Check, ChevronDown, ChevronRight, FileText, Highlighter, ListTree, LoaderCircle, Maximize2, Minus, NotebookPen, Plus, Quote, Sparkles, Video } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import type { PageId } from '../data'
import { Badge, Button, ConfirmDialog, IconButton, MarkdownLink, Modal, ProgressBar } from '../components/UI'
import { backend, backendErrorMessage, onBackendEvent, reportBackendError, isCancelRace, deltaThrottle } from '../backend/client'
import type { Lecture, LearningPlan, Material, ProviderRequest, ReadingProgress } from '../backend/client'
import { mediaBackend } from '../backend/media'
import MediaPlayer from '../components/MediaPlayer'
import { clampToBytes } from '../limits'

const lectureStatus: Record<string, string> = { empty: '尚未生成', running: '正在生成', cancelling: '正在取消', done: '已生成', failed: '生成失败', cancelled: '已取消', interrupted: '已中断' }

// 80#2：讲义区块 memo 化——直播 tick 引发的整页重渲染不再对全文
// Markdown/KaTeX 逐次重解析（内容引用不变即跳过）
const MemoMarkdown = memo(function MemoMarkdown({ text }: { text: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]} components={{ a: MarkdownLink }}>{text}</ReactMarkdown>
})

export default function StudyRoom({ target, onFocus, onNavigate, onTutorContext, notify }: { target?: { planId: string; stageId: string; nonce: number } | null; onFocus: () => void; onNavigate: (page: PageId) => void; onTutorContext: (context: { planId: string; label: string } | null) => void; notify: (message: string) => void }) {
  const [plans, setPlans] = useState<LearningPlan[]>([])
  const [plan, setPlan] = useState<LearningPlan | null>(null)
  const [stageId, setStageId] = useState('')
  const [progress, setProgress] = useState<Record<string, ReadingProgress>>({})
  const [lecture, setLecture] = useState<Lecture | null>(null)
  const [unitIndex, setUnitIndex] = useState(0)
  const [tab, setTab] = useState('阅读')
  const [fontScale, setFontScale] = useState(1)
  const [courseOpen, setCourseOpen] = useState(true)
  const [noteOpen, setNoteOpen] = useState(false)
  const [note, setNote] = useState('')
  // 82#3：批注打开时的基线——关闭时正文偏离基线且非空则先确认，
  // 原先 X/Esc/遮罩直接关掉，长批注一次误点即丢（与 Library 笔记编辑器
  // 的草稿+确认双保护不一致，正是 28#2 在练习侧修过的同型问题）
  const [noteBaseline, setNoteBaseline] = useState('')
  const [noteDiscardOpen, setNoteDiscardOpen] = useState(false)
  const openNoteModal = (initial?: string) => {
    if (initial !== undefined) setNote(initial)
    setNoteBaseline(initial !== undefined ? initial : note)
    setNoteOpen(true)
  }
  const closeNoteModal = () => {
    if (note.trim() && note !== noteBaseline) setNoteDiscardOpen(true)
    else setNoteOpen(false)
  }
  const [lectureOpen, setLectureOpen] = useState(false)
  const [liveRequest, setLiveRequest] = useState<ProviderRequest | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [mediaMaterials, setMediaMaterials] = useState<Material[]>([])
  const [mediaId, setMediaId] = useState<number | null>(null)
  const stageRef = useRef({ planId: '', stageId: '' })
  const consumedTarget = useRef('')
  const routeUpstreams = useRef<Record<string, string>>({})
  // 35#9：useLayoutEffect 在提交后同步刷新——passive effect 有微任务窗口，
  // 在途响应的 promise 微任务可先于 effect 冲刷漏过守卫
  useLayoutEffect(() => { stageRef.current = { planId: plan?.id || '', stageId } }, [plan, stageId])
  useEffect(() => { routeUpstreams.current = {} }, [plan?.id, stageId])
  // 76#6：监听只挂载一次——原先随 tab 重挂，注销与新 listen() 生效之间的
  // IPC 窗口内 material.* 事件被丢弃（51#9 同型）；当前是否视频页经 ref 读取
  const mediaTabRef = useRef(false)
  useEffect(() => { mediaTabRef.current = tab === '视频' })
  const loadMedia = useCallback(async () => {
    if (!mediaTabRef.current) return
    try {
      const items = (await backend.listMaterials()).filter(item => item.originalAvailable && mediaBackend.isMedia(item.kind))
      setMediaMaterials(items)
      setMediaId(current => items.some(item => item.id === current) ? current : items[0]?.id ?? null)
    } catch (cause) { setError(backendErrorMessage(cause)) }
  }, [])
  useEffect(() => {
    let active = true
    let stop: (() => void) | undefined
    void onBackendEvent(event => { if (active && event.kind.startsWith('material.')) void loadMedia() }).then(unlisten => { if (active) stop = unlisten; else unlisten() }).catch(reportBackendError)
    return () => { active = false; stop?.() }
  }, [loadMedia])
  useEffect(() => { if (tab === '视频') void loadMedia() }, [tab, loadMedia])
  const selectedMedia = mediaMaterials.find(item => item.id === mediaId) ?? mediaMaterials[0]

  const refreshProgress = useCallback(async () => {
    if (!plan?.result) { setProgress({}); return }
    // 32#6：捕获请求时的方案 id——快速切换课程时旧方案的并行进度响应
    // 后到会整体替换 progress，新方案进度全丢
    const requestedPlanId = plan.id
    try {
      const entries = await Promise.all(plan.result.stages.map(async stage => [stage.id, await backend.getReadingProgress(plan.id, stage.id)] as const))
      if (stageRef.current.planId !== requestedPlanId) return
      setProgress(Object.fromEntries(entries))
    } catch (cause) { setError(backendErrorMessage(cause)) }
  }, [plan])
  // 正在阅读的讲义版本：事件刷新时保持同一版本，生成新版本期间不被拉回
  const readingVersionRef = useRef<number | undefined>(undefined)
  const refreshLecture = useCallback(async (version?: number) => {
    if (version !== undefined) setLiveRequest(null)
    if (!plan || !stageId) { setLecture(null); return }
    const requested = { planId: plan.id, stageId }
    try {
      const next = await backend.getLecture(plan.id, stageId, version)
      // 32#4：响应返回时用户可能已切到其他阶段/课程——过期讲义不得覆盖
      if (stageRef.current.planId !== requested.planId || stageRef.current.stageId !== requested.stageId) return
      readingVersionRef.current = version ?? next.version
      setLecture(next)
    } catch (cause) { setError(backendErrorMessage(cause)) }
  }, [plan, stageId])
  // 76#8：序号守卫（51#2 家族补齐）
  const plansSeq = useRef(0)
  const refreshPlans = useCallback(async () => {
    const ticket = ++plansSeq.current
    try {
      const items = (await backend.listLearningPlans()).filter(item => item.adoptedProjectId !== null)
      if (ticket !== plansSeq.current) return
      setPlans(items)
      setPlan(current => {
        if (target) return items.find(item => item.id === target.planId) || current || items[0] || null
        return current ? items.find(item => item.id === current.id) || items[0] || null : items[0] || null
      })
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setLoading(false) }
  }, [target])
  useEffect(() => { void refreshPlans() }, [refreshPlans])
  useEffect(() => { void refreshProgress() }, [refreshProgress])
  // 28#6：nonce 让同阶段的重复导航（⌘K 再点同一结果）也能触发跳转——
  // consumedTarget 只增不减会静默吞掉重复目标
  const targetKey = target ? `${target.planId}:${target.stageId}:${target.nonce}` : ''
  useEffect(() => {
    const stages = plan?.result?.stages || []
    if (target && targetKey !== consumedTarget.current && target.planId === plan?.id && stages.some(stage => stage.id === target.stageId)) {
      consumedTarget.current = targetKey
      setStageId(target.stageId)
      return
    }
    setStageId(current => stages.some(stage => stage.id === current)
      ? current
      : stages.find(stage => !progress[stage.id]?.completed)?.id || stages[0]?.id || '')
  }, [plan, target, targetKey, progress])
  useEffect(() => {
    setUnitIndex(0)
    setLiveRequest(null)
    void refreshLecture()
  }, [refreshLecture])
  useEffect(() => {
    if (lecture?.units.length) setUnitIndex(current => Math.min(current, lecture.units.length - 1))
  }, [lecture?.units.length])

  // 35#8：监听器依赖去掉 lecture?.units——原先每条 lecture.* 事件刷新都产生
  // 新数组身份，监听器反复重挂，注销与 listen() 解析之间存在丢事件窗口
  const lectureUnitsRef = useRef(lecture?.units)
  useEffect(() => { lectureUnitsRef.current = lecture?.units }, [lecture?.units])
  // 51#9：refreshLecture/refreshProgress 随方案/阶段变化 identity——同样导致
  // 监听器反复重挂丢事件；回调经 ref 取最新，监听只挂载一次
  const refreshLectureRef = useRef(refreshLecture)
  const refreshProgressRef = useRef(refreshProgress)
  useEffect(() => { refreshLectureRef.current = refreshLecture; refreshProgressRef.current = refreshProgress })
  useEffect(() => {
    let disposed = false
    let unlisten: (() => void) | undefined
    const throttledFetch = deltaThrottle()
    void onBackendEvent(event => {
      if (disposed) return
      if (event.kind.startsWith('lecture.') && event.payload.planId === stageRef.current.planId && event.payload.stageId === stageRef.current.stageId) void refreshLectureRef.current(readingVersionRef.current)
      if (event.kind === 'study.progress_changed' && event.payload.planId === stageRef.current.planId) void refreshProgressRef.current()
      if (event.kind === 'agent.artifact_promoted' && event.payload.planId === stageRef.current.planId && event.payload.stageId === stageRef.current.stageId) void refreshLectureRef.current()
      // 51#5：查表改用 ref——闭包里的 lecture 是挂载时快照，failover 发生在
      // 状态更新后会让匹配失败、映射漏登，直播预览停在 failover 前最后一帧
      if (event.kind === 'provider.route_attempt' && typeof event.payload.requestId === 'string' && typeof event.payload.upstreamRequestId === 'string' && lectureUnitsRef.current?.some(unit => unit.status === 'running' && unit.requestId === event.payload.requestId)) {
        // 15#6：delta 携带的是上游 id（-f1/-f2），映射必须以上游 id 为 key、
        // 逻辑 id 为 value，方向写反会导致 failover 后查表必 miss、直播冻结
        routeUpstreams.current[event.payload.upstreamRequestId] = event.payload.requestId
      }
      if (event.kind === 'provider.chat_delta' && typeof event.payload.requestId === 'string') {
        const requestId = event.payload.requestId
        const logicalId = routeUpstreams.current[requestId] ?? requestId
        if (lectureUnitsRef.current?.some(unit => unit.status === 'running' && unit.requestId === logicalId)) {
          // 多条 delta 合并为一次全量拉取，长回答期间不再逐条重放
          throttledFetch(() => void backend.getProviderChat(logicalId).then(setLiveRequest).catch(cause => setError(backendErrorMessage(cause))))
        }
      }
    }).then(remove => { if (disposed) remove(); else unlisten = remove })
    return () => { disposed = true; unlisten?.() }
      // 76#7：卸载取消武装中的定时器
      throttledFetch.cancel()
  }, [])

  const stage = plan?.result?.stages.find(item => item.id === stageId)
  const nodes = useMemo(() => stage?.nodeIds.map(id => plan?.result?.graph.nodes.find(node => node.id === id)).filter(node => node !== undefined) || [], [stage, plan])
  const currentUnit = lecture?.units[unitIndex]
  const currentNode = nodes.find(node => node.id === currentUnit?.nodeId) || nodes[unitIndex] || nodes[0]
  const content = currentUnit?.content
  const completed = plan?.result?.stages.filter(item => progress[item.id]?.completed).length || 0
  const totalStages = plan?.result?.stages.length || 0
  const completion = totalStages ? Math.round(completed / totalStages * 100) : 0
  const running = lecture?.status === 'running' || lecture?.status === 'cancelling'
  const currentReading = progress[stageId] || { completed: false, bookmarked: false }
  useEffect(() => {
    onTutorContext(plan && stage ? { planId: plan.id, label: `${plan.goal} · ${stage.title}${currentNode ? ` · ${currentNode.title}` : ''}` } : null)
  }, [onTutorContext, plan, stage, currentNode])

  const saveReading = async (done: boolean, bookmarked?: boolean): Promise<boolean> => {
    if (!plan || !stageId) return false
    setBusy('reading')
    try {
      const next = await backend.setReadingProgress(plan.id, stageId, done, bookmarked)
      setProgress(current => ({ ...current, [stageId]: next }))
      return true
    } catch (cause) { setError(backendErrorMessage(cause)); return false } finally { setBusy('') }
  }
  const completeAndContinue = async () => {
    if (!plan?.result || !(await saveReading(true))) return
    const index = plan.result.stages.findIndex(item => item.id === stageId)
    const next = plan.result.stages[index + 1]
    if (next) setStageId(next.id)
    notify('本阶段阅读完成状态已保存')
  }
  const generate = async () => {
    if (!plan || !stageId) return
    setBusy('generate')
    // 28#1：生成/恢复前把版本 ref 重置为"跟当前"——首次生成（空讲义返回
    // version=0）与恢复生成（versions 表无行）若沿用旧值，每条 lecture.*
    // 事件都会以不存在的版本号请求并报 not_found，进度永不更新、弹窗被
    // running 挡住形成死锁
    readingVersionRef.current = undefined
    const requested = { planId: plan.id, stageId }
    try {
      const next = await backend.generateLecture(plan.id, stageId, lecture?.status === 'done')
      // 35#4：生成耗时数十秒，响应返回时用户可能已切阶段/课程——过期讲义
      // 与版本号不得写入（与 refreshLecture 的 32#4 守卫同型）
      if (stageRef.current.planId !== requested.planId || stageRef.current.stageId !== requested.stageId) return
      readingVersionRef.current = next.version
      setLecture(next)
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const cancel = async () => {
    if (!plan || !stageId) return
    setBusy('cancel')
    const requestedCancel = { planId: plan.id, stageId }
    try {
      const next = await backend.cancelLecture(plan.id, stageId)
      // 35#4：同 generate——取消响应也按当前 stage 复查
      if (stageRef.current.planId !== requestedCancel.planId || stageRef.current.stageId !== requestedCancel.stageId) return
      setLecture(next)
    } catch (cause) {
      if (!isCancelRace(cause)) setError(backendErrorMessage(cause))
      // 28#1：取消命中竞态码（任务已终态）时重读当前状态，避免弹窗停在
      // running 卡死（flag 已随线程结束移除，取消必然 no_active_lecture）
      else void refreshLecture()
    } finally { setBusy('') }
  }
  const saveNote = async () => {
    if (!plan || !stageId || !note.trim()) return
    setBusy('note')
    try {
      await backend.saveStudyNote(plan.id, stageId, note.trim())
      setNote('')
      setNoteOpen(false)
      notify('批注已保存到笔记库，并关联当前阶段')
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  // 43#2：导出写盘期间复用 busy 通道，行按钮禁用防连点
  const exportMaterial = async (materialId: number, title: string) => {
    if (busy) return
    setBusy('export')
    try {
      const detail = await backend.getMaterial(materialId)
      const destination = await backend.saveMaterialFile(title, detail.kind)
      if (!destination) return
      await backend.exportMaterialOriginal(materialId, destination)
      notify('原件已由原生后端导出')
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }

  if (loading && !error) return <div className="page-loading" role="status"><LoaderCircle size={22} className="spin" />正在读取已采用的学习方案…</div>
  if (error && !plans.length) return <div className="page study-page"><div className="provider-empty" role="alert"><h2>学习方案读取失败</h2><p>{error}</p><Button onClick={() => { setError(''); setLoading(true); void refreshPlans() }}>重新读取</Button></div></div>
  if (!plan?.result || !stage) return <div className="page study-page"><div className="provider-empty"><BookOpen size={26} /><h2>还没有可学习的课程</h2><p>请先通过真实资料生成并采用学习方案，阶段和讲义会出现在这里。</p><Button onClick={() => onNavigate('generator')}>前往方案生成<ArrowRight size={16} /></Button></div></div>

  return <div className="study-page">
    <div className="study-topbar"><div><button className="back-course" onClick={() => onNavigate('plan')}><ArrowLeft size={17} />{plan.goal}</button><span>/</span><strong>{stage.title}</strong></div><div className="study-progress"><span>课程进度 {completion}%</span><ProgressBar value={completion} /></div><div className="study-actions"><IconButton label={currentReading.bookmarked ? '取消收藏' : '收藏本阶段'} className={currentReading.bookmarked ? 'is-active' : ''} onClick={() => void saveReading(currentReading.completed, !currentReading.bookmarked)} disabled={busy === 'reading'}><BookMarked size={18} fill={currentReading.bookmarked ? 'currentColor' : 'none'} /></IconButton><Button variant="secondary" size="sm" onClick={onFocus}><Maximize2 size={16} />专注模式</Button></div></div>
    {error && <div className="form-error" role="alert">{error}<Button variant="ghost" size="sm" onClick={() => setError('')}>关闭</Button></div>}
    <div className="study-layout">
      <aside className={`course-rail ${courseOpen ? '' : 'is-collapsed'}`}>
        <button className="course-rail-title" aria-expanded={courseOpen} onClick={() => setCourseOpen(!courseOpen)}><span><ListTree size={17} />课程目录</span><ChevronDown size={16} /></button>
        {courseOpen && <><div className="course-chapter"><span>{plans.length} 个已采用方案</span><strong>{plan.goal}</strong><select aria-label="切换课程" value={plan.id} onChange={event => setPlan(plans.find(item => item.id === event.target.value) || null)}>{plans.map(item => <option key={item.id} value={item.id}>{item.goal}</option>)}</select></div><div className="lesson-list">{plan.result.stages.map((item, index) => <button key={item.id} className={stageId === item.id ? 'active' : ''} aria-current={stageId === item.id ? 'true' : undefined} onClick={() => { setStageId(item.id); setTab('阅读') }}><span className="lesson-state">{progress[item.id]?.completed ? <Check size={13} /> : index + 1}</span><div><strong>{item.title}</strong><small>{item.minutes} 分钟 · 难度 {item.difficulty}/5</small></div>{stageId === item.id && <ChevronRight size={15} />}</button>)}</div><div className="course-rail-bottom"><span>已完成阶段</span><strong>{completed} / {totalStages}</strong><ProgressBar value={completion} /><small>完成阅读不等于掌握度；掌握度只由真实测评记录计算。</small></div></>}
      </aside>

      <main className="reading-pane">
        <div className="reading-toolbar"><div className="content-tabs">{['阅读', '资料'].map(item => <button className={tab === item ? 'active' : ''} aria-pressed={tab === item} key={item} onClick={() => setTab(item)}>{item === '阅读' ? <BookOpen size={16} /> : <FileText size={16} />}{item}</button>)}<button className={tab === '视频' ? 'active' : ''} aria-pressed={tab === '视频'} onClick={() => setTab('视频')}><Video size={16} />视频</button></div><div className="reading-tools"><IconButton label="减小字号" onClick={() => setFontScale(Math.max(.9, fontScale - .1))}><Minus size={16} /></IconButton><span>{Math.round(fontScale * 100)}%</span><IconButton label="增大字号" onClick={() => setFontScale(Math.min(1.25, fontScale + .1))}><Plus size={16} /></IconButton><IconButton label="生成自学讲义" onClick={() => setLectureOpen(true)}><Sparkles size={17} /></IconButton><IconButton label="记录学习笔记" onClick={() => openNoteModal()}><NotebookPen size={17} /></IconButton></div></div>
        {tab === '阅读' && <article className="reading-article" style={{ fontSize: `${fontScale}rem` }}>
          <div className="article-kicker"><Badge tone="green">{stage.title}</Badge><span>难度 {stage.difficulty}/5 · 单元 {Math.min(unitIndex + 1, nodes.length)} / {nodes.length}</span></div>
          {content ? <>
            <h1>{content.title}</h1><div className="article-lead"><MemoMarkdown text={content.overview} /></div>
            {content.sections.map((section, index) => <section key={`${section.heading}-${index}`}><h2>{section.heading}</h2><MemoMarkdown text={section.content} /></section>)}
            {content.examples.map((example, index) => <div className="worked-example" key={`${example.title}-${index}`}><div className="example-head"><span>例题</span><strong>{example.title}</strong></div><MemoMarkdown text={example.content} /></div>)}
            <div className="article-check"><Check size={20} /><h3>主动检查</h3></div>{content.checkpoints.map((checkpoint, index) => <details className="lecture-checkpoint" key={`${checkpoint.question}-${index}`}><summary>{checkpoint.question}</summary><MemoMarkdown text={checkpoint.answer} /></details>)}
            <div className="lecture-citations"><h3>原文引用</h3>{content.citations.map((citation, index) => <button key={`${citation.materialId}-${index}`} onClick={() => { openNoteModal(citation.quote) }}><Quote size={16} /><span>「{citation.quote}」</span><small>资料 #{citation.materialId} · 引用到批注</small></button>)}</div>
          </> : <>
            <h1>{currentNode?.title || stage.title}</h1><p className="article-lead">尚未生成这一单元的讲义。以下只展示已保存的节点摘要与原文引用，不用模板正文替代。</p>
            <div className="article-summary"><span>节点摘要</span><p>{currentNode?.summary}</p></div>
            {currentNode?.citations.map((citation, index) => <div className="term-grid" key={`${citation.materialId}-${index}`}><div><span>资料 #{citation.materialId}</span><p>「{citation.quote}」</p><button onClick={() => { openNoteModal(citation.quote) }}><Highlighter size={16} />引用到批注</button></div></div>)}
            {lecture?.error && <div className="form-error">{lecture.error.code}：{lecture.error.message}</div>}
          </>}
          <div className="article-footer"><Button variant="secondary" disabled={unitIndex === 0} onClick={() => setUnitIndex(Math.max(0, unitIndex - 1))}><ArrowLeft size={16} />上一单元</Button><span>{lecture ? `讲义 v${lecture.version} · ${lectureStatus[lecture.status]}` : '尚未读取讲义'}</span><Button disabled={unitIndex >= nodes.length - 1} onClick={() => setUnitIndex(Math.min(nodes.length - 1, unitIndex + 1))}>下一单元<ArrowRight size={16} /></Button></div>
          <div className="article-footer"><Button variant="secondary" disabled={plan.result.stages[0]?.id === stageId} onClick={() => { const index = plan.result!.stages.findIndex(item => item.id === stageId); setStageId(plan.result!.stages[Math.max(0, index - 1)].id) }}><ArrowLeft size={16} />上一阶段</Button>{/* 48#3：状态归 Badge、按钮恒为动作语义——原先完成后文案变「已完成阅读」但 onClick 仍是重新保存并跳下一阶段，状态文案放在动作钮上双重误导 */}{progress[stageId]?.completed && <Badge tone="green"><Check size={13} />本阶段已完成</Badge>}<Button onClick={() => void completeAndContinue()} disabled={busy === 'reading'}>完成并继续<ArrowRight size={16} /></Button></div>
        </article>}
        {tab === '资料' && <div className="chapter-resources"><h1>本方案关联资料</h1>{plan.result.resources.items.map(item => <button key={item.materialId} disabled={busy === 'export'} onClick={() => void exportMaterial(item.materialId, item.title)}><FileText size={21} /><div><strong>{item.title}</strong><span>资料 #{item.materialId} · SHA-256 {item.sha256.slice(0, 16)}… · {busy === 'export' ? '正在等待选择保存位置…' : '点击导出原件'}</span></div><ArrowRight size={17} /></button>)}</div>}
        {tab === '视频' && <div className="chapter-resources"><h1>本地音视频原件</h1><p>只显示当前用户实际导入的媒体，不展示演示视频或虚构课程关联。</p>{selectedMedia ? <><label>选择媒体原件<select value={selectedMedia.id} onChange={event => setMediaId(Number(event.target.value))}>{mediaMaterials.map(item => <option key={item.id} value={item.id}>{item.title} · {item.kind}</option>)}</select></label><MediaPlayer material={selectedMedia} /></> : <div className="provider-empty"><Video size={26} /><h2>尚未导入音视频</h2><p>请在资料库导入真实 MP4、WebM、MP3、WAV 等原件，再在这里预览。</p><Button onClick={() => onNavigate('library')}>前往资料库</Button></div>}</div>}
      </main>
      <aside className="study-tutor"><section className="surface"><h3>当前知识节点</h3><h2>{currentNode?.title}</h2><p>{currentNode?.summary}</p><Badge tone="neutral">难度 {currentNode?.difficulty}/5</Badge><div className="node-relations"><h3>阶段信息</h3><div><span>预计时长</span><strong>{currentNode?.estimatedMinutes} 分钟</strong></div><div><span>阶段日期</span><strong>{stage.startDate} 至 {stage.endDate}</strong></div><div><span>阅读状态</span><strong>{currentReading.completed ? '已完成' : '未完成'}</strong></div></div><p>阅读状态与测评掌握度分别记录，不会将阅读完成自动视为已经掌握。</p></section></aside>
    </div>
    <Modal open={noteOpen} onClose={closeNoteModal} title="记录学习批注"><div className="form-stack"><div className="note-context"><BookOpen size={16} />{stage.title} · {currentNode?.title}</div><label>批注内容<textarea autoFocus rows={7} maxLength={100000} value={note} onChange={event => setNote(clampToBytes(event.target.value, 100000))} placeholder="记录你的理解、疑问或一个自己的例子…" /></label><div className="note-quick"><span>快速插入：</span><button onClick={() => setNote(note + '\n我的理解：')}>我的理解</button><button onClick={() => setNote(note + '\n仍有疑问：')}>仍有疑问</button><button onClick={() => setNote(note + '\n自己的例子：')}>自己的例子</button></div><Button onClick={() => void saveNote()} disabled={!note.trim() || busy === 'note'}>保存到笔记库</Button></div></Modal>
    {/* 82#3：批注丢弃确认——保存按钮之外的关闭路径全部经此守卫 */}
    <ConfirmDialog open={noteDiscardOpen} onClose={() => setNoteDiscardOpen(false)} onConfirm={() => { setNoteDiscardOpen(false); setNoteOpen(false) }} title="放弃未保存的批注？" description="批注内容尚未保存到笔记库，关闭后将丢失；引用原文会随下次打开重新注入。" confirmLabel="放弃并关闭" />
    <Modal open={lectureOpen} onClose={() => { if (!running) setLectureOpen(false); else notify('讲义生成进行中，弹窗保持打开；点击「取消生成」可停止。') }} title="生成自学讲义"><div className="form-stack"><div className="lecture-scope"><BookOpen size={18} /><div><strong>{stage.title} · 难度 {stage.difficulty}/5</strong><p>按实际 {nodes.length} 个知识节点逐单元生成，引用仅接受资料白名单中的原文。</p></div></div><div className="lecture-options">{nodes.map(node => <span key={node.id}>{node.title}</span>)}</div>{lecture && <><div className="lecture-progress" aria-live="polite"><LoaderCircle size={18} className={running ? 'spin' : ''} /><div><strong>{lectureStatus[lecture.status]} · v{lecture.version}</strong><p>{lecture.completedUnits} / {lecture.totalUnits} 个单元已保存{lecture.previousVersion ? ` · 可查看旧版本 v${lecture.previousVersion}` : ''}</p><ProgressBar value={lecture.totalUnits ? Math.round(lecture.completedUnits / lecture.totalUnits * 100) : 0} /></div></div>{liveRequest && <pre className="native-chat-output">{liveRequest.output || '等待真实模型输出…'}</pre>}{lecture.error && <div className="form-error">{lecture.error.code}：{lecture.error.message}</div>}{lecture.previousVersion && <Button variant="ghost" size="sm" onClick={() => void refreshLecture(lecture.previousVersion!)}>读取旧版本 v{lecture.previousVersion}</Button>}{lecture.status === 'done' && lecture.version > 1 && <Button variant="ghost" size="sm" onClick={() => void refreshLecture()}>返回当前版本</Button>}</>}<div className="generator-actions"><Button variant="secondary" disabled={running && (lecture?.status === 'cancelling' || busy === 'cancel')} onClick={() => running ? void cancel() : setLectureOpen(false)}>{lecture?.status === 'cancelling' ? '正在取消…' : running ? '取消生成' : '关闭'}</Button><Button onClick={() => void generate()} disabled={busy === 'generate' || running}>{busy === 'generate' ? <LoaderCircle size={16} className="spin" /> : <Sparkles size={16} />}{lecture?.status === 'done' ? '生成新版本' : lecture?.status === 'empty' || !lecture ? '开始生成' : '恢复生成'}</Button></div></div></Modal>
  </div>
}

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowRight, BookOpen, Check, CircleDashed, GitBranch, List, LoaderCircle, Map as MapIcon, Play, Route, Target } from '../components/icons'
import type { PageId } from '../data'
import { Badge, Button, ProgressBar, SectionHeader } from '../components/UI'
import { backend, backendErrorMessage, onBackendEvent, reportBackendError } from '../backend/client'
import type { KnowledgeOverview, LearningGraph, LearningPlan } from '../backend/client'

type Position = { x: number; y: number; depth: number }

// P-037：同层节点行距——节点标题改为独占整行且最多 3 行后，节点高度上限
// 约 87px（12px 字号 ×1.35 行高 ×3 行 + 图标行 + 内边距），行距取 96px
// 留出间隙；原先 76px 行距按 66px 节点高假设排布，长标题节点互相叠压
const LEVEL_ROW_GAP = 96

// 150#3：取后端错误码——backendErrorMessage 只给「码：文案」的拼接串，
// 横幅需要按码区分「换参数才有意义」与「重试即可」两类失败
function errorCodeOf(cause: unknown) {
  if (typeof cause === 'object' && cause !== null && 'code' in cause) return String((cause as { code?: unknown }).code || '')
  return ''
}

function graphLayout(graph: LearningGraph) {
  const depths = new Map<string, number>()
  const depth = (id: string, visiting = new Set<string>()): number => {
    const existing = depths.get(id)
    if (existing !== undefined) return existing
    if (visiting.has(id)) return 0
    visiting.add(id)
    const parents = graph.edges.filter(edge => edge.to === id)
    const value = parents.length ? Math.max(...parents.map(edge => depth(edge.from, new Set(visiting)))) + 1 : 0
    depths.set(id, value)
    return value
  }
  graph.nodes.forEach(node => depth(node.id))
  const groups = new Map<number, string[]>()
  graph.nodes.forEach(node => {
    const level = depths.get(node.id) || 0
    groups.set(level, [...(groups.get(level) || []), node.id])
  })
  const maximum = Math.max(...groups.keys(), 0)
  const positions = new Map<string, Position>()
  // 147#15：画布按节点最小间距动态扩展——固定 760×390 下深图（层数≥5）
  // 列距 <112px 节点宽、同层多节点行距 <66px 节点高，节点互相重叠并叠到
  // 顶边；按 128px/列、LEVEL_ROW_GAP/行的下限扩画布，稀疏图保持原 760×390 紧凑
  // P-037：行距改用 LEVEL_ROW_GAP（96px）——节点实际高度可达约 87px，
  // 76px 行距会让同层长标题节点互相叠压
  const perLevel = Math.max(1, ...Array.from(groups.values(), ids => ids.length))
  const width = Math.max(760, (maximum + 2) * 128)
  const height = Math.max(390, (perLevel + 1) * LEVEL_ROW_GAP)
  for (const [level, ids] of groups) {
    ids.forEach((id, index) => positions.set(id, {
      x: (level + 1) * width / (maximum + 2),
      y: (index + 1) * height / (ids.length + 1),
      depth: level,
    }))
  }
  return { positions, width, height }
}

export default function KnowledgeMap({ onNavigate, notify, onStudy, onPractice, initialPlanId, focusTarget, onFocusConsumed }: { onNavigate: (page: PageId) => void; notify: (message: string) => void; onStudy?: (planId: string, stageId: string) => void; onPractice?: (planId: string, stageId: string, nodeId: string) => void; initialPlanId?: string | null; focusTarget?: { planId: string; nodeId: string; nonce: number } | null; onFocusConsumed?: () => void }) {
  const [plans, setPlans] = useState<LearningPlan[]>([])
  const [plan, setPlan] = useState<LearningPlan | null>(null)
  // P-136：方案列表首次拉取完成前不渲染「尚未采用学习方案」假空态——
  // 挂载时 plans 为空、plan 为 null，早退分支会把加载中误报为没有方案
  const [plansLoaded, setPlansLoaded] = useState(false)
  const [overview, setOverview] = useState<KnowledgeOverview | null>(null)
  const [selected, setSelected] = useState('')
  const [target, setTarget] = useState('')
  const [path, setPath] = useState<string[] | null>(null)
  const [view, setView] = useState('图谱')
  const [error, setError] = useState('')
  // 121#6：错误来源标记——重试按钮按源分流，原先一律 refreshOverview，
  // 路径计算/加任务失败点重试只清了横幅、目标动作未被重试
  const [errorSource, setErrorSource] = useState<'overview' | 'plans' | 'path' | 'task'>('overview')
  // 150#3：错误码——path_not_found 需与「换个起终点就能重试」的错误区分
  const [errorCode, setErrorCode] = useState('')
  const [busy, setBusy] = useState(false)
  const planRef = useRef<LearningPlan | null>(null)
  useEffect(() => { planRef.current = plan }, [plan])

  // 156#4：补整体序号守卫（144#4 两侧口径）——原先 catch 无守卫，A 的
  // 失败晚到会在 B 成功清错后重设错误横幅
  const overviewSeq = useRef(0)
  const refreshOverview = useCallback(async () => {
    if (!plan) { setOverview(null); return }
    // 32#7：方案 A→B 快速切换时，A 的响应后到不得覆盖 B 的掌握度概览
    const requestedPlanId = plan.id
    const ticket = ++overviewSeq.current
    try {
      const next = await backend.getKnowledgeOverview(plan.id)
      if (planRef.current?.id !== requestedPlanId || ticket !== overviewSeq.current) return
      setOverview(next)
    } catch (cause) { if (ticket === overviewSeq.current) { setError(backendErrorMessage(cause)); setErrorCode(errorCodeOf(cause)); setErrorSource('overview') } }
  }, [plan])
  // 76#8：序号守卫——他处采用方案触发 plan.adopted 与本页刷新并发时旧列表后到不回写
  const plansSeq = useRef(0)
  const refreshPlans = useCallback(async () => {
    const ticket = ++plansSeq.current
    try {
      const items = (await backend.listLearningPlans()).filter(item => item.adoptedProjectId !== null)
      if (ticket !== plansSeq.current) return
      setPlans(items)
      const preferred = initialPlanId
      setPlan(current => preferred ? items.find(item => item.id === preferred) || items[0] || null : current ? items.find(item => item.id === current.id) || items[0] || null : items[0] || null)
    } catch (cause) { if (ticket === plansSeq.current) { setError(backendErrorMessage(cause)); setErrorCode(errorCodeOf(cause)); setErrorSource('plans') } } /* 156#4：catch 补同 ticket 复查 */
    // P-136：首次拉取结束（成功或失败）即退出加载态——失败走错误态，
    // 成功且零方案才走「尚未采用学习方案」空态
    finally { if (ticket === plansSeq.current) setPlansLoaded(true) }
  }, [initialPlanId])
  useEffect(() => { void refreshPlans() }, [refreshPlans])
  useEffect(() => { void refreshOverview() }, [refreshOverview])
  // 51#9：事件监听只挂载一次——原先依赖 refreshOverview/refreshPlans（随方案
  // 切换变化 identity），重挂间隙到达的证据事件被丢弃；回调经 ref 取最新
  const refreshOverviewRef = useRef(refreshOverview)
  const refreshPlansRef = useRef(refreshPlans)
  useEffect(() => { refreshOverviewRef.current = refreshOverview; refreshPlansRef.current = refreshPlans })
  useEffect(() => {
    let disposed = false
    let unlisten: (() => void) | undefined
    void onBackendEvent(event => {
      if (disposed) return
      if (event.kind === 'plan.adopted') void refreshPlansRef.current()
      // 练习/复习证据落库后掌握度随之变化，停留页面也要刷新（事件载荷不带 planId，直接重拉概览）
      if (event.kind === 'study.progress_changed' && event.payload.planId === planRef.current?.id) void refreshOverviewRef.current()
      else if (['practice.answer_graded', 'practice.answer_submitted', 'practice.set_completed', 'practice.exam_completed', 'practice.mistake_mastered_by_variant', 'review.graded'].includes(event.kind)) void refreshOverviewRef.current()
    // 136#2：订阅链补 .catch——与全仓其余订阅同口径，不留 unhandled rejection
    }).then(remove => { if (disposed) remove(); else unlisten = remove }).catch(reportBackendError)
    return () => { disposed = true; unlisten?.() }
  }, [])

  const graph = plan?.result?.graph
  // 76#10：重置只在方案 id 真正切换时发生——原先依赖 [plan, graph] 对象
  // identity，他处采用方案触发的 refreshPlans 替换同 id 方案对象也会把
  // 用户正在浏览的选区/路径弹回首节点
  useEffect(() => {
    setSelected(graph?.nodes[0]?.id || '')
    setTarget(graph?.nodes.at(-1)?.id || '')
    setPath(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- graph 随 plan.id 变化派生
  }, [plan?.id])
  // 120#5：全局搜索节点结果的定位——initialPlanId 已切到所属方案，图谱
  // 就绪后选中该节点（nonce 支持同一节点重复点击再次定位）；声明在
  // 76#10 重置之后，同批 effect 中后写覆盖首节点重置
  // 121#3：目标只对所属方案生效——模型生成的节点 id（小写短横线）跨方案
  // 不保证唯一，陈旧目标在撞名图谱上会静默拉回旧选中（绕过 76#10 的
  // [plan?.id] 重置保护）；消费即过期（不匹配也一并清除，App 侧状态归零）
  useEffect(() => {
    if (!focusTarget || !graph || !plan) return
    if (focusTarget.planId === plan.id && graph.nodes.some(item => item.id === focusTarget.nodeId)) {
      setSelected(focusTarget.nodeId)
    }
    onFocusConsumed?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只随定位目标与图谱数据驱动
  }, [focusTarget, graph])
  const layout = useMemo(() => graph ? graphLayout(graph) : { positions: new Map<string, Position>(), width: 760, height: 390 }, [graph])
  const { positions, width: mapWidth, height: mapHeight } = layout
  const node = graph?.nodes.find(item => item.id === selected)
  const progress = overview?.nodes.find(item => item.nodeId === selected)
  const stage = plan?.result?.stages.find(item => item.nodeIds.includes(selected))
  const prerequisites = graph?.edges.filter(edge => edge.to === selected).map(edge => graph.nodes.find(item => item.id === edge.from)?.title || edge.from) || []
  const successors = graph?.edges.filter(edge => edge.from === selected).map(edge => graph.nodes.find(item => item.id === edge.to)?.title || edge.to) || []

  const generatePath = async () => {
    if (!plan || !selected || !target) return
    setBusy(true)
    setError('')
    try {
      const next = await backend.getKnowledgePath(plan.id, selected, target)
      setPath(next)
      notify(`已按真实依赖生成最短路径，共 ${next.length} 个知识节点`)
    } catch (cause) { setError(backendErrorMessage(cause)); setErrorCode(errorCodeOf(cause)); setErrorSource('path'); setPath(null) } finally { setBusy(false) }
  }
  const addTask = async () => {
    if (!plan || !node) return
    setBusy(true)
    try {
      await backend.addKnowledgeTask(plan.id, node.id)
      notify(`已把「${node.title}」加入方案，重复添加不会重复创建任务`)
      onNavigate('plan')
    } catch (cause) { setError(backendErrorMessage(cause)); setErrorCode(errorCodeOf(cause)); setErrorSource('task') } finally { setBusy(false) }
  }

  // 121#6：重试按错误源分流——原先固定 refreshOverview，路径计算/加任务/
  // 方案列表失败点重试只清横幅、动作未恢复
  const retry = () => {
    setError('')
    setErrorCode('')
    if (errorSource === 'path') void generatePath()
    else if (errorSource === 'task') void addTask()
    else if (errorSource === 'plans') void refreshPlans()
    else { void refreshPlans(); void refreshOverview() }
  }
  // 150#3：path_not_found 用同一对起终点重跑必定再失败——横幅改为只给关闭
  // 与改选指引，不再提供注定失败的重试入口
  const pathUnreachable = errorSource === 'path' && errorCode === 'path_not_found'

  // P-136：加载态与空态分离——方案列表未回来前不显示「尚未采用学习方案」
  if (!plan || !graph) return <div className="page map-page"><div className="page-heading"><div><h1>知识地图</h1><p>地图来自你实际采用的学习方案，不展示预设成绩。</p></div></div>{!plansLoaded ? <div className="provider-empty" role="status"><LoaderCircle size={26} className="spin" /><h2>正在读取知识地图</h2><p>正在拉取已采用的学习方案与知识依赖，稍候即可看到真实图谱。</p></div> : error ? <div className="provider-empty"><MapIcon size={26} /><h2>知识地图读取失败</h2><p role="alert">{error}</p><Button variant="secondary" onClick={retry}>重新读取</Button></div> : <div className="provider-empty"><MapIcon size={26} /><h2>尚未采用学习方案</h2><p>先根据目标生成并采用方案，辅助资料可选；知识节点、依赖和阅读记录会出现在这里。</p><Button onClick={() => onNavigate('generator')}>前往 AI 方案生成<ArrowRight size={16} /></Button></div>}</div>

  return <div className="page map-page">
    <div className="page-heading"><div><h1>知识地图</h1><p>{plan.goal} · 每个掌握度都有证据，每条路径都来自真实依赖。</p></div><div className="heading-actions"><select aria-label="选择已采用方案" value={plan.id} onChange={event => { setPlan(plans.find(item => item.id === event.target.value) || null); setError(''); setErrorCode('') }}>{plans.map(item => <option value={item.id} key={item.id}>{item.goal} · v{item.version}</option>)}</select><div className="segmented">{['图谱', '列表'].map(item => <button className={view === item ? 'active' : ''} aria-pressed={view === item} key={item} onClick={() => setView(item)}>{item === '图谱' ? <MapIcon size={16} /> : <List size={16} />}{item}</button>)}</div></div></div>
    {/* 120#10：错误横幅补 role=alert 与重试——选区视图下原先只能等下一个
        证据事件自愈 */}
    {/* 150#3：横幅补关闭入口；path_not_found 不再给注定失败的重试 */}
    {error && <div className="form-error" role="alert"><CircleDashed size={16} />{error}{pathUnreachable && <span>请更换「补学目标」或另选起点节点后再生成路径。</span>}<Button variant="ghost" size="sm" onClick={() => { setError(''); setErrorCode('') }}>关闭</Button>{!pathUnreachable && <Button variant="ghost" size="sm" onClick={retry}>重试</Button>}</div>}
    <div className="map-summary"><div><strong>{graph.nodes.length}</strong><span>方案知识节点</span></div><div><strong>{overview?.overallMastery === null || !overview ? '待评估' : `${overview.overallMastery}%`}</strong><span>测评掌握度</span></div><div><strong>{overview?.missingEvidence ?? graph.nodes.length}</strong><span>待收集证据</span></div><div className="map-legend"><span><i className="legend-mastered" />已测评达标</span><span><i className="legend-learning" />进行中（已阅读或测评未达标）</span><span><i className="legend-locked" />待评估</span></div></div>
    <div className="map-layout">
      <section className="surface map-surface">
        <SectionHeader title="真实知识网络" description={path ? `最短补学路径：${path.map(id => graph.nodes.find(item => item.id === id)?.title || id).join(' → ')}` : '点击节点查看证据，或选择目标计算真实有向最短路径。'} action={<div className="heading-actions"><select aria-label="补学目标" value={target} onChange={event => { setTarget(event.target.value); setPath(null); setError(''); setErrorCode('') }}>{graph.nodes.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select><Button variant={path ? 'primary' : 'secondary'} size="sm" onClick={() => path ? setPath(null) : void generatePath()} disabled={busy}><Route size={16} />{path ? '退出路径' : '生成补学路径'}</Button></div>} />
        {/* 146#10：show-path 死类已删（无 CSS 规则、无脚本消费）；路径高亮由 path-edge/on-path 类承载 */}
        {view === '图谱' ? <div className="knowledge-canvas" style={{ width: mapWidth, height: mapHeight }}>
          <svg viewBox={`0 0 ${mapWidth} ${mapHeight}`} preserveAspectRatio="none" aria-hidden="true">{graph.edges.map(edge => { const a = positions.get(edge.from); const b = positions.get(edge.to); const onPath = path?.some((id, index) => index < path.length - 1 && id === edge.from && path[index + 1] === edge.to); return a && b ? <line key={`${edge.from}-${edge.to}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={onPath ? 'path-edge' : ''} /> : null })}</svg>
          {graph.nodes.map(item => { const nodeProgress = overview?.nodes.find(progress => progress.nodeId === item.id); const mastery = nodeProgress?.mastery; const position = positions.get(item.id); if (!position) return null; const hasEvidence = mastery !== null && mastery !== undefined; const middle = hasEvidence || nodeProgress?.readingCompleted; return <button key={item.id} className={`map-node ${hasEvidence ? ((mastery ?? 0) >= 80 ? 'node-mastered' : 'node-learning') : middle ? 'node-learning' : 'node-locked'} ${selected === item.id ? 'selected' : ''} ${path?.includes(item.id) ? 'on-path' : ''}`} aria-current={selected === item.id ? 'true' : undefined} title={item.title} style={{ left: position.x, top: position.y }} onClick={() => { setSelected(item.id); setPath(null); setError(''); setErrorCode('') }}><span className="node-status">{hasEvidence && (mastery ?? 0) >= 80 ? <Check size={15} /> : middle ? <GitBranch size={15} /> : <CircleDashed size={15} />}</span><strong>{item.title}</strong><small>{!hasEvidence ? (nodeProgress?.readingCompleted ? '已阅读' : '待评估') : `${mastery}%`}</small></button> })}
        </div> : <div className="knowledge-list">{graph.nodes.map(item => { const mastery = overview?.nodes.find(progress => progress.nodeId === item.id)?.mastery; return <button key={item.id} onClick={() => { setSelected(item.id); setError(''); setErrorCode('') }} className={selected === item.id ? 'active' : ''} aria-current={selected === item.id ? 'true' : undefined}><div><strong>{item.title}</strong><span>{item.summary}</span></div><div><span>{mastery === null || mastery === undefined ? '待评估' : `${mastery}%`}</span><ProgressBar value={mastery || 0} srLabel={`节点掌握度 ${mastery ?? 0}%`} /></div><ArrowRight size={17} /></button> })}</div>}
      </section>
      {node && <aside className="surface node-detail">
        <div className="node-detail-top"><Badge tone={progress?.mastery != null ? 'green' : progress?.readingCompleted ? 'amber' : 'neutral'}>{progress?.mastery != null ? '已有测评' : progress?.readingCompleted ? '阅读完成' : '待评估'}</Badge><span>知识节点详情</span></div><h2>{node.title}</h2><p>{node.summary}</p>
        <div className="mastery-evidence"><div><strong>{progress?.mastery == null ? '待评估' : `${progress.mastery}%`}</strong><span>测评掌握度</span></div><ProgressBar value={progress?.mastery || 0} srLabel={`测评掌握度 ${progress?.mastery ?? 0}%`} /><small><Target size={14} />{progress?.evidence || '尚无测评证据'}</small></div>
        <div className="node-relations"><h3>知识关系</h3><div><span>前置知识</span><strong>{prerequisites.length ? prerequisites.join('、') : '无'}</strong></div><div><span>后续应用</span><strong>{successors.length ? successors.join('、') : '无直接后续节点'}</strong></div></div>
        <div className="node-actions"><Button disabled={!stage} onClick={() => { if (stage) { if (onStudy) onStudy(plan.id, stage.id); else onNavigate('study') } }}>{stage ? <BookOpen size={17} /> : <CircleDashed size={17} />}{stage ? '开始学习' : '尚未安排阶段'}</Button><Button variant="secondary" disabled={!stage || !node} onClick={() => { if (stage && node) { if (onPractice) onPractice(plan.id, stage.id, node.id); else onNavigate('practice') } }}><Target size={17} />针对「{node?.title}」练习</Button><Button variant="secondary" onClick={() => void addTask()} disabled={busy}><Play size={17} />加入方案</Button></div>
        {node.citations.length > 0 && <div className="node-agent"><h3>原文引用</h3>{node.citations.map((citation, index) => <button key={`${citation.materialId}-${index}`} onClick={() => onNavigate('library')}><p>「{citation.quote}」</p><small>资料 #{citation.materialId} · 来自所选原文</small></button>)}</div>}
      </aside>}
    </div>
  </div>
}

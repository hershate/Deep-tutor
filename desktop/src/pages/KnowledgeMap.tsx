import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowRight, BookOpen, Check, CircleDashed, GitBranch, List, Map as MapIcon, Play, Route, Target } from 'lucide-react'
import type { PageId } from '../data'
import { Badge, Button, ProgressBar, SectionHeader } from '../components/UI'
import { backend, backendErrorMessage, onBackendEvent } from '../backend/client'
import type { KnowledgeOverview, LearningGraph, LearningPlan } from '../backend/client'

type Position = { x: number; y: number; depth: number }

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
  for (const [level, ids] of groups) {
    ids.forEach((id, index) => positions.set(id, {
      x: (level + 1) * 760 / (maximum + 2),
      y: (index + 1) * 390 / (ids.length + 1),
      depth: level,
    }))
  }
  return positions
}

export default function KnowledgeMap({ onNavigate, notify, onStudy, onPractice, initialPlanId }: { onNavigate: (page: PageId) => void; notify: (message: string) => void; onStudy?: (planId: string, stageId: string) => void; onPractice?: (planId: string, stageId: string, nodeId: string) => void; initialPlanId?: string | null }) {
  const [plans, setPlans] = useState<LearningPlan[]>([])
  const [plan, setPlan] = useState<LearningPlan | null>(null)
  const [overview, setOverview] = useState<KnowledgeOverview | null>(null)
  const [selected, setSelected] = useState('')
  const [target, setTarget] = useState('')
  const [path, setPath] = useState<string[] | null>(null)
  const [view, setView] = useState('图谱')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const planRef = useRef<LearningPlan | null>(null)
  useEffect(() => { planRef.current = plan }, [plan])

  const refreshOverview = useCallback(async () => {
    if (!plan) { setOverview(null); return }
    try { setOverview(await backend.getKnowledgeOverview(plan.id)) } catch (cause) { setError(backendErrorMessage(cause)) }
  }, [plan])
  const refreshPlans = useCallback(async () => {
    try {
      const items = (await backend.listLearningPlans()).filter(item => item.adoptedProjectId !== null)
      setPlans(items)
      const preferred = initialPlanId
      setPlan(current => preferred ? items.find(item => item.id === preferred) || items[0] || null : current ? items.find(item => item.id === current.id) || items[0] || null : items[0] || null)
    } catch (cause) { setError(backendErrorMessage(cause)) }
  }, [initialPlanId])
  useEffect(() => { void refreshPlans() }, [refreshPlans])
  useEffect(() => { void refreshOverview() }, [refreshOverview])
  useEffect(() => {
    let disposed = false
    let unlisten: (() => void) | undefined
    void onBackendEvent(event => {
      if (disposed) return
      if (event.kind === 'plan.adopted') void refreshPlans()
      if (event.kind === 'study.progress_changed' && event.payload.planId === planRef.current?.id) void refreshOverview()
    }).then(remove => { if (disposed) remove(); else unlisten = remove })
    return () => { disposed = true; unlisten?.() }
  }, [refreshOverview, refreshPlans])

  const graph = plan?.result?.graph
  useEffect(() => {
    setSelected(graph?.nodes[0]?.id || '')
    setTarget(graph?.nodes.at(-1)?.id || '')
    setPath(null)
  }, [plan, graph])
  const positions = useMemo(() => graph ? graphLayout(graph) : new Map<string, Position>(), [graph])
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
      notify(`已按真实依赖生成最短路径，共 ${next.length} 个节点`)
    } catch (cause) { setError(backendErrorMessage(cause)); setPath(null) } finally { setBusy(false) }
  }
  const addTask = async () => {
    if (!plan || !node) return
    setBusy(true)
    try {
      await backend.addKnowledgeTask(plan.id, node.id)
      notify(`已把「${node.title}」加入学习计划，重复添加不会重复创建任务`)
      onNavigate('plan')
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy(false) }
  }

  if (!plan || !graph) return <div className="page map-page"><div className="page-heading"><div><h1>知识地图</h1><p>地图来自你实际采用的学习方案，不展示预设成绩。</p></div></div><div className="provider-empty"><MapIcon size={26} /><h2>尚未采用学习方案</h2><p>先通过真实资料生成方案并采用，知识节点、依赖和阅读记录才会出现在这里。</p><Button onClick={() => onNavigate('generator')}>前往方案生成<ArrowRight size={16} /></Button></div></div>

  return <div className="page map-page">
    <div className="page-heading"><div><h1>知识地图</h1><p>{plan.goal} · 每个掌握度都有证据，每条路径都来自真实依赖。</p></div><div className="heading-actions"><select aria-label="选择已采用方案" value={plan.id} onChange={event => setPlan(plans.find(item => item.id === event.target.value) || null)}>{plans.map(item => <option value={item.id} key={item.id}>{item.goal} · v{item.version}</option>)}</select><div className="segmented">{['图谱', '列表'].map(item => <button className={view === item ? 'active' : ''} key={item} onClick={() => setView(item)}>{item === '图谱' ? <MapIcon size={16} /> : <List size={16} />}{item}</button>)}</div></div></div>
    {error && <div className="form-error"><CircleDashed size={16} />{error}</div>}
    <div className="map-summary"><div><strong>{graph.nodes.length}</strong><span>方案知识节点</span></div><div><strong>{overview?.overallMastery === null || !overview ? '待评估' : `${overview.overallMastery}%`}</strong><span>测评掌握度</span></div><div><strong>{overview?.missingEvidence ?? graph.nodes.length}</strong><span>待收集证据</span></div><div className="map-legend"><span><i className="legend-mastered" />已有测评</span><span><i className="legend-learning" />阅读完成</span><span><i className="legend-locked" />待评估</span></div></div>
    <div className="map-layout">
      <section className="surface map-surface">
        <SectionHeader title="真实知识网络" description={path ? `最短补学路径：${path.map(id => graph.nodes.find(item => item.id === id)?.title || id).join(' → ')}` : '点击节点查看证据，或选择目标计算真实有向最短路径。'} action={<div className="heading-actions"><select aria-label="补学目标" value={target} onChange={event => { setTarget(event.target.value); setPath(null) }}>{graph.nodes.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select><Button variant={path ? 'primary' : 'secondary'} size="sm" onClick={() => path ? setPath(null) : void generatePath()} disabled={busy}><Route size={16} />{path ? '退出路径' : '生成补学路径'}</Button></div>} />
        {view === '图谱' ? <div className={`knowledge-canvas ${path ? 'show-path' : ''}`}>
          <svg viewBox="0 0 760 390" preserveAspectRatio="none" aria-hidden="true">{graph.edges.map(edge => { const a = positions.get(edge.from)!; const b = positions.get(edge.to)!; const onPath = path?.some((id, index) => index < path.length - 1 && id === edge.from && path[index + 1] === edge.to); return <line key={`${edge.from}-${edge.to}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={onPath ? 'path-edge' : ''} /> })}</svg>
          {graph.nodes.map(item => { const mastery = overview?.nodes.find(progress => progress.nodeId === item.id)?.mastery; const position = positions.get(item.id)!; return <button key={item.id} className={`map-node ${mastery === null || mastery === undefined ? 'node-locked' : mastery >= 80 ? 'node-mastered' : 'node-learning'} ${selected === item.id ? 'selected' : ''} ${path?.includes(item.id) ? 'on-path' : ''}`} style={{ left: `${position.x / 760 * 100}%`, top: `${position.y / 390 * 100}%` }} onClick={() => { setSelected(item.id); setPath(null) }}><span className="node-status">{mastery !== null && mastery !== undefined && mastery >= 80 ? <Check size={15} /> : mastery === null || mastery === undefined ? <CircleDashed size={15} /> : <GitBranch size={15} />}</span><strong>{item.title}</strong><small>{mastery === null || mastery === undefined ? '待评估' : `${mastery}%`}</small></button> })}
        </div> : <div className="knowledge-list">{graph.nodes.map(item => { const mastery = overview?.nodes.find(progress => progress.nodeId === item.id)?.mastery; return <button key={item.id} onClick={() => setSelected(item.id)} className={selected === item.id ? 'active' : ''}><div><strong>{item.title}</strong><span>{item.summary}</span></div><div><span>{mastery === null || mastery === undefined ? '待评估' : `${mastery}%`}</span><ProgressBar value={mastery || 0} /></div><ArrowRight size={17} /></button> })}</div>}
      </section>
      {node && <aside className="surface node-detail">
        <div className="node-detail-top"><Badge tone={progress?.mastery != null && progress.mastery >= 70 ? 'green' : progress?.readingCompleted ? 'amber' : 'neutral'}>{progress?.mastery != null ? '已有测评' : progress?.readingCompleted ? '阅读完成' : '待评估'}</Badge><span>知识点详情</span></div><h2>{node.title}</h2><p>{node.summary}</p>
        <div className="mastery-evidence"><div><strong>{progress?.mastery == null ? '待评估' : `${progress.mastery}%`}</strong><span>测评掌握度</span></div><ProgressBar value={progress?.mastery || 0} /><small><Target size={14} />{progress?.evidence || '尚无测评证据'}</small></div>
        <div className="node-relations"><h3>知识关系</h3><div><span>前置知识</span><strong>{prerequisites.length ? prerequisites.join('、') : '无'}</strong></div><div><span>后续应用</span><strong>{successors.length ? successors.join('、') : '无直接后续节点'}</strong></div></div>
        <div className="node-actions"><Button disabled={!stage} onClick={() => { if (stage) { if (onStudy) onStudy(plan.id, stage.id); else onNavigate('study') } }}>{stage ? <BookOpen size={17} /> : <CircleDashed size={17} />}{stage ? '开始学习' : '尚未安排阶段'}</Button><Button variant="secondary" disabled={!stage || !node} onClick={() => { if (stage && node) { if (onPractice) onPractice(plan.id, stage.id, node.id); else onNavigate('practice') } }}><Target size={17} />针对「{node?.title}」练习</Button><Button variant="secondary" onClick={() => void addTask()} disabled={busy}><Play size={17} />加入计划</Button></div>
        <div className="node-agent"><h3>原文引用</h3>{node.citations.map((citation, index) => <button key={`${citation.materialId}-${index}`} onClick={() => onNavigate('library')}><p>“{citation.quote}”</p><small>资料 #{citation.materialId} · 来自所选原文</small></button>)}</div>
      </aside>}
    </div>
  </div>
}

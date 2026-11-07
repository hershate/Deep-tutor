import { useEffect, useState } from 'react'
import { AlertTriangle, BookOpen, CalendarDays, Check, Clock3, FileUp, LoaderCircle, LockKeyhole, Route, Target } from 'lucide-react'
import { Badge, Button } from '../components/UI'
import { backendErrorMessage } from '../backend/client'
import { sharingBackend } from '../backend/sharing'
import type { SharedPlanView } from '../backend/sharing'
import type { LearningCheckName } from '../backend/client'

const validationNames: [LearningCheckName, string][] = [
  ['dependencyClosure', '依赖闭包'],
  ['difficultyStep', '难度阶梯'],
  ['cognitiveLoad', '认知负荷'],
  ['milestoneFeasibility', '里程碑可达'],
  ['gapAnalysis', '缺口分析'],
]

export default function SharedPlan({ token = null }: { token?: string | null }) {
  const [view, setView] = useState<SharedPlanView | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(Boolean(token))
  const [opening, setOpening] = useState(false)

  useEffect(() => {
    if (!token) { setLoading(false); return }
    let active = true
    setLoading(true)
    setError('')
    setView(null)
    sharingBackend.read(token).then(result => { if (active) setView(result) }).catch(cause => { if (active) setError(backendErrorMessage(cause)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [token])

  const openPackage = async () => {
    setOpening(true)
    setError('')
    try {
      const path = await sharingBackend.openFile()
      if (!path) return
      setView(await sharingBackend.readFile(path))
    } catch (cause) {
      // 打开新包失败时清空旧内容并给出提示，避免用户误以为新包已打开
      setView(null)
      setError(backendErrorMessage(cause))
    } finally { setOpening(false) }
  }

  if (loading) return <div className="shared-missing"><LoaderCircle size={32} className="spin" /><h1>正在读取真实分享</h1><p>正在校验本机快照、撤销状态与有效期。</p></div>
  if (!view) return <div className="shared-missing"><LockKeyhole size={36} /><h1>{token ? '分享内容不可用' : '打开只读学习方案'}</h1><p>{error || '选择由知舟导出的 .dtshare 分享包，查看不可变的方案快照。'}</p><Button onClick={() => void openPackage()} disabled={opening}><FileUp size={17} />{opening ? '正在等待选择文件' : '选择分享包'}</Button>{token && <span>不可用</span>}</div>

  const { plan } = view
  return <div className="shared-plan">
    <header><div><span className="shared-logo">知舟</span><Badge tone="green">只读分享</Badge></div><span>有效期至 {new Date(view.expiresAt).toLocaleString()}</span></header>
    <main>
      <div className="shared-hero"><Badge tone="green">学习方案 · v{plan.planVersion}</Badge><h1>{plan.goal}</h1><p>由本机用户分享的方案快照 · 不包含私人笔记、会话、学习进度或模型密钥。</p><div><span><Target size={16} />{plan.stages.length} 个渐进阶段</span><span><CalendarDays size={16} />{plan.schedule.days} 天</span><span><Clock3 size={16} />每日 {plan.dailyMinutes} 分钟</span></div></div>
      {view.source === 'offline' && <div className="modal-callout offline-notice"><FileUp size={18} /><span>这是独立离线分享包。它无法接收创建者之后的撤销；校验值仅用于检测损坏，不证明发送者身份。</span></div>}
      {plan.warnings.length > 0 && <div className="capacity-warning"><AlertTriangle size={18} /><div><strong>原方案的复核提示</strong>{plan.warnings.map(warning => <p key={warning}>{warning}</p>)}</div></div>}
      <section><h2>学习路径</h2><p className="shared-description">{plan.schedule.startDate} 至 {plan.schedule.endDate} · 目标截止 {plan.deadline}</p>{plan.stages.map((stage, index) => <article className="shared-stage" key={stage.id}><span>{index + 1}</span><div><strong>{stage.title}</strong><p>{stage.nodeIds.map(id => plan.graph.nodes.find(node => node.id === id)?.title || id).join(' → ')}</p><small>{stage.startDate} 至 {stage.endDate} · {stage.minutes} 分钟 · 难度 {stage.difficulty}/5</small></div><Badge tone="neutral">方案阶段</Badge></article>)}</section>
      <section><h2>知识结构</h2><div className="shared-nodes">{plan.graph.nodes.map(node => <details key={node.id}><summary><BookOpen size={16} /><strong>{node.title}</strong><span>难度 {node.difficulty}/5</span></summary><p>{node.summary}</p></details>)}</div></section>
      <section><h2>方案校验</h2><div className="shared-validation">{validationNames.map(([key, name]) => { const check = plan.validation[key]; return <div key={key}><span>{check.status === 'pass' ? <Check size={15} /> : <AlertTriangle size={15} />}{name}</span><Badge tone={check.status === 'pass' ? 'green' : check.status === 'warn' ? 'amber' : 'red'}>{check.status === 'pass' ? '通过' : check.status === 'warn' ? '警告' : '未通过'}</Badge>{check.details && <p>{check.details}</p>}</div>})}</div>{plan.validation.fixes.length > 0 && <ul>{plan.validation.fixes.map(fix => <li key={fix}>{fix}</li>)}</ul>}</section>
      <section><h2>本方案包含</h2><div className="shared-includes"><span><BookOpen size={17} />{plan.graph.nodes.length} 个知识节点</span><span><Route size={17} />{plan.graph.edges.length} 条依赖关系</span><span><CalendarDays size={17} />{plan.schedule.entries.length} 个学习日程</span></div></section>
      <Button variant="secondary" onClick={() => void openPackage()} disabled={opening}><FileUp size={16} />打开另一个分享包</Button>
    </main>
    <footer>知舟 Deep Tutor · {view.source === 'local' ? '本机只读视图，撤销与过期由原生后端校验' : '离线快照，仅校验内容与有效期'}</footer>
  </div>
}

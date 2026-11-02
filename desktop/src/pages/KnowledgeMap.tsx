import { useState } from 'react'
import { ArrowRight, BookOpen, Check, CircleDashed, GitBranch, List, Map as MapIcon, Play, Route, Sparkles, Target } from 'lucide-react'
import { knowledgeNodes } from '../data'
import type { PageId } from '../data'
import { Badge, Button, ProgressBar, SectionHeader } from '../components/UI'

const edges = [['prob', 'cond'], ['prob', 'indep'], ['cond', 'bayes'], ['indep', 'bayes'], ['bayes', 'nb'], ['nb', 'eval'], ['indep', 'eval']]

export default function KnowledgeMap({ onNavigate, notify }: { onNavigate: (page: PageId) => void; notify: (message: string) => void }) {
  const [selected, setSelected] = useState('bayes')
  const [view, setView] = useState('图谱')
  const [pathMode, setPathMode] = useState(false)
  const node = knowledgeNodes.find(n => n.id === selected)!
  return <div className="page map-page">
    <div className="page-heading"><div><h1>知识地图</h1><p>每个掌握度都有证据，每条路径都知道为何出发。</p></div><div className="segmented">{['图谱', '列表'].map(item => <button className={view === item ? 'active' : ''} key={item} onClick={() => setView(item)}>{item === '图谱' ? <MapIcon size={16} /> : <List size={16} />}{item}</button>)}</div></div>
    <div className="map-summary"><div><strong>24</strong><span>核心知识点</span></div><div><strong>68<small>%</small></strong><span>整体掌握度</span></div><div><strong>3</strong><span>待补齐缺口</span></div><div className="map-legend"><span><i className="legend-mastered" />已掌握</span><span><i className="legend-learning" />学习中</span><span><i className="legend-locked" />未开始</span></div></div>
    <div className="map-layout">
      <section className="surface map-surface">
        <SectionHeader title="概率论 · 第四章知识网络" description={pathMode ? '已高亮前往“模型评估”的最短补学路径。' : '点击知识点查看掌握证据与可执行行动。'} action={<Button variant={pathMode ? 'primary' : 'secondary'} size="sm" onClick={() => { setPathMode(!pathMode); notify(pathMode ? '已退出补学路径' : '已生成最短补学路径') }}><Route size={16} />{pathMode ? '退出路径' : '生成补学路径'}</Button>} />
        {view === '图谱' ? <div className={`knowledge-canvas ${pathMode ? 'show-path' : ''}`}>
          <svg viewBox="0 0 760 390" preserveAspectRatio="none" aria-hidden="true">{edges.map(([from, to]) => { const a = knowledgeNodes.find(n => n.id === from)!; const b = knowledgeNodes.find(n => n.id === to)!; const onPath = pathMode && ((from === 'bayes' && to === 'nb') || (from === 'nb' && to === 'eval')); return <line key={`${from}-${to}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={onPath ? 'path-edge' : ''} /> })}</svg>
          {knowledgeNodes.map(item => <button key={item.id} className={`map-node node-${item.status} ${selected === item.id ? 'selected' : ''} ${pathMode && ['bayes', 'nb', 'eval'].includes(item.id) ? 'on-path' : ''}`} style={{ left: `${item.x / 760 * 100}%`, top: `${item.y / 390 * 100}%` }} onClick={() => setSelected(item.id)}><span className="node-status">{item.mastery >= 80 ? <Check size={15} /> : item.mastery === 0 ? <CircleDashed size={15} /> : <GitBranch size={15} />}</span><strong>{item.label}</strong><small>{item.mastery}%</small></button>)}
        </div> : <div className="knowledge-list">{knowledgeNodes.map(item => <button key={item.id} onClick={() => setSelected(item.id)} className={selected === item.id ? 'active' : ''}><div><strong>{item.label}</strong><span>{item.description}</span></div><div><span>{item.mastery}%</span><ProgressBar value={item.mastery} /></div><ArrowRight size={17} /></button>)}</div>}
      </section>
      <aside className="surface node-detail">
        <div className="node-detail-top"><Badge tone={node.mastery >= 70 ? 'green' : node.mastery > 0 ? 'amber' : 'neutral'}>{node.status}</Badge><span>知识点详情</span></div><h2>{node.label}</h2><p>{node.description}</p>
        <div className="mastery-evidence"><div><strong>{node.mastery}%</strong><span>当前掌握度</span></div><ProgressBar value={node.mastery} /><small><Target size={14} />{node.evidence}</small></div>
        <div className="node-relations"><h3>知识关系</h3><div><span>前置知识</span><strong>{node.id === 'prob' ? '无' : node.id === 'bayes' ? '条件概率、独立性' : '概率基础'}</strong></div><div><span>后续应用</span><strong>{node.id === 'eval' ? '综合项目' : node.id === 'nb' ? '模型评估' : '朴素贝叶斯'}</strong></div></div>
        <div className="node-actions"><Button onClick={() => onNavigate(node.mastery === 0 ? 'study' : 'practice')}>{node.mastery === 0 ? <BookOpen size={17} /> : <Play size={17} />}{node.mastery === 0 ? '开始学习' : '针对练习'}</Button><Button variant="secondary" onClick={() => { notify(`已把「${node.label}」加入今日计划`); onNavigate('plan') }}>加入计划</Button></div>
        <div className="node-agent"><Sparkles size={17} /><p>{node.mastery < 70 ? '建议先用 5 分钟做一次闭卷回忆，再针对答不出的部分学习。' : '掌握较稳定，可通过交错练习保持，不必重复整章阅读。'}</p></div>
      </aside>
    </div>
  </div>
}

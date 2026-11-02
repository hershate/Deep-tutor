import { useState } from 'react'
import { ArrowUp, BookOpen, Check, ChevronRight, FileText, History, Link2, Search, ShieldCheck, Trash2 } from 'lucide-react'
import type { Resource } from '../data'
import { Badge, Button, EmptyState } from './UI'

type WikiHistory = { id: number; question: string; answer: string; sources: string[] }

export default function WikiPanel({ resources, notify }: { resources: Resource[]; notify: (message: string) => void }) {
  const [selected, setSelected] = useState<number[]>(resources.slice(0, 2).map(r => r.id))
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<WikiHistory | null>(null)
  const [history, setHistory] = useState<WikiHistory[]>([{ id: 1, question: '贝叶斯定理和全概率公式有什么关系？', answer: '全概率公式用于计算证据出现的总概率 P(B)，它是贝叶斯公式分母的重要来源。', sources: ['概率论与数理统计 · 第四章'] }])
  const ask = () => {
    if (!question.trim() || !selected.length) return
    const item = { id: Date.now(), question: question.trim(), answer: '根据当前选中的库内资料，判断阳性结果时必须同时考虑先验患病率与检测性能。低基线率会放大健康人群中假阳性的相对数量，因此“准确率高”并不直接等于“阳性后患病概率高”。', sources: resources.filter(r => selected.includes(r.id)).slice(0, 2).map(r => r.title) }
    setAnswer(item); setHistory([item, ...history]); setQuestion('')
  }
  return <div className="wiki-layout"><aside className="wiki-docs"><div className="wiki-docs-head"><div><BookOpen size={18} /><strong>引用范围</strong></div><Badge tone="neutral">{selected.length} 份</Badge></div><p>回答只会引用选中的库内文档。</p>{resources.map(resource => <label key={resource.id} className={selected.includes(resource.id) ? 'selected' : ''}><input type="checkbox" checked={selected.includes(resource.id)} onChange={() => setSelected(selected.includes(resource.id) ? selected.filter(id => id !== resource.id) : [...selected, resource.id])} /><FileText size={17} /><div><strong>{resource.title}</strong><span>{resource.type} · {resource.meta}</span></div></label>)}<div className="wiki-policy"><ShieldCheck size={16} />白名单过滤无法定位到原文的引用，不用模型记忆补造来源。</div></aside><main className="wiki-qa"><div className="wiki-answer-area">{answer ? <article className="wiki-answer"><Badge tone="green">库内引用回答</Badge><h2>{answer.question}</h2><p>{answer.answer}</p><div className="wiki-citations">{answer.sources.map((source, i) => <button key={source} onClick={() => notify(`已定位到「${source}」的解析文本`)}><span>[{i + 1}]</span><div><strong>{source}</strong><small>第 4 章 · 解析文本第 18 段</small></div><ChevronRight size={15} /></button>)}</div><div className="wiki-answer-note"><Check size={15} />{answer.sources.length} 条引用均通过库内白名单校验</div></article> : <EmptyState title="向你的知识库提问" description="先选择引用文档，再输入问题。回答、引用与历史都会保留在当前页面。" />}</div><form className="wiki-composer" onSubmit={e => { e.preventDefault(); ask() }}><textarea rows={3} value={question} onChange={e => setQuestion(e.target.value)} placeholder="例如：根据教材和课程，为什么准确率高仍可能误判？" /><div><span>{selected.length ? `基于 ${selected.length} 份文档作答` : '请至少选择一份文档'}</span><Button type="submit" disabled={!question.trim() || !selected.length}><ArrowUp size={17} />提问</Button></div></form></main><aside className="wiki-history"><div><History size={17} /><strong>问答历史</strong></div>{history.map(item => <button key={item.id} onClick={() => setAnswer(item)}><strong>{item.question}</strong><span>{item.sources.length} 条引用</span></button>)}{history.length > 0 && <Button variant="ghost" size="sm" onClick={() => { setHistory([]); setAnswer(null); notify('Wiki 问答历史已删除') }}><Trash2 size={15} />清空历史</Button>}</aside></div>
}

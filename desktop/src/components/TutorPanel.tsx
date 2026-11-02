import { useEffect, useRef, useState } from 'react'
import { ArrowUp, BookOpen, Lightbulb, MessageCircleQuestion, RotateCcw, Sparkles, X } from 'lucide-react'
import type { ChatMessage } from '../data'
import { IconButton } from './UI'

const initialMessages: ChatMessage[] = [
  { id: 1, role: 'assistant', text: '我在当前章节里。你可以问概念，也可以让我只给一点提示。现在最想弄清楚哪一步？' },
]

function demoReply(input: string, mode: string): ChatMessage {
  const lower = input.toLowerCase()
  if (input.includes('先验') || input.includes('贝叶斯') || input.includes('准确率')) return { id: Date.now(), role: 'assistant', text: '先把“检测有多准”和“我患病的可能性”分开。准确率高，只说明检测结果与真实状态高度相关；贝叶斯定理还会把疾病原本有多常见（先验概率）纳入更新。你可以把它理解为：新证据很重要，但起点也很重要。', source: '《概率论与数理统计》第 4 章 · 第 18 页' }
  if (input.includes('提示') || mode === '提示') return { id: Date.now(), role: 'assistant', text: '我只给一个提示：不要急着套公式。先写出“在未检测前，此人患病的概率是多少”，再比较患者与健康人各自产生阳性结果的可能性。' }
  if (input.includes('练习') || mode === '练习') return { id: Date.now(), role: 'assistant', text: '来做一个快速检查：如果一种疾病的患病率从 1% 上升到 10%，而检测性能不变，阳性结果的可信度会怎么变？请先用直觉回答，我再陪你验证。' }
  if (lower.includes('递归')) return { id: Date.now(), role: 'assistant', text: '递归先检查三件事：什么时候停止、问题是否严格变小、子结果怎样组合。你可以把当前函数画成一棵调用树，我们再逐层看返回过程。' }
  return { id: Date.now(), role: 'assistant', text: `我们可以用“概念 → 例子 → 反例 → 自测”来拆解「${input.slice(0, 28)}」。先说你的直觉理解，即使不完整也可以；我会针对你的回答继续追问，而不是直接给结论。` }
}

export default function TutorPanel({ compact = false, onClose }: { compact?: boolean; onClose?: () => void }) {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages)
  const [input, setInput] = useState('')
  const [mode, setMode] = useState('讲解')
  const [typing, setTyping] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [messages, typing])
  const send = (value = input) => {
    const clean = value.trim()
    if (!clean || typing) return
    setMessages(prev => [...prev, { id: Date.now(), role: 'user', text: clean }])
    setInput(''); setTyping(true)
    window.setTimeout(() => { setMessages(prev => [...prev, demoReply(clean, mode)]); setTyping(false) }, 650)
  }
  return <div className={`tutor-panel ${compact ? 'is-compact' : ''}`}>
    <div className="tutor-head"><div><span className="agent-mark"><Sparkles size={18} /></span><div><strong>知舟导师</strong><span><i />当前上下文已连接</span></div></div>{onClose && <IconButton label="关闭导师" onClick={onClose}><X size={18} /></IconButton>}</div>
    <div className="tutor-context"><BookOpen size={14} /><span>贝叶斯定理 · 第 3 节</span><button onClick={() => setMessages(initialMessages)}><RotateCcw size={13} />新对话</button></div>
    <div className="tutor-modes">{['讲解', '提示', '练习', '反馈'].map(item => <button key={item} className={mode === item ? 'active' : ''} onClick={() => setMode(item)}>{item}</button>)}</div>
    <div className="tutor-messages" aria-live="polite">
      {messages.map(message => <div className={`message message-${message.role}`} key={message.id}><div className="message-bubble">{message.text}</div>{message.source && <button className="message-source" onClick={() => setInput('请解释这段资料中的公式') }><BookOpen size={13} />{message.source}</button>}</div>)}
      {typing && <div className="message message-assistant"><div className="message-bubble typing"><span /><span /><span /><em>正在组织讲解</em></div></div>}
      <div ref={endRef} />
    </div>
    {messages.length < 3 && <div className="tutor-suggestions"><button onClick={() => send('为什么准确率高，阳性结果仍可能不可靠？')}>为什么准确率高仍不可靠？</button><button onClick={() => send('只给我一个提示')}>只给一点提示</button><button onClick={() => send('出一道练习')}>检查我的理解</button></div>}
    <form className="tutor-composer" onSubmit={e => { e.preventDefault(); send() }}><textarea value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} placeholder={mode === '反馈' ? '贴入你的答案，我会给出反馈…' : '提问，或描述你卡住的地方…'} aria-label="向知舟导师提问" rows={2} /><div className="composer-bottom"><span><MessageCircleQuestion size={14} />{mode}模式 · 演示回复</span><button type="submit" aria-label="发送问题" disabled={!input.trim() || typing}><ArrowUp size={18} /></button></div></form>
    <div className="tutor-foot"><Lightbulb size={13} />导师会优先引导思考，重要结论请核对来源。</div>
  </div>
}

import { useEffect, useRef, useState } from 'react'
import { ArrowUp, BookOpen, Lightbulb, LoaderCircle, MessageCircleQuestion, RotateCcw, Sparkles, Square, X } from 'lucide-react'
import { IconButton, Button } from './UI'
import { clampToBytes, utf8Bytes } from '../limits'
import { useAgent } from '../backend/useAgent'

const modes = ['讲解', '提示', '练习', '反馈']

// hintsOnly 由 App 传入：本组件不得再实例化 useBackendState，
// 否则抽屉打开期间会并行跑第二条 bootstrap 轮询与事件同步管道（09#1）
export default function TutorPanel({ compact = false, onClose, planId, contextLabel, hintsOnly = false }: { compact?: boolean; onClose?: () => void; planId?: string; contextLabel?: string; hintsOnly?: boolean }) {
  const { plans, activeSession, activeRun, loading, error, busy, stream, createSession, send, cancel, resume, resolveApproval } = useAgent({ planId, sessionTitle: '知舟导师' })
  const [input, setInput] = useState('')
  const [mode, setMode] = useState('讲解')
  const endRef = useRef<HTMLDivElement>(null)
  const running = activeRun?.status === 'running' || activeRun?.status === 'cancelling'
  const messages = activeSession?.messages.filter(message => message.role !== 'tool') ?? []
  const plan = plans.find(item => item.id === activeSession?.planId) ?? plans.find(item => item.id === planId) ?? plans[0]
  const context = contextLabel ?? plan?.goal ?? '尚未选择学习方案'
  // 82#7：仅贴底时跟随——同 AgentCenter，上翻阅读不被流式滚动劫持
  useEffect(() => {
    let scroller: HTMLElement | null = null
    for (let node = endRef.current?.parentElement ?? null; node; node = node.parentElement) {
      if (node.scrollHeight > node.clientHeight + 4) { scroller = node; break }
    }
    const nearBottom = !scroller || scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 48
    if (nearBottom) endRef.current?.scrollIntoView({ block: 'nearest' })
  }, [messages.length, stream.text])
  useEffect(() => { setMode(hintsOnly ? '提示' : '讲解') }, [hintsOnly])

  // 91#2：后端对拼接前缀后的整条 content 校验 32000 字节——输入钳制须
  // 按当前模式的实际前缀长度预留，否则贴近上限的输入发送时必超限，
  // 报错文案（32000）与界面所见限额口径矛盾且前缀不可见无法自查
  const prefix = `【辅导模式：${mode}】请依据当前方案的真实内容辅导我，不要编造来源或学习效果。${mode === '提示' ? '只给引导提示，不直接给完整答案。' : mode === '练习' ? '给出练习并等待我的回答，不要代替我作答。' : mode === '反馈' ? '针对我的答案指出具体依据、问题和下一步。' : '优先帮助我理解概念并检查我的理解。'}我的输入：`
  const inputBudget = 32000 - utf8Bytes(prefix)
  const submit = async (value = input) => {
    const clean = value.trim()
    if (!clean || running || busy === 'send' || busy === 'create') return
    const instruction = prefix + clean
    if (await send(instruction)) setInput('')
  }
  const newSession = async () => { await createSession('知舟导师', planId) }

  return <div className={`tutor-panel ${compact ? 'is-compact' : ''}`}>
    <div className="tutor-head"><div><span className="agent-mark"><Sparkles size={18} /></span><div><strong>知舟导师</strong><span><i />{error ? '后端操作失败' : '本地后端会话'}</span></div></div>{onClose && <IconButton label="关闭导师" onClick={onClose}><X size={18} /></IconButton>}</div>
    <div className="tutor-context"><BookOpen size={14} /><span>{context}</span><button disabled={running || busy === 'create'} onClick={() => void newSession()}><RotateCcw size={13} />新会话</button></div>
    <div className="tutor-modes">{modes.map(item => <button key={item} className={mode === item ? 'active' : ''} aria-pressed={mode === item} disabled={running} onClick={() => setMode(item)}>{item}</button>)}</div>
    {error && <div className="form-error" role="alert">{error}</div>}
    <div className="tutor-messages" aria-live="polite">
      {loading && <div className="message message-assistant"><div className="message-bubble">正在读取真实会话…</div></div>}
      {!loading && !messages.length && <div className="message message-assistant"><div className="message-bubble">请描述你想理解的内容。发送后，这里会显示模型通过原生后端返回的真实回答；没有模型档案时，会明确返回配置错误。</div></div>}
      {messages.map(message => <div className={`message message-${message.role}`} key={message.id}><div className="message-bubble" style={{ whiteSpace: 'pre-wrap' }}>{message.role === 'user' ? message.content.replace(/^【辅导模式：[^】]+】.*?我的输入：/s, '') : message.content || '模型本步请求了工具，详细结果可在 Agent 工作台查看。'}</div>{message.reasoning && <details className="thought-details"><summary>模型返回的推理内容</summary><p style={{ whiteSpace: 'pre-wrap' }}>{message.reasoning}</p></details>}{message.partial && <small>这段模型输出未完成，可从最近运行恢复。</small>}</div>)}
      {running && <div className="message message-assistant"><div className="message-bubble" style={{ whiteSpace: 'pre-wrap' }}>{stream.text || <span><LoaderCircle size={14} className="spin" /> 正在等待真实模型输出</span>}</div></div>}
      {activeRun?.error && <div className="form-error" role="alert">{activeRun.error.code}：{activeRun.error.message}<Button size="sm" disabled={busy === 'resume'} onClick={() => void resume(activeRun.id)}>恢复最近运行</Button></div>}
      {activeSession?.approvals.map(approval => <div className="artifact-approval" key={approval.id}><strong>{approval.kind === 'artifact.promote' ? '讲义提升需要批准' : approval.kind === 'memory.delete' ? '删除记忆需要批准' : '保存记忆需要批准'}</strong><div><Button size="sm" disabled={busy === `approval-${approval.id}`} onClick={() => void resolveApproval(approval.id, true)}>批准</Button><Button size="sm" variant="ghost" disabled={busy === `approval-${approval.id}`} onClick={() => void resolveApproval(approval.id, false)}>取消</Button></div></div>)}
      <div ref={endRef} />
    </div>
    {!messages.some(message => message.role === 'user') && !running && <div className="tutor-suggestions"><button onClick={() => void submit(`请解释「${context}」的核心概念`)}>解释当前内容</button><button onClick={() => void submit('只给我一个理解当前内容的提示')}>只给一点提示</button><button onClick={() => void submit('依据当前学习内容出一道练习，并等待我回答')}>检查我的理解</button></div>}
    {running && <div className="agent-running"><LoaderCircle size={15} className="spin" /><span>第 {activeRun.steps} / 25 步</span><button disabled={activeRun.status === 'cancelling' || busy === 'cancel'} onClick={() => void cancel(activeRun.id)}><Square size={13} />取消</button></div>}
    <form className="tutor-composer" onSubmit={event => { event.preventDefault(); void submit() }}><textarea value={input} onChange={event => setInput(clampToBytes(event.target.value, inputBudget))} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit() } }} placeholder={mode === '反馈' ? '贴入你的答案，我会给出反馈…' : '提问，或描述你卡住的地方…'} aria-label="向知舟导师提问" rows={2} /><div className="composer-bottom"><span><MessageCircleQuestion size={14} />{mode}模式 · 原生后端实时输出</span><button type="submit" aria-label="发送问题" disabled={!input.trim() || running || busy === 'send' || busy === 'create'}><ArrowUp size={18} /></button></div></form>
    <div className="tutor-foot"><Lightbulb size={13} />导师会优先引导思考；工具调用与引用可在 Agent 工作台核查。</div>
  </div>
}

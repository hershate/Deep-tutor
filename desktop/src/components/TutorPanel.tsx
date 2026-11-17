import { useEffect, useRef, useState } from 'react'
import { ArrowUp, BookOpen, FileCheck2, Lightbulb, LoaderCircle, MessageCircleQuestion, RotateCcw, Sparkles, Square, X } from 'lucide-react'
import { IconButton, Button, Badge } from './UI'
import { clampToBytes, utf8Bytes } from '../limits'
import { useAgent } from '../backend/useAgent'
import type { AgentApproval } from '../backend/client'

const modes = ['讲解', '提示', '练习', '反馈']

// 115#1：审批卡片展示被批准内容——记忆键值/产物 ID 与 AgentCenter 的
// 同款卡片一致（AgentCenter.tsx ApprovalCard）；原先只有三种静态文案，
// 用户在不知道要保存/删除哪条记忆的情况下被要求批准
function approvalTitle(approval: AgentApproval) {
  return approval.kind === 'artifact.promote' ? '讲义提升需要批准' : approval.kind === 'memory.delete' ? '删除记忆需要批准' : '保存记忆需要批准'
}
function approvalPayload(approval: AgentApproval) {
  if (approval.kind === 'artifact.promote') return `产物 #${String(approval.payload.artifactId ?? '')} · 批准后创建新讲义版本`
  const key = String(approval.payload.key ?? '')
  return approval.kind === 'memory.delete' ? key : `${key}${approval.payload.value ? ` · ${String(approval.payload.value)}` : ''}`
}

// hintsOnly 由 App 传入：本组件不得再实例化 useBackendState，
// 否则抽屉打开期间会并行跑第二条 bootstrap 轮询与事件同步管道（09#1）
// 146#10：删除 compact prop 与 is-compact 类名——无 CSS 规则、无脚本消费，
// prop 的唯一效果是挂一个无任何视觉/行为影响的死类，误导维护者
export default function TutorPanel({ onClose, planId, contextLabel, hintsOnly = false }: { onClose?: () => void; planId?: string; contextLabel?: string; hintsOnly?: boolean }) {
  // 26#1：strictTitle——导师抽屉只认标题为「知舟导师」的会话。原先回退链
  // 是「标题匹配 → 列表首项」，工作台会话（标题为消息摘要）被当作导师
  // 会话复用，辅导内容带前缀写进无关的工作台会话并共扣预算
  const { plans, activeSession, activeRun, loading, error, actionError, busy, stream, createSession, send, cancel, resume, resolveApproval } = useAgent({ planId, sessionTitle: '知舟导师', strictTitle: true })
  const [input, setInput] = useState('')
  const [mode, setMode] = useState('讲解')
  const endRef = useRef<HTMLDivElement>(null)
  const running = activeRun?.status === 'running' || activeRun?.status === 'cancelling'
  // 139#1：预算/步数耗尽的终态不可恢复——后端 resume 入口同口径拦截
  const resumable = activeRun && ['failed', 'cancelled', 'interrupted'].includes(activeRun.status) && !['budget_exceeded', 'step_limit'].includes(activeRun.error?.code ?? '')
  const messages = activeSession?.messages.filter(message => message.role !== 'tool') ?? []
  const plan = plans.find(item => item.id === activeSession?.planId) ?? plans.find(item => item.id === planId)
  const context = contextLabel ?? plan?.goal ?? '通用学习问答（不绑定学习方案）'
  const workspaceBound = Boolean(planId || activeSession?.planId)
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
  const prefix = `【辅导模式：${mode}】${workspaceBound ? '请依据当前方案的真实内容辅导我，不要编造来源或学习效果。' : '这是未绑定学习方案的通用问答，请直接辅导我，不要虚构方案、来源或学习效果。'}${mode === '提示' ? '只给引导提示，不直接给完整答案。' : mode === '练习' ? '给出练习并等待我的回答，不要代替我作答。' : mode === '反馈' ? '针对我的答案指出具体依据、问题和下一步。' : '优先帮助我理解概念并检查我的理解。'}我的输入：`
  const inputBudget = 32000 - utf8Bytes(prefix)
  // 104#5：四模式前缀长度不同——贴近上限输入后切到更长前缀的模式时，
  // 已有内容不重算会使 prefix+input 超 32000；按新预算重钳
  useEffect(() => { setInput(current => clampToBytes(current, inputBudget)) }, [inputBudget])
  const submit = async (value = input) => {
    const clean = value.trim()
    if (!clean || running || busy === 'send' || busy === 'create') return
    const instruction = prefix + clean
    if (await send(instruction)) setInput('')
  }
  const newSession = async () => { await createSession('知舟导师', planId ?? null) }

  return <div className="tutor-panel">
    <div className="tutor-head"><div><span className="agent-mark"><Sparkles size={18} /></span><div><strong>知舟导师</strong><span><i />{error || actionError ? '后端操作失败' : '本地后端会话'}</span></div></div>{onClose && <IconButton label="关闭导师" onClick={onClose}><X size={18} /></IconButton>}</div>
    <div className="tutor-context"><BookOpen size={14} /><span>{context}</span><button disabled={running || busy === 'create'} onClick={() => void newSession()}><RotateCcw size={13} />新会话</button></div>
    <div className="tutor-modes">{modes.map(item => <button key={item} className={mode === item ? 'active' : ''} aria-pressed={mode === item} disabled={running} onClick={() => setMode(item)}>{item}</button>)}</div>
    {error && <div className="form-error" role="alert">{error}</div>}
    {/* 53#1：操作失败（新建/发送/审批等）与读取失败分列——真实原因留在
        界面上，不被「重新读取」之类的动作清掉 */}
    {actionError && <div className="form-error" role="alert">{actionError.text}</div>}
    <div className="tutor-messages" aria-live="polite">
      {loading && <div className="message message-assistant"><div className="message-bubble">正在读取真实会话…</div></div>}
      {/* 148#1：空态明示全部前置条件——原先只提「没有模型档案」，未采用方案的用户发送后才撞 workspace_unavailable */}
      {!loading && !messages.length && <div className="message message-assistant"><div className="message-bubble">请描述你想理解的内容。发送后，这里会显示模型通过原生后端返回的真实回答。无需先上传资料或采用学习方案；开始前只需在模型接入中心配置并启用一个供应商档案。</div></div>}
      {messages.map(message => <div className={`message message-${message.role}`} key={message.id}><div className="message-bubble" style={{ whiteSpace: 'pre-wrap' }}>{message.role === 'user' ? message.content.replace(/^【辅导模式：[^】]+】.*?我的输入：/s, '') : message.content || '模型本步请求了工具，详细结果可在 Agent 工作台查看。'}</div>{message.reasoning && <details className="thought-details"><summary>模型返回的推理内容</summary><p style={{ whiteSpace: 'pre-wrap' }}>{message.reasoning}</p></details>}{message.partial && <small>这段模型输出未完成，可从最近运行恢复。</small>}</div>)}
      {running && <div className="message message-assistant"><div className="message-bubble" style={{ whiteSpace: 'pre-wrap' }}>{stream.text || <span><LoaderCircle size={14} className="spin" /> 正在等待真实模型输出</span>}</div></div>}
      {/* 139#1：预算/步数耗尽的终态排除「恢复最近运行」——后端 resume 入口
          同口径拦截（budget_exceeded / step_limit），按钮可点必失败；与
          AgentCenter 的 122#2 口径一致 */}
      {activeRun?.error && <div className="form-error" role="alert">{activeRun.error.code}：{activeRun.error.message}{resumable && <Button size="sm" disabled={busy === 'resume'} onClick={() => void resume(activeRun.id)}>恢复最近运行</Button>}</div>}
      {/* 115#1：审批卡片展示被批准内容（记忆键值 / 产物 ID）——结构与
          AgentCenter 的 ApprovalCard 一致 */}
      {activeSession?.approvals.map(approval => <div className="artifact-approval" key={approval.id}><div><FileCheck2 size={19} /><div><strong>{approvalTitle(approval)}</strong><span>{approvalPayload(approval)}</span></div><Badge tone="amber">等待批准</Badge></div><div><Button size="sm" disabled={busy === `approval-${approval.id}`} onClick={() => void resolveApproval(approval.id, true)}>批准</Button><Button size="sm" variant="ghost" disabled={busy === `approval-${approval.id}`} onClick={() => void resolveApproval(approval.id, false)}>取消</Button></div><small>{approval.kind === 'artifact.promote' ? '提升前校验原文引用与 scratch 哈希；产物保留 24 小时。' : '未经批准不会写入或删除记忆。'}</small></div>)}
      <div ref={endRef} />
    </div>
    {/* 148#1：无已采用方案时建议按钮禁用并提示——原先一键即撞 workspace_unavailable */}
    {!messages.some(message => message.role === 'user') && !running && <div className="tutor-suggestions">{workspaceBound ? <><button onClick={() => void submit(`请解释「${context}」的核心概念`)}>解释当前内容</button><button onClick={() => void submit('只给我一个理解当前内容的提示')}>只给一点提示</button><button onClick={() => void submit('依据当前学习内容出一道练习，并等待我回答')}>检查我的理解</button></> : <><button onClick={() => void submit('请用适合零基础的方式解释什么是主动回忆')}>解释学习概念</button><button onClick={() => void submit('我不知道如何开始学习，请给我一个今天能完成的小步骤')}>找到第一步</button><button onClick={() => void submit('出一道基础练习并等待我回答，不要先给答案')}>检查我的理解</button></>}</div>}
    {running && <div className="agent-running"><LoaderCircle size={15} className="spin" /><span>第 {activeRun.steps} / 25 步</span><button disabled={activeRun.status === 'cancelling' || busy === 'cancel'} onClick={() => void cancel(activeRun.id)}><Square size={13} />取消</button></div>}
    <form className="tutor-composer" onSubmit={event => { event.preventDefault(); void submit() }}><textarea value={input} onChange={event => setInput(clampToBytes(event.target.value, inputBudget))} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit() } }} placeholder={mode === '反馈' ? '贴入你的答案，我会给出反馈…' : '提问，或描述你卡住的地方…'} aria-label="向知舟导师提问" rows={2} /><div className="composer-bottom"><span><MessageCircleQuestion size={14} />{mode}模式 · 原生后端实时输出</span><button type="submit" aria-label="发送问题" disabled={!input.trim() || running || busy === 'send' || busy === 'create'}><ArrowUp size={18} /></button></div></form>
    <div className="tutor-foot"><Lightbulb size={13} />导师会优先引导思考；工具调用与引用可在 Agent 工作台核查。</div>
  </div>
}

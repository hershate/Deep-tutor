import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowUp, BookOpen, Bot, Brain, Check, ChevronDown, ChevronRight, Code2, FileCheck2, FileText, History, LoaderCircle, MessageSquareText, Plus, Search, ShieldCheck, Sparkles, Square, Trash2, Wrench, X } from 'lucide-react'
import { Badge, Button, ConfirmDialog, IconButton, ProgressBar, SectionHeader } from '../components/UI'
import { useAgent } from '../backend/useAgent'
import type { AgentApproval, AgentMessage } from '../backend/client'

const toolCatalog = [
  ['workspace.read', '读取当前方案、讲义或 scratch 产物'],
  ['workspace.write', '将有效的讲义单元保存为 scratch'],
  ['workspace.edit', '对 scratch 讲义做唯一文本替换'],
  ['workspace.list', '列出阅读与 scratch 产物'],
  ['web.search', '公开检索并实际抓取验证候选网页'],
  ['web.fetch', '安全抓取公开网页并提取真实正文'],
  ['memory.save', '批准后保存学习偏好'],
  ['memory.recall', '按需召回已批准的学习偏好'],
  ['memory.delete', '批准后删除学习偏好'],
  ['history.load', '读取当前会话的真实历史'],
  ['history.search', '搜索真实会话与消息历史'],
  ['artifact.promote', '批准后提升为讲义新版本'],
]
const runStatus: Record<string, string> = { running: '运行中', cancelling: '正在取消', done: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' }
const toolStatus: Record<string, string> = { done: '完成', failed: '失败', committed: '已保存', approval_required: '等待审批' }

function toolResult(message: AgentMessage | undefined) {
  if (!message) return null
  try { return JSON.parse(message.content) as { status?: string; error?: { message?: string } } } catch { return null }
}

function ApprovalCard({ approval, busy, onResolve }: { approval: AgentApproval; busy: boolean; onResolve: (approved: boolean) => void }) {
  const artifact = approval.kind === 'artifact.promote'
  const removing = approval.kind === 'memory.delete'
  return <div className="artifact-approval">
    <div><FileCheck2 size={19} /><div><strong>{artifact ? '讲义产物提升' : removing ? '删除学习记忆' : '保存学习记忆'}</strong><span>{artifact ? `产物 #${String(approval.payload.artifactId)} · 批准后创建新讲义版本` : `${String(approval.payload.key ?? '')}${!removing ? ` · ${String(approval.payload.value ?? '')}` : ''}`}</span></div><Badge tone="amber">等待批准</Badge></div>
    <div><Button size="sm" disabled={busy} onClick={() => onResolve(true)}><Check size={15} />批准</Button><Button variant="ghost" size="sm" disabled={busy} onClick={() => onResolve(false)}><X size={15} />取消</Button></div>
    <small>{artifact ? '提升前校验原文引用与 scratch 哈希；产物保留 24 小时。' : '未经批准不会写入或删除记忆。'}</small>
  </div>
}

export default function AgentCenter({ notify }: { notify: (message: string) => void }) {
  const { sessions, plans, activeSession, activeId, activeRun, selectSession, loading, error, busy, stream, createSession, send, cancel, resume, remove, resolveApproval, retry } = useAgent()
  const [input, setInput] = useState('')
  const [rightTab, setRightTab] = useState('运行过程')
  const [sessionSearch, setSessionSearch] = useState('')
  const [newPlan, setNewPlan] = useState('')
  // 37#1：删除会话级联清空全部历史，不可恢复——确认后再执行（对齐 20#5 家族）
  const [deleteOpen, setDeleteOpen] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const running = activeRun?.status === 'running' || activeRun?.status === 'cancelling'
  const resumable = activeRun && ['failed', 'cancelled', 'interrupted'].includes(activeRun.status)
  const messages = useMemo(() => activeSession?.messages.filter(message => message.role !== 'tool') ?? [], [activeSession])
  const plan = plans.find(item => item.id === activeSession?.planId)
  // 32#18：搜索语料按 sessions 记忆化——原先每次击键都重拼全部消息全文
  const sessionCorpus = useMemo(() => sessions.map(session => `${session.title} ${session.messages.map(message => message.content).join(' ')}`.toLowerCase()), [sessions])
  const filteredSessions = useMemo(() => {
    const needle = sessionSearch.trim().toLowerCase()
    return sessions.filter((_, index) => sessionCorpus[index].includes(needle))
  }, [sessions, sessionCorpus, sessionSearch])
  const toolEvents = useMemo(() => {
    if (!activeSession) return []
    const messages = activeSession.messages
    const lastUser = messages.map(message => message.role).lastIndexOf('user')
    const recent = messages.slice(Math.max(0, lastUser))
    const results = new Map(recent.filter(message => message.role === 'tool').map(message => [message.toolCallId, message]))
    return recent.flatMap(message => message.toolCalls.map(call => {
      const result = toolResult(results.get(call.id))
      return { id: call.id, name: call.name, status: result?.status ?? 'waiting', detail: result?.error?.message ?? (result?.status === 'approval_required' ? '后端已创建真实审批，等待用户决定' : '工具结果已持久化，可从会话历史恢复') }
    }))
  }, [activeSession])
  const usedTokens = activeSession?.outputTokensUsed ?? 0
  const budget = activeSession?.budgetTokens ?? 0
  const budgetPercent = budget ? Math.min(100, usedTokens / budget * 100) : 0
  const totalTools = activeSession?.messages.reduce((total, message) => total + message.toolCalls.length, 0) ?? 0

  useEffect(() => { endRef.current?.scrollIntoView({ block: 'nearest' }) }, [messages.length, stream.text])
  const submit = async () => {
    const value = input.trim()
    if (!value || running || busy === 'send' || busy === 'create') return
    const sent = await send(value)
    if (sent) setInput('')
  }
  const create = async () => {
    const created = await createSession(undefined, newPlan || undefined)
    if (created) notify('Agent 会话已写入本地后端')
  }
  const removeSession = async () => {
    if (!activeSession) return
    if (await remove(activeSession.id)) notify('会话及其本地历史已删除')
  }
  const resolve = async (approval: AgentApproval, approved: boolean) => {
    if (await resolveApproval(approval.id, approved)) notify(approved ? approval.kind === 'artifact.promote' ? '产物已提升为正式讲义的新版本' : '记忆操作已由原生后端执行' : '审批已取消，未执行对应操作')
  }

  if (loading && !error) return <div className="page-loading"><LoaderCircle size={24} className="spin" />正在读取 Agent 会话与事件…</div>
  if (error && !sessions.length) return <div className="page-loading" role="alert"><h2>Agent 会话读取失败</h2><p>{error}</p><Button onClick={() => void retry()}>重新读取</Button></div>
  return <div className="agent-workspace-page">
    <aside className="agent-sessions">
      <div className="agent-sessions-head"><div><MessageSquareText size={18} /><strong>Agent 会话</strong></div><IconButton label="新建会话" onClick={() => void create()} disabled={busy === 'create'}><Plus size={18} /></IconButton></div>
      <div className="session-search"><Search size={15} /><input value={sessionSearch} onChange={event => setSessionSearch(event.target.value)} placeholder="搜索真实会话与消息…" /></div>
      <div className="session-list">{filteredSessions.map(session => <button key={session.id} className={activeId === session.id ? 'active' : ''} onClick={() => selectSession(session.id)}><MessageSquareText size={16} /><div><strong>{session.title}</strong><span>{new Date(session.updatedAt).toLocaleString('zh-CN')} · {session.messages.filter(message => message.role === 'user').length} 次提问</span></div><ChevronRight size={15} /></button>)}</div>
      {!sessions.length && <div className="inspector-empty"><MessageSquareText size={24} /><p>尚无真实会话。新建后，消息和运行事件会保存在本地。</p></div>}
      {!activeSession && <div style={{ padding: 12 }}><select aria-label="新会话学习方案" value={newPlan || plans[0]?.id || ''} onChange={event => setNewPlan(event.target.value)}><option value="" disabled>选择已采用方案</option>{plans.map(item => <option value={item.id} key={item.id}>{item.goal}</option>)}</select><Button size="sm" disabled={!plans.length || busy === 'create'} onClick={() => void create()}><Plus size={14} />创建真实会话</Button></div>}
      <div className="session-bottom"><History size={16} /><span>消息、工具结果和运行状态由 SQLite 保存；切回会话可续看真实事件。</span></div>
    </aside>

    <main className="agent-chat">
      <header><div><span className="agent-mark"><Sparkles size={18} /></span><div><h1>{activeSession?.title ?? 'Agent 学习工作台'}</h1><p>{activeSession ? `${plan?.goal ?? activeSession.planId} · ${activeSession.providerId}` : '需要已采用的学习方案和已配置的模型供应商'}</p></div></div><div><Badge tone={error ? 'amber' : 'green'}><i className="live-dot" />{error ? '后端连接异常' : '本地后端已连接'}</Badge><IconButton label="删除当前会话" disabled={!activeSession || running || busy === 'delete'} onClick={() => setDeleteOpen(true)}><Trash2 size={17} /></IconButton></div></header>
      {error && <div className="form-error" role="alert">{error}</div>}
      <div className="agent-message-scroll">
        {messages.map(message => <div className={`agent-message agent-${message.role}`} key={message.id}>{message.role === 'assistant' && <span className="agent-avatar"><Bot size={17} /></span>}<div className="agent-message-content"><div className="agent-bubble" style={{ whiteSpace: 'pre-wrap' }}>{message.content || (message.toolCalls.length ? `已请求 ${message.toolCalls.length} 个工具调用。` : '模型未返回文本。')}</div>{message.reasoning && <details className="thought-details"><summary><Brain size={14} />查看模型返回的推理内容<ChevronDown size={14} /></summary><p style={{ whiteSpace: 'pre-wrap' }}>{message.reasoning}</p></details>}{message.toolCalls.length > 0 && <div className="message-tools">{message.toolCalls.map(tool => <span key={tool.id}><Wrench size={12} />{tool.name}</span>)}</div>}{message.partial && <Badge tone="amber">未完成的模型输出</Badge>}</div></div>)}
        {running && (stream.text || stream.reasoning) && <div className="agent-message agent-assistant"><span className="agent-avatar"><Bot size={17} /></span><div className="agent-message-content"><div className="agent-bubble" style={{ whiteSpace: 'pre-wrap' }}>{stream.text || '模型正在返回推理或工具调用…'}</div>{stream.reasoning && <details className="thought-details"><summary><Brain size={14} />实时推理内容<ChevronDown size={14} /></summary><p style={{ whiteSpace: 'pre-wrap' }}>{stream.reasoning}</p></details>}</div></div>}
        {activeSession?.approvals.map(approval => <ApprovalCard key={approval.id} approval={approval} busy={busy === `approval-${approval.id}`} onResolve={approved => void resolve(approval, approved)} />)}
        {activeRun?.error && <div className="form-error" role="alert">{activeRun.error.code}：{activeRun.error.message}{resumable && <Button size="sm" disabled={busy === 'resume'} onClick={() => void resume(activeRun.id).then(ok => { if (ok) notify('已恢复运行；已完成的模型结果不会重复请求') })}>恢复运行</Button>}</div>}
        {running && <div className="agent-running"><LoaderCircle size={17} className="spin" /><span>Agent 正在执行真实工具循环（第 {activeRun.steps} / 25 步）</span><button disabled={activeRun.status === 'cancelling' || busy === 'cancel'} onClick={() => void cancel(activeRun.id).then(ok => { if (ok) notify('取消请求已提交，最终状态由后端保存') })}><Square size={13} />取消</button></div>}
        {!messages.length && !running && <div className="agent-starter"><span>从一个真实学习任务开始</span><div><button onClick={() => setInput('阅读当前方案，找出我最需要补齐的知识缺口')}><Search size={16} />分析当前方案的知识缺口</button><button onClick={() => setInput('使用 workspace.read 读取当前方案，并依据真实内容补充一个直觉案例')}><BookOpen size={16} />读取资料并补充案例</button><button onClick={() => setInput('把当前单元讲义改得更适合零基础学习者')}><FileText size={16} />编辑讲义并提交产物审批</button></div></div>}
        <div ref={endRef} />
      </div>
      <div className="agent-composer-wrap"><form className="agent-composer" onSubmit={event => { event.preventDefault(); void submit() }}><textarea rows={3} value={input} onChange={event => setInput(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit() } }} placeholder="让 Agent 阅读、检索或修改当前学习工作区…" /><div><span><ShieldCheck size={14} />记忆按需召回，不自动注入</span><button type="submit" disabled={!input.trim() || running || busy === 'send' || busy === 'create'}><ArrowUp size={18} /></button></div></form><div className="agent-cost-line"><span>本轮实际输出 {activeRun?.outputTokens ?? 0} tokens</span><span>会话输出预算 {usedTokens} / {budget || 0} tokens</span><ProgressBar value={budgetPercent} /></div></div>
    </main>

    <aside className="agent-inspector">
      <div className="inspector-tabs">{['运行过程', '工具', '预算'].map(item => <button key={item} className={rightTab === item ? 'active' : ''} onClick={() => setRightTab(item)}>{item}</button>)}</div>
      {rightTab === '运行过程' && <div className="run-inspector"><SectionHeader title="持久化工具调用" description={activeRun ? `run #${activeRun.id} · ${runStatus[activeRun.status] ?? activeRun.status}` : '尚无运行'} />{toolEvents.length ? <div className="tool-event-list">{toolEvents.map(event => <div key={event.id}><span>{event.status === 'failed' ? <X size={14} /> : event.status === 'waiting' ? <LoaderCircle size={14} className={running ? 'spin' : ''} /> : <Check size={14} />}</span><div><strong>{event.name}</strong><p>{event.detail}</p></div><Badge tone={event.status === 'failed' || event.status === 'waiting' ? 'amber' : 'neutral'}>{toolStatus[event.status] ?? '等待模型完成'}</Badge></div>)}</div> : <div className="inspector-empty"><Wrench size={24} /><p>发送指令后，这里显示后端实际完成的工具调用；未执行的工具不会显示为成功。</p></div>}<div className="run-lifecycle"><h3>真实产物生命周期</h3><div className="lifecycle-track"><span className={activeSession?.artifacts.length ? 'done' : ''}>scratch</span><i /><span className={activeSession?.artifacts.some(item => item.status === 'committed') ? 'done' : ''}>committed</span><i /><span className={activeSession?.artifacts.some(item => item.status === 'promoted') ? 'done' : ''}>promoted</span></div><p>{activeSession?.artifacts.length ? activeSession.artifacts.map(item => `${item.path} · ${item.status} · 单元 ${item.unitIndex}`).join('\n') : '尚无 scratch 产物。工作区修改经批准后才进入正式讲义。'}</p></div></div>}
      {rightTab === '工具' && <div className="tool-catalog"><SectionHeader title="12 个 Agent 工具" description="名称与参数由原生工具目录注册；搜索与网页抓取返回真实网络结果。" />{toolCatalog.map(([tool, description]) => <button key={tool} title={description} onClick={() => setInput(`请使用 ${tool} 帮我完成：`)}><Code2 size={15} /><span>{tool}</span><Plus size={14} /></button>)}<div className="security-note"><ShieldCheck size={16} />scratch 有路径限制与哈希校验；记忆写删和产物提升需要明确审批。网页访问执行 SSRF 防护，未验证的链接不会标为成功。注意：阅读工作区/资料、检索与抓取公开网页不需要逐次审批——被污染的网页内容可能诱导模型读取本地学习数据并经公开请求外发，请仅让它访问可信来源。</div></div>}
      {rightTab === '预算' && <div className="budget-inspector"><SectionHeader title="真实运行限制" description="Token 用量来自供应商响应；无价格表时不换算为美元。" /><div className="budget-item"><div><span>当前会话输出</span><strong>{usedTokens} / {budget || 0} tokens</strong></div><ProgressBar value={budgetPercent} /></div><div className="budget-item"><div><span>本次运行步数</span><strong>{activeRun?.steps ?? 0} / 25</strong></div><ProgressBar value={(activeRun?.steps ?? 0) / 25 * 100} tone="blue" /></div><div className="usage-breakdown"><h3>本次运行计量</h3><div><span>输入 tokens</span><strong>{activeRun?.inputTokens ?? 0}</strong></div><div><span>输出 tokens</span><strong>{activeRun?.outputTokens ?? 0}</strong></div><div><span>会话工具调用</span><strong>{totalTools}</strong></div></div><p>达到输出 Token 预算后停止新请求；单步最多 8 个工具调用。异常与取消时已返回的 Token 仍计入。</p></div>}
    </aside>

    <ConfirmDialog open={deleteOpen} onClose={() => setDeleteOpen(false)} onConfirm={() => { setDeleteOpen(false); void removeSession() }} title="删除这个会话？" description="将同时删除该会话的全部消息、工具结果、运行与审批历史；此操作不可恢复。" confirmLabel="确认删除" busy={busy === 'delete'} />
  </div>
}

import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowUp, BookOpen, Check, ChevronRight, CircleAlert, FileText, History, Search, ShieldCheck, Trash2 } from 'lucide-react'
import type { Resource } from '../data'
import { Badge, Button, ConfirmDialog, EmptyState, Modal } from './UI'
import { clampToBytes } from '../limits'
import { statusName } from '../status'
import { backend, backendErrorMessage, onBackendEvent, reportBackendError, type MaterialDetail, isCancelRace, deltaThrottle } from '../backend/client'
import { knowledgeBackend, type KnowledgeCitation, type KnowledgeHit, type KnowledgeSession } from '../backend/knowledge'

export default function WikiPanel({ resources, notify }: { resources: Resource[]; notify: (message: string) => void }) {
  const [selected, setSelected] = useState<number[]>(() => resources.filter(resource => resource.progress === 100).slice(0, 2).map(resource => resource.id))
  const [question, setQuestion] = useState('')
  const [sessions, setSessions] = useState<KnowledgeSession[]>([])
  const [current, setCurrent] = useState<KnowledgeSession | null>(null)
  const [output, setOutput] = useState('')
  const [busy, setBusy] = useState(false)
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<KnowledgeHit[]>([])
  const [searching, setSearching] = useState(false)
  const [reading, setReading] = useState<{ citation: KnowledgeCitation | null; detail: MaterialDetail } | null>(null)
  const [noteHit, setNoteHit] = useState<KnowledgeHit | null>(null)
  const currentId = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    const items = await knowledgeBackend.list()
    setSessions(items)
    // 32#23：get 在途期间用户可能已切到其他会话——按请求时的 id 复查再 set
    if (currentId.current) {
      const requested = currentId.current
      const next = await knowledgeBackend.get(requested)
      if (currentId.current === requested) setCurrent(next)
    }
  }, [])
  useEffect(() => {
    void refresh().catch(reportBackendError)
    let unlisten: (() => void) | undefined
    // 32#5：晚解析守卫——卸载早于 listen() resolve 时立即注销
    let disposed = false
    onBackendEvent(event => {
      if (event.kind.startsWith('knowledge.')) void refresh().catch(reportBackendError)
    }).then(stop => { if (disposed) stop(); else unlisten = stop }).catch(reportBackendError)
    return () => { disposed = true; unlisten?.() }
  }, [refresh])
  useEffect(() => {
    setSelected(previous => previous.filter(id => resources.some(resource => resource.id === id)))
  }, [resources])
  useEffect(() => {
    const requestId = current?.requestId
    if (!requestId || !current || !['running', 'cancelling'].includes(current.status)) { setOutput(''); return }
    let active = true
    const update = () => {
      void backend.getProviderChat(requestId).then(request => { if (active) setOutput(request.output) }).catch(error => {
        if (typeof error !== 'object' || error === null || !('code' in error) || error.code !== 'not_found') reportBackendError(error)
      })
    }
    update()
    let unlisten: (() => void) | undefined
    // 32#5：晚解析守卫——卸载早于 listen() resolve 时立即注销
    let disposed = false
    // failover 后 delta 携带 {requestId}-fN 上游 id：记录 route_attempt 映射再对齐逻辑 id
    const upstreams = new Map<string, string>()
    // 每条 delta 不再各拉一次全量输出：150ms 窗口内合并为一次
    const throttledUpdate = deltaThrottle()
    onBackendEvent(event => {
      if (event.kind === 'provider.route_attempt' && typeof event.payload.requestId === 'string' && typeof event.payload.upstreamRequestId === 'string' && event.payload.requestId === requestId) upstreams.set(event.payload.upstreamRequestId, requestId)
      if (event.kind === 'provider.chat_delta' && typeof event.payload.requestId === 'string' && (event.payload.requestId === requestId || upstreams.get(event.payload.requestId) === requestId)) throttledUpdate(update)
    }).then(stop => { if (disposed) stop(); else unlisten = stop }).catch(reportBackendError)
    return () => { active = false; disposed = true; unlisten?.() }
  }, [current?.requestId, current?.status])

  const choose = (session: KnowledgeSession) => {
    currentId.current = session.id
    setCurrent(session)
  }
  const ask = async () => {
    if (busy || !question.trim() || !selected.length) return
    setBusy(true)
    try {
      choose(await knowledgeBackend.ask(question.trim(), selected))
      setQuestion('')
      await refresh()
      notify('问答任务已保存，等待真实模型输出')
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const cancel = async () => {
    if (!current || busy) return
    setBusy(true)
    try { choose(await knowledgeBackend.cancel(current.id)); notify('已请求取消，原始问题与资料范围已保留') }
    catch (error) { if (!isCancelRace(error)) reportBackendError(error) } finally { setBusy(false) }
  }
  const resume = async () => {
    if (!current || busy) return
    setBusy(true)
    try { choose(await knowledgeBackend.resume(current.id)); notify('已从保存的问答状态恢复') }
    // 29#2：answer_active 为"已在恢复"的窄窗口竞态码
    catch (error) { if (!isCancelRace(error)) reportBackendError(error) } finally { setBusy(false) }
  }
  const remove = async (id: number) => {
    // 32#10：busy 守卫——refresh 完成前二次命中会弹 not_found 伪错误
    if (busy) return
    setBusy(true)
    try {
      await knowledgeBackend.remove(id)
      if (currentId.current === id) { currentId.current = null; setCurrent(null) }
      await refresh()
      notify('这条真实问答历史已删除')
    } catch (error) { reportBackendError(error, 'write') } finally { setBusy(false) }
  }
  // 20#5：清空问答历史不可恢复——补确认
  const [clearOpen, setClearOpen] = useState(false)
  // 43#6：单条删除同为不可恢复操作（历史侧栏 13px 小图标紧邻会话切换按钮，
  // 误触即丢整条问答）——与清空历史同护栏
  const [removeTarget, setRemoveTarget] = useState<KnowledgeSession | null>(null)
  const clear = async () => {
    try {
      await knowledgeBackend.clearHistory()
      currentId.current = null; setCurrent(null); setSessions([])
      notify('Wiki 历史已由原生后端清空')
    } catch (error) { reportBackendError(error, 'write') }
  }
  const search = async () => {
    if (searching || !query.trim()) return
    setSearching(true)
    try { setHits(await knowledgeBackend.search(query)) }
    catch (error) { reportBackendError(error) } finally { setSearching(false) }
  }
  const openCitation = async (citation: KnowledgeCitation) => {
    try {
      const detail = await backend.getMaterial(citation.materialId)
      if (!detail.text?.includes(citation.quote)) throw new Error('引用已无法在当前解析文本中定位')
      setReading({ citation, detail })
    } catch (error) { reportBackendError(error) }
  }
  const openHit = async (hit: KnowledgeHit) => {
    if (hit.kind === 'note') { setNoteHit(hit); return }
    if (hit.citation) { await openCitation(hit.citation); return }
    if (hit.materialId) {
      try { setReading({ citation: null, detail: await backend.getMaterial(hit.materialId) }) }
      catch (error) { reportBackendError(error) }
    }
  }

  return <div className="wiki-layout"><aside className="wiki-docs"><div className="wiki-docs-head"><div><BookOpen size={18} /><strong>引用范围</strong></div><Badge tone="neutral">{selected.length} 份</Badge></div><p>仅使用选中的已解析资料（最多 8 份，解析中的资料不可选）；每次问答会保存实际范围。</p>{resources.map(resource => <label key={resource.id} className={selected.includes(resource.id) ? 'selected' : ''}><input type="checkbox" disabled={!selected.includes(resource.id) && (resource.progress !== 100 || selected.length >= 8)} checked={selected.includes(resource.id)} onChange={() => setSelected(selected.includes(resource.id) ? selected.filter(id => id !== resource.id) : [...selected, resource.id])} /><FileText size={17} /><div><strong>{resource.title}</strong><span>{resource.type} · {resource.progress === 100 ? '已解析' : `解析进度 ${resource.progress}%`}</span></div></label>)}<div className="wiki-policy"><ShieldCheck size={16} />模型引用必须能逐字定位到所选资料，否则不会保存为有效回答。</div></aside><main className="wiki-qa">
    <form className="wiki-search" onSubmit={event => { event.preventDefault(); void search() }}><Search size={17} /><input value={query} onChange={event => setQuery(clampToBytes(event.target.value, 200))} placeholder="检索真实笔记标题、正文与资料解析文本" aria-label="检索知识库" /><Button size="sm" disabled={searching || !query.trim()}>检索</Button></form>
    {query && <div className="knowledge-search-results">{hits.map(hit => <button key={`${hit.kind}-${hit.id}`} onClick={() => void openHit(hit)}><Badge tone="neutral">{hit.kind === 'material' ? '资料' : '笔记'}</Badge><div><strong>{hit.title}</strong><p>{hit.excerpt || '标题匹配，尚未产生解析正文。'}</p><small>实际匹配 {hit.score} 次{hit.citation ? ` · 第 ${hit.citation.line} 行` : ''}</small></div><ChevronRight size={16} /></button>)}</div>}
    <div className="wiki-answer-area">{current ? <article className="wiki-answer"><Badge tone={current.sufficient === false ? 'amber' : 'green'}>{current.status === 'done' ? current.sufficient === false ? '资料不足' : '库内引用回答' : '真实问答任务'}</Badge><h2>{current.question}</h2>
      {['running', 'cancelling'].includes(current.status) && <><pre className="streaming-output">{output || '问题与资料范围已保存，等待真实模型输出…'}</pre><Button variant="secondary" onClick={() => void cancel()} disabled={busy || current.status === 'cancelling'}>取消问答</Button></>}
      {['failed', 'cancelled', 'interrupted'].includes(current.status) && <div className="answer-feedback is-wrong"><CircleAlert size={20} /><p>{current.error ? backendErrorMessage(current.error) : '问答尚未完成'}</p><Button onClick={() => void resume()} disabled={busy}>恢复问答</Button></div>}
      {current.status === 'done' && <><p style={{ whiteSpace: 'pre-wrap' }}>{current.answer}</p>{current.warning && <p className="setting-note">{current.warning}</p>}<div className="wiki-citations">{current.citations.map((citation, index) => <button key={`${citation.materialId}-${citation.charStart}`} onClick={() => void openCitation(citation)}><span>[{index + 1}]</span><div><strong>{citation.title}</strong><small>第 {citation.line} 行 · 字符 {citation.charStart}–{citation.charEnd}</small><p>{citation.quote}</p></div><ChevronRight size={15} /></button>)}</div><div className="wiki-answer-note"><Check size={15} />{current.citations.length ? `${current.citations.length} 条引用均已定位到实际解析文本` : '本次回答没有可定位引用，请核对资料是否足够。'}</div></>}
      <div className="row-actions"><span>实际范围：{current.sources.map(source => source.title).join('、')}</span><Button variant="ghost" size="sm" onClick={() => setRemoveTarget(current)} disabled={['running', 'cancelling'].includes(current.status)}><Trash2 size={14} />删除此条</Button></div>
    </article> : <EmptyState title="向你的真实知识库提问" description="选择已解析资料，再输入问题。回答、引用位置与历史都由原生后端保存。" />}</div>
    <form className="wiki-composer" onSubmit={event => { event.preventDefault(); void ask() }}><textarea rows={3} value={question} onChange={event => setQuestion(clampToBytes(event.target.value, 8000))} placeholder="例如：根据所选资料，先验概率是什么？" /><div><span>{selected.length ? `基于 ${selected.length} 份实际资料作答` : '请至少选择一份资料'}</span><Button type="submit" disabled={busy || !question.trim() || !selected.length}><ArrowUp size={17} />提问</Button></div></form>
  </main><aside className="wiki-history"><div><History size={17} /><strong>真实问答历史</strong></div>{sessions.map(item => <div key={item.id}><button onClick={() => choose(item)}><strong>{item.question}</strong><span>{item.citations.length} 条引用 · 第 {item.attempt} 次尝试 · {statusName(item.status)}</span></button><button aria-label={`删除问答 ${item.question}`} onClick={() => setRemoveTarget(item)} disabled={['running', 'cancelling'].includes(item.status)}><Trash2 size={13} /></button></div>)}{sessions.length > 0 && <Button variant="ghost" size="sm" onClick={() => setClearOpen(true)}><Trash2 size={15} />清空历史</Button>}<ConfirmDialog open={clearOpen} onClose={() => setClearOpen(false)} onConfirm={() => { setClearOpen(false); void clear() }} title="清空问答历史？" description="将删除全部 Wiki 问答会话与引用记录；此操作不可恢复。" confirmLabel="确认清空" /><ConfirmDialog open={removeTarget !== null} onClose={() => setRemoveTarget(null)} onConfirm={() => { const target = removeTarget; setRemoveTarget(null); if (target) void remove(target.id) }} title="删除这条问答？" description={`「${removeTarget?.question ?? ''}」的回答与引用定位记录将一并删除；此操作不可恢复。`} confirmLabel="确认删除" busy={busy} /></aside>

    <Modal open={reading !== null} onClose={() => setReading(null)} title={reading?.detail.title ?? '资料原文'} wide>{reading && <div><p className="form-help">{reading.citation ? `第 ${reading.citation.line} 行 · 字符 ${reading.citation.charStart}–${reading.citation.charEnd}` : '实际解析文本'} · SHA-256 {reading.detail.sha256}</p><pre className="citation-source-text">{reading.citation ? reading.detail.text?.split(reading.citation.quote).map((part, index, parts) => <span key={index}>{part}{index < parts.length - 1 && <mark>{reading.citation!.quote}</mark>}</span>) : reading.detail.text ?? '尚未产生解析文本。'}</pre></div>}</Modal>
    <Modal open={noteHit !== null} onClose={() => setNoteHit(null)} title={noteHit?.title ?? '笔记检索结果'}><p>{noteHit?.excerpt}</p><p className="form-help">来自笔记 #{noteHit?.noteId} · 实际匹配 {noteHit?.score} 次</p></Modal>
  </div>
}

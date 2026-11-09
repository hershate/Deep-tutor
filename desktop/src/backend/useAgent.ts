import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { backend, backendErrorMessage, onBackendEvent, isCancelRace } from './client'
import type { AgentRun, AgentSession, LearningPlan } from './client'

type Options = { planId?: string; sessionTitle?: string; enabled?: boolean }
type Stream = { requestId: string; text: string; reasoning: string }
const emptyStream: Stream = { requestId: '', text: '', reasoning: '' }

function numberField(value: unknown) {
  return typeof value === 'number' && Number.isSafeInteger(value) ? value : null
}
function stringField(value: unknown) {
  return typeof value === 'string' ? value : null
}

export function useAgent({ planId, sessionTitle = '新学习会话', enabled = true }: Options = {}) {
  const [sessions, setSessions] = useState<AgentSession[]>([])
  const [plans, setPlans] = useState<LearningPlan[]>([])
  const [activeId, setActiveId] = useState<number | null>(null)
  const [loading, setLoading] = useState(enabled)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [liveRequestId, setLiveRequestId] = useState('')
  const [stream, setStream] = useState<Stream>(emptyStream)
  const activeIdRef = useRef<number | null>(null)
  const plansRef = useRef<LearningPlan[]>([])
  const liveRequestRef = useRef('')
  // 逻辑请求 id 与当前上游显示 id 分开记录：failover 后上游 id 变为 {logical}-fN，
  // 而 route_attempt/chat_delta 事件携带的 requestId 仍是逻辑 id，两者靠映射对齐
  const liveLogicalRef = useRef('')
  const routeUpstreams = useRef(new Map<string, string>())
  const runOwners = useRef(new Map<number, number>())

  const rememberRuns = useCallback((items: AgentSession[]) => {
    for (const session of items) for (const run of session.runs) runOwners.current.set(run.id, session.id)
  }, [])
  const selectSession = useCallback((id: number | null) => {
    activeIdRef.current = id
    setActiveId(id)
    setLiveRequest('')
    liveLogicalRef.current = ''
    routeUpstreams.current.clear()
    setStream(emptyStream)
    setError('')
  }, [])
  const setLiveRequest = useCallback((id: string) => {
    liveRequestRef.current = id
    setLiveRequestId(id)
  }, [])
  const setLiveLogical = useCallback((id: string) => {
    liveLogicalRef.current = id
    routeUpstreams.current.clear()
    liveRequestRef.current = id
    setLiveRequestId(id)
  }, [])
  const clearLive = useCallback(() => {
    liveLogicalRef.current = ''
    routeUpstreams.current.clear()
    liveRequestRef.current = ''
    setLiveRequestId('')
  }, [setLiveRequest])
  const refreshSession = useCallback(async (id: number) => {
    try {
      const next = await backend.getAgentSession(String(id))
      rememberRuns([next])
      setSessions(current => current.some(item => item.id === id)
        ? current.map(item => item.id === id ? next : item)
        : [next, ...current])
      setError('')
    } catch (cause) { setError(backendErrorMessage(cause)) }
  }, [rememberRuns])
  const refresh = useCallback(async () => {
    if (!enabled) return
    try {
      const [items, learningPlans] = await Promise.all([backend.listAgentSessions(), backend.listLearningPlans()])
      const adopted = learningPlans.filter(item => item.adoptedProjectId !== null)
      const available = planId ? items.filter(item => item.planId === planId) : items
      rememberRuns(available)
      setSessions(available)
      plansRef.current = adopted
      setPlans(adopted)
      const next = activeIdRef.current && available.some(item => item.id === activeIdRef.current)
        ? activeIdRef.current
        : available.find(item => item.title === sessionTitle)?.id ?? available[0]?.id ?? null
      activeIdRef.current = next
      setActiveId(next)
      setError('')
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setLoading(false) }
  }, [enabled, planId, rememberRuns, sessionTitle])

  useEffect(() => { void refresh() }, [refresh])
  const activeSession = useMemo(() => sessions.find(item => item.id === activeId) ?? null, [sessions, activeId])
  const activeRun = useMemo(() => activeSession?.runs.find(run => run.status === 'running' || run.status === 'cancelling') ?? activeSession?.runs[0] ?? null, [activeSession])
  useEffect(() => {
    const live = activeRun?.status === 'running' || activeRun?.status === 'cancelling'
    const id = live ? activeRun.currentRequestId ?? '' : ''
    if (id && id !== liveLogicalRef.current) setLiveLogical(id)
    else if (!id && liveLogicalRef.current) clearLive()
  }, [activeRun?.id, activeRun?.currentRequestId, activeRun?.status, setLiveLogical, clearLive])

  // 24#3：delta 游标——快照请求期间到达的增量会让游标前进，晚到的快照
  // 不得覆盖已拼接的文本
  const deltaEpochRef = useRef(0)
  useEffect(() => {
    let current = true
    setStream(emptyStream)
    if (liveRequestId && (activeRun?.status === 'running' || activeRun?.status === 'cancelling')) {
      const epochAtFetch = deltaEpochRef.current
      void backend.getProviderChat(liveRequestId).then(request => {
        if (current && epochAtFetch === deltaEpochRef.current && (request.status === 'running' || request.status === 'cancelled' || request.status === 'failed')) {
          setStream({ requestId: liveRequestId, text: request.output, reasoning: request.reasoning })
        }
      }).catch(cause => {
        // 03#1：step_started 事件先于路由行落库到达时 not_found 是毫秒级竞态，
        // 流式正文随后由事件增量补齐，不当作错误横幅
        const code = typeof cause === 'object' && cause !== null && 'code' in cause ? String((cause as { code?: unknown }).code) : ''
        if (current && code !== 'not_found') setError(backendErrorMessage(cause))
      })
    }
    return () => { current = false }
  }, [liveRequestId, activeRun?.status])

  // 51#9：refresh/refreshSession 随 planId/sessionTitle 等变化 identity，原先
  // 依赖它们导致监听器反复卸载重挂，间隙到达的 agent.*/delta 事件被丢弃；
  // 回调经 ref 取最新，依赖只剩 enabled 与两个空依赖的稳定 setter
  const refreshRef = useRef(refresh)
  const refreshSessionRef = useRef(refreshSession)
  useEffect(() => { refreshRef.current = refresh; refreshSessionRef.current = refreshSession })
  useEffect(() => {
    if (!enabled) return
    let disposed = false
    let unlisten: (() => void) | undefined
    void onBackendEvent(event => {
      if (disposed) return
      if (event.kind === 'provider.route_attempt') {
        const requestId = stringField(event.payload.requestId)
        const upstreamRequestId = stringField(event.payload.upstreamRequestId)
        if (requestId && upstreamRequestId && requestId === liveLogicalRef.current) {
          routeUpstreams.current.set(upstreamRequestId, requestId)
          setLiveRequest(upstreamRequestId)
          setStream({ requestId: upstreamRequestId, text: '', reasoning: '' })
        }
        return
      }
      if (event.kind === 'provider.chat_delta') {
        const requestId = stringField(event.payload.requestId)
        const text = stringField(event.payload.text)
        if (requestId !== null && text !== null && (routeUpstreams.current.get(requestId) ?? requestId) === liveLogicalRef.current && liveLogicalRef.current) {
          deltaEpochRef.current += 1
          setStream(current => current.requestId === liveRequestRef.current
            ? event.payload.reasoning === true ? { ...current, reasoning: current.reasoning + text } : { ...current, text: current.text + text }
            : { requestId, text: event.payload.reasoning === true ? '' : text, reasoning: event.payload.reasoning === true ? text : '' })
        }
        return
      }
      if (!event.kind.startsWith('agent.')) return
      const runId = numberField(event.payload.runId)
      const eventSessionId = numberField(event.payload.sessionId)
      if (event.kind === 'agent.run_started' && runId !== null && eventSessionId !== null) runOwners.current.set(runId, eventSessionId)
      const sessionId = eventSessionId ?? (runId === null ? null : runOwners.current.get(runId))
      if (event.kind === 'agent.step_started' && runId !== null && sessionId === activeIdRef.current) {
        const requestId = stringField(event.payload.requestId) ?? ''
        setLiveLogical(requestId)
        setStream({ requestId, text: '', reasoning: '' })
      }
      if (event.kind === 'agent.message_saved' && numberField(event.payload.sessionId) === activeIdRef.current) setStream(current => ({ ...current, text: '', reasoning: '' }))
      if (event.kind === 'agent.approval_expired') void refreshRef.current()
      if (event.kind === 'agent.session_created' || event.kind === 'agent.session_deleted') void refreshRef.current()
      else if (sessionId !== null && sessionId === activeIdRef.current) void refreshSessionRef.current(sessionId)
    }).then(remove => { if (disposed) remove(); else unlisten = remove }).catch(cause => setError(backendErrorMessage(cause)))
    return () => { disposed = true; unlisten?.() }
  }, [enabled, setLiveLogical, setLiveRequest])

  const createSession = useCallback(async (title?: string, requestedPlanId?: string): Promise<AgentSession | null> => {
    setBusy('create')
    setError('')
    try {
      const selectedPlan = requestedPlanId ?? planId ?? plansRef.current[0]?.id
      const created = await backend.createAgentSession({ title: title ?? sessionTitle, planId: selectedPlan })
      rememberRuns([created])
      setSessions(current => [created, ...current.filter(item => item.id !== created.id)])
      selectSession(created.id)
      return created
    } catch (cause) { setError(backendErrorMessage(cause)); return null } finally { setBusy('') }
  }, [planId, rememberRuns, selectSession, sessionTitle])

  const send = useCallback(async (content: string): Promise<AgentRun | null> => {
    const value = content.trim()
    if (!value) return null
    let id = activeIdRef.current
    if (!id) {
      const created = await createSession()
      id = created?.id ?? null
    }
    if (!id) return null
    setBusy('send')
    setError('')
    try {
      const run = await backend.sendAgentMessage(String(id), value)
      runOwners.current.set(run.id, id)
      await refreshSession(id)
      return run
    } catch (cause) { setError(backendErrorMessage(cause)); return null } finally { setBusy('') }
  }, [createSession, refreshSession])

  const cancel = useCallback(async (id: number) => {
    setBusy('cancel')
    try { await backend.cancelAgentRun(String(id)); if (activeIdRef.current) await refreshSession(activeIdRef.current); return true }
    catch (cause) {
      // 任务已终态后的取消是刷新竞态：刷新会话即可，不作为错误提示
      if (!isCancelRace(cause)) setError(backendErrorMessage(cause))
      if (activeIdRef.current) await refreshSession(activeIdRef.current).catch(() => {})
      return false
    } finally { setBusy('') }
  }, [refreshSession])
  const resume = useCallback(async (id: number) => {
    setBusy('resume')
    try { await backend.resumeAgentRun(String(id)); if (activeIdRef.current) await refreshSession(activeIdRef.current); return true }
    // 29#2：run_active 是"内存中已在恢复"的窄窗口竞态码，静默即可
    catch (cause) { if (!isCancelRace(cause)) setError(backendErrorMessage(cause)); return false } finally { setBusy('') }
  }, [refreshSession])
  const remove = useCallback(async (id: number) => {
    setBusy('delete')
    try {
      await backend.deleteAgentSession(String(id))
      if (activeIdRef.current === id) selectSession(null)
      await refresh()
      return true
    } catch (cause) { setError(backendErrorMessage(cause)); return false } finally { setBusy('') }
  }, [refresh, selectSession])
  const resolveApproval = useCallback(async (id: number, approved: boolean) => {
    setBusy(`approval-${id}`)
    try {
      if (approved) await backend.approveAgentRequest(String(id))
      else await backend.cancelAgentApproval(String(id))
      if (activeIdRef.current) await refreshSession(activeIdRef.current)
      return true
    } catch (cause) {
      // 29#1：双击审批/两面板同时处理时 approval_resolved 表示已被正常处理
      if (!isCancelRace(cause)) setError(backendErrorMessage(cause))
      return false
    } finally { setBusy('') }
  }, [refreshSession])

  const retry = useCallback(async () => {
    setLoading(true)
    setError('')
    if (!enabled) { setLoading(false); return }
    await refresh()
  }, [enabled, refresh])

  return { sessions, plans, activeSession, activeId, activeRun, selectSession, loading, error, busy, stream, createSession, send, cancel, resume, remove, resolveApproval, refresh, retry }
}

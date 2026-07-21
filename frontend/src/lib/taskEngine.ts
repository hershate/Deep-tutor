// App 级任务引擎（iter 187 F2）：方案/材料生成 SSE 连接的常驻宿主。
// 连接从页面组件上收到本模块（单例，不进 React 树），页面改为订阅 store/tasks 渲染——
// 切页/刷新/关弹窗不再 abort 流、不丢任务可见性（后端 _finalize 持久化本就与连接解耦）。
//
// - live map：同页同任务单连接（防重复挂接）；多标签页各一路（后端扇出支持）
// - boot()：启动对账（GET /sessions reconcile）后自动 attach running 条目，刷新无感续看
// - 终态副作用集中：失效查询 + 打扰抑制 toast（用户正在该任务页则不 toast）
// - 启动即败（failFast：409 配额/402 预算/404）抛给调用方做引导；中途断流内部处理
//   （409 stale -> interrupted 可重试；重连耗尽 -> conn offline，后端仍在跑）
import type { QueryClient } from '@tanstack/react-query'
import {
  ApiError,
  cancelGeneration,
  clarifyStream,
  generateMaterialsStream,
  generateStream,
  getSession,
  listSessions,
  retryStream,
  type SessionStatusDetail,
  type SseReconnectOpts,
} from './api'
import { SseError } from './sse'
import { events as pluginEvents } from './plugins'
import {
  materialsTaskId,
  planTaskId,
  useTaskStore,
  type TaskEntry,
  type TaskPhase,
} from '../store/tasks'
import { toast } from '../components/Toast'

type NavigateFn = (path: string, opts?: { replace?: boolean }) => void
type StreamOnEvent = (event: string, data: Record<string, unknown>) => void
type StreamStart = (onEvent: StreamOnEvent, signal: AbortSignal, opts?: SseReconnectOpts) => Promise<unknown>

interface EngineDeps {
  navigate: NavigateFn
  queryClient: QueryClient
}

const live = new Map<string, AbortController>()
let deps: EngineDeps | null = null
let currentPath = '/'
let booted = false

const store = () => useTaskStore.getState()
const entryOf = (taskId: string): TaskEntry | undefined => store().tasks[taskId]

function invalidate(...keys: Array<Array<string | number>>): void {
  for (const queryKey of keys) void deps?.queryClient.invalidateQueries({ queryKey })
}

/** 用户是否正在该任务的进度页（在则不打扰：页面本身已展示终态，不发 toast）。 */
function isOnTaskPage(t: TaskEntry): boolean {
  if (t.kind === 'plan') {
    return currentPath === `/generate/${t.sessionId}` || currentPath === `/clarify/${t.sessionId}`
  }
  return currentPath === `/plans/${t.planId}`
}

/** 相位落定（done/clarify/error/cancelled/interrupted）的集中副作用：失效查询 + 打扰抑制 toast。 */
function notifySettled(t: TaskEntry): void {
  invalidate(['sessions'])
  // iter 233 W2：方案进入终态 -> 该会话的澄清草稿使命终结（问题文本可能随重生成变化，
  // 旧草稿按旧问题键存储已无意义）。此前仅「提交成功」路径清除，放弃澄清/他端完成
  // 的草稿会在 localStorage 里按会话 id 逐条累积（无界键泄漏）。
  if (t.kind === 'plan' && t.sessionId != null) {
    try {
      localStorage.removeItem(`dt_clarify_draft_${t.sessionId}`)
    } catch {
      /* 隐私模式等：忽略 */
    }
  }
  const name = t.title || (t.kind === 'plan' ? '学习方案' : '学习材料')
  if (t.phase === 'done' && t.kind === 'plan') {
    invalidate(['plans'], ['usage'])
    if (!isOnTaskPage(t)) {
      toast.success(`「${name}」生成完成`, {
        label: '查看方案',
        onClick: () => deps?.navigate(t.resultPlanId ? `/plans/${t.resultPlanId}` : '/plans'),
      })
    }
  } else if (t.phase === 'clarify') {
    if (!isOnTaskPage(t)) {
      toast.info(`「${name}」需要回答几个问题才能继续`, {
        label: '去回答',
        onClick: () => deps?.navigate(`/clarify/${t.sessionId}`),
      })
    }
  } else if (t.phase === 'error') {
    if (t.kind === 'materials') invalidate(['plans'])
    if (!isOnTaskPage(t)) {
      toast.error(`「${name}」生成失败${t.errorMessage ? `：${t.errorMessage}` : ''}`, {
        label: '查看',
        onClick: () => deps?.navigate(t.kind === 'plan' ? `/generate/${t.sessionId}` : `/plans/${t.planId ?? ''}`),
      })
    }
  } else if (t.phase === 'interrupted') {
    if (!isOnTaskPage(t)) {
      toast.info(`「${name}」已中断，可重试`, {
        label: '查看',
        onClick: () => deps?.navigate(t.kind === 'plan' ? `/generate/${t.sessionId}` : `/plans/${t.planId ?? ''}`),
      })
    }
  } else if (t.phase === 'cancelled') {
    if (!isOnTaskPage(t)) toast.info(`「${name}」已取消`)
  } else if (t.phase === 'done' && t.kind === 'materials') {
    invalidate(['plans'], ['materials', t.planId ?? 0])
    if (!isOnTaskPage(t)) {
      toast.success(`「${name}」材料生成完成`, {
        label: '查看',
        onClick: () => deps?.navigate(`/plans/${t.planId ?? ''}?materials=1`),
      })
    }
  }
}

function onPhaseTransition(taskId: string, prev: TaskPhase | undefined, next: TaskPhase | undefined): void {
  if (!next || prev === next) return
  const t = entryOf(taskId)
  if (!t) return
  // iter 195 F10：插件 API taskchange 事件（v1.1.0）——每次相位迁移广播
  pluginEvents.emit('taskchange', { id: t.id, kind: t.kind, phase: t.phase, title: t.title })
  notifySettled(t)
}

/** 流异常内部处理（非 failFast 路径）：404 移除台账；409 stale -> interrupted；其余 -> offline。 */
function handleStreamError(taskId: string, e: unknown): void {
  const t = entryOf(taskId)
  if (!t) return
  if (e instanceof SseError) {
    if (e.status === 404) {
      store().remove(taskId) // 会话已删：台账同步移除（进度页自有 404 视图）
      return
    }
    if (e.status === 409) {
      // stale running（进程重启等）：后端已不在跑，标中断可重试
      store().update(taskId, {
        phase: 'interrupted',
        errorCode: 'stale',
        errorMessage: '服务已重启或生成已中断，可重试生成',
        conn: 'closed',
      })
      const nt = entryOf(taskId)
      if (nt) notifySettled(nt)
      return
    }
  }
  // 材料流的 SSE error 事件经 ApiError 上抛（携后端 code/message）-> 落任务失败
  if (e instanceof ApiError && e.code !== 'network_error' && e.code !== 'upstream_timeout') {
    store().update(taskId, { phase: 'error', errorCode: e.code, errorMessage: e.message, conn: 'closed' })
    const nt = entryOf(taskId)
    if (nt) notifySettled(nt)
    return
  }
  // 重连耗尽/网络错误：标离线（UI「连接中断」+ 重试连接）；相位保持 running（后端仍在跑）
  store().setConn(taskId, 'offline')
}

interface AttachOpts {
  initialLastEventId?: string
  /** 首事件前的启动错误（409 配额/402 预算/404/409 状态冲突）抛给调用方做引导。 */
  failFast?: boolean
  /** 任何 4xx 抛给调用方（clarify 页内联错误）。 */
  rethrow4xx?: boolean
  /** 收到首个流事件时回调（clarify 提交后「成功发起即跳进度页」的时机信号）。 */
  onFirstEvent?: () => void
}

/** 挂接一条 SSE 流到台账任务：事件 -> applyEvent，相位迁移 -> 副作用，异常按 opts 分流。 */
function attachStream(taskId: string, start: StreamStart, opts: AttachOpts = {}): Promise<void> {
  if (live.has(taskId)) return Promise.resolve()
  const ac = new AbortController()
  live.set(taskId, ac)
  store().setConn(taskId, 'live')
  let receivedAny = false
  return Promise.resolve(
    start(
      (event, data) => {
        if (!receivedAny) {
          receivedAny = true
          opts.onFirstEvent?.()
        }
        const prev = entryOf(taskId)?.phase
        store().applyEvent(taskId, event, data)
        onPhaseTransition(taskId, prev, entryOf(taskId)?.phase)
      },
      ac.signal,
      {
        initialLastEventId: opts.initialLastEventId,
        onEventId: (eid) => {
          const cur = entryOf(taskId)
          if (cur && cur.lastEventId !== eid) store().update(taskId, { lastEventId: eid })
        },
        onRetry: () => {
          if (entryOf(taskId)) store().setConn(taskId, 'reconnecting')
        },
      },
    ),
  )
    .then(() => undefined)
    .catch((e: unknown) => {
      if (ac.signal.aborted) return
      if (opts.failFast && !receivedAny) {
        if (entryOf(taskId)) store().setConn(taskId, 'closed')
        throw e
      }
      if (opts.rethrow4xx && e instanceof SseError && e.status !== undefined && e.status >= 400 && e.status < 500) {
        if (entryOf(taskId)) store().setConn(taskId, 'closed')
        throw e
      }
      handleStreamError(taskId, e)
    })
    .finally(() => {
      live.delete(taskId)
      const t = entryOf(taskId)
      // 流干净结束但相位仍 running 且连接未正常关闭（不应发生，兜底）-> 标离线让 UI 提示重连
      if (t && t.phase === 'running' && t.conn !== 'closed' && !ac.signal.aborted) {
        store().setConn(taskId, 'offline')
      }
    })
}

/** 发起/重试前重置条目为全新一次运行（清终态与易变字段，保留标题）。 */
function resetForNewRun(taskId: string): void {
  store().update(taskId, {
    phase: 'running',
    steps: [],
    cost: 0,
    tokens: 0,
    partialPlan: undefined,
    lastEventId: undefined,
    questions: undefined,
    errorCode: undefined,
    errorMessage: undefined,
    resultPlanId: undefined,
    startedAt: Date.now(),
  })
}

export const engine = {
  /** 注入 Router/QueryClient 能力（TaskEngineBridge 挂载时调用，幂等）。 */
  bind(d: EngineDeps): void {
    deps = d
  },

  setCurrentPath(path: string): void {
    currentPath = path
  },

  isLive(taskId: string): boolean {
    return live.has(taskId)
  },

  /** 启动对账：reconcile 服务端会话 -> running 条目自动 attach（刷新后无感续看）。 */
  async boot(): Promise<void> {
    if (booted) return
    booted = true
    try {
      const res = await listSessions({ limit: 50 })
      store().reconcile(res.items)
    } catch {
      return // 未登录/离线：静默（登录后页面刷新会再 boot）
    }
    for (const t of Object.values(store().tasks)) {
      if (t.kind === 'plan' && t.phase === 'running' && t.sessionId != null) {
        void engine.attach(t.sessionId).catch(() => undefined)
      }
    }
  },

  /**
   * 挂接会话生成流（判态后行动）：
   * - running -> 续接（重放 + 实时；游标取台账持久化的 lastEventId）
   * - paused_clarify -> 不回放流，直接以详情的 clarify_questions 落相位（等待用户回答）
   * - 终态 -> 静默对账落相位（不 toast：历史状态而非新事件）
   * - idle -> 不发起（防误触发新生成），交调用方展示引导视图
   */
  async attach(sessionId: number): Promise<SessionStatusDetail | null> {
    const id = planTaskId(sessionId)
    if (live.has(id)) return null
    let detail: SessionStatusDetail
    try {
      detail = await getSession(sessionId)
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) store().remove(id)
      throw e
    }
    if (detail.status === 'idle') return detail
    store().upsert({ id, kind: 'plan', sessionId, title: detail.title })
    if (detail.status === 'running') {
      void attachStream(id, (onEvent, signal, o) => generateStream(sessionId, '', onEvent, signal, o), {
        initialLastEventId: entryOf(id)?.lastEventId,
      })
      return detail
    }
    if (detail.status === 'paused_clarify') {
      store().update(id, { phase: 'clarify', questions: detail.clarify_questions ?? [], conn: 'closed' })
      return detail
    }
    // 终态静默对账（silent）：与 reconcile 同语义，不落 toast
    store().update(id, {
      phase: detail.error?.code === 'cancelled' ? 'cancelled' : (detail.status as TaskPhase),
      conn: 'closed',
      errorCode: detail.error?.code,
      errorMessage: detail.error?.message,
      resultPlanId: detail.latest_plan?.id ?? entryOf(id)?.resultPlanId,
    })
    return detail
  },

  /** 发起新一次方案生成（Home/F5「继续生成」）。启动即败抛给调用方（F9 引导矩阵）。 */
  async startPlan(sessionId: number, rawText: string, title = ''): Promise<void> {
    const id = planTaskId(sessionId)
    store().upsert({ id, kind: 'plan', sessionId, title, rawText })
    resetForNewRun(id)
    try {
      await attachStream(id, (onEvent, signal, o) => generateStream(sessionId, rawText, onEvent, signal, o), {
        failFast: true,
      })
    } catch (e) {
      // 启动即败（配额/预算/冲突）：会话仍是 idle。保留条目与 rawText（errorCode
      // start_rejected）——方案列表占位行/进度页 idle 视图的「继续生成」依赖它；
      // 重试入口对 start_rejected 走 startPlan 而非 retry（服务端 idle retry 必 409）
      store().update(id, {
        phase: 'interrupted',
        errorCode: 'start_rejected',
        errorMessage: '生成未开始（并发/预算限制），可稍后继续',
        conn: 'closed',
      })
      throw e
    }
  },

  /** 失败/中断后重试（B4 retry 端点；原始输入以服务端持久化为准）。 */
  async retry(sessionId: number): Promise<void> {
    const id = planTaskId(sessionId)
    const prev = entryOf(id)
    store().upsert({ id, kind: 'plan', sessionId })
    resetForNewRun(id)
    try {
      await attachStream(id, (onEvent, signal, o) => retryStream(sessionId, onEvent, signal, o), { failFast: true })
    } catch (e) {
      if (prev) {
        store().update(id, { phase: prev.phase, errorCode: prev.errorCode, errorMessage: prev.errorMessage, conn: 'closed' })
      } else {
        store().remove(id)
      }
      throw e
    }
  },

  /** 提交澄清回答（流与 generate 同构；409 状态冲突抛给调用方内联展示）。
   *  返回的 Promise 在「成功发起（收到首个流事件）」时兑现——调用方据此跳进度页；
   *  启动即败（409 等）则拒绝，流后续生命周期由引擎内部托管。 */
  async submitClarify(sessionId: number, answers: Record<string, unknown>): Promise<void> {
    const id = planTaskId(sessionId)
    store().upsert({ id, kind: 'plan', sessionId })
    store().update(id, { phase: 'running', questions: undefined })
    let onFirst: () => void = () => undefined
    const started = new Promise<void>((res) => {
      onFirst = res
    })
    const done = attachStream(id, (onEvent, signal) => clarifyStream(sessionId, answers, onEvent, signal), {
      rethrow4xx: true,
      onFirstEvent: () => onFirst(),
    })
    // race 之后 done 的拒绝已被 race 观察过一次；race 已兑现时的迟到拒绝补吞防空 unhandled
    const raced = Promise.race([started, done])
    void done.catch(() => undefined)
    await raced
  },

  /** 取消生成（后端取消编排器任务；本地流会经扇出收到 error{cancelled}）。 */
  async cancel(sessionId: number): Promise<void> {
    await cancelGeneration(sessionId) // 409 等抛给调用方
    const id = planTaskId(sessionId)
    const t = entryOf(id)
    // 本页未挂流（他页/任务中心发起的取消）-> 本地立即落相位；挂了流则等终态事件（同结果）
    if (t && t.phase === 'running' && !live.has(id)) {
      store().update(id, { phase: 'cancelled', errorCode: 'cancelled', conn: 'closed' })
      const nt = entryOf(id)
      if (nt) notifySettled(nt)
    }
  },

  /** 材料生成纳入引擎：关弹窗不再 abort，任务中心可见；幂等命中（sync）直接落 done。 */
  startMaterials(planId: number, opts: { depth: 'brief' | 'full'; refresh: boolean; unitIds?: number[]; title?: string }): void {
    const id = materialsTaskId(planId)
    if (live.has(id)) return
    store().upsert({ id, kind: 'materials', planId, title: opts.title ?? `材料 #${planId}`, phase: 'running' })
    store().update(id, { materialsUnits: [], materialsTotal: 0, errorCode: undefined, errorMessage: undefined })
    void attachStream(id, (onEvent, signal) =>
      generateMaterialsStream(
        planId,
        opts.depth,
        opts.refresh,
        {
          onUnitStart: (d) => onEvent('unit_start', d as unknown as Record<string, unknown>),
          onUnitDone: (d) => onEvent('unit_done', d as unknown as Record<string, unknown>),
          onDone: (d) => onEvent('done', d as unknown as Record<string, unknown>),
          onSync: () => onEvent('sync', {}),
        },
        signal,
        opts.unitIds,
      ),
    )
  },
}

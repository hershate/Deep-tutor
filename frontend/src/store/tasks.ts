// 全局任务台账（iter 186 F1）：学习方案/学习资料生成等长任务的统一账本。
// SSE 连接由 App 级任务引擎（lib/taskEngine.ts，iter 187 F2）持有，页面组件订阅本
// store 渲染——切页/刷新不再丢失任务可见性（此前进度锁在各页面组件内，卸载即丢）。
//
// 持久化边界：仅「恢复所需字段」落 localStorage('dt_tasks_v1')
// （rawText/lastEventId/questions/error/时间戳/resultPlanId/材料单元进度）；
// steps/cost/tokens 等易变数据靠 attach 时 SSE 重放重建（后端事件缓冲 + 多订阅扇出）。
// 台账非真相：状态/结果一律以 GET /sessions 与 SSE 重放为准，reconcile 单向服务端覆盖本地。
import { create } from 'zustand'

export type TaskKind = 'plan' | 'materials'
export type TaskPhase = 'running' | 'clarify' | 'done' | 'error' | 'cancelled' | 'interrupted'
export type TaskConn = 'live' | 'reconnecting' | 'offline' | 'closed'

export interface TaskStep {
  tool: string
  stepIndex: number
  totalSteps: number
  attempt: number
  status: 'running' | 'done' | 'error'
}

export interface MaterialsUnitProgress {
  order: number
  title: string
  status: 'running' | 'done' | 'degraded'
}

export interface TaskEntry {
  id: string // plan:<sessionId> | materials:<planId>
  kind: TaskKind
  sessionId?: number
  planId?: number
  title: string
  phase: TaskPhase
  steps: TaskStep[]
  cost: number
  tokens: number
  conn: TaskConn
  partialPlan?: unknown // plan_chunk 最新快照（「查看部分」弹层用，易变不持久化）
  // 材料任务进度（材料单元已逐单元落库，持久化它可让刷新后立即显示 x/total）
  materialsTotal?: number
  materialsUnits?: MaterialsUnitProgress[]
  // --- 恢复字段（持久化）---
  rawText?: string
  lastEventId?: string
  questions?: string[]
  errorCode?: string
  errorMessage?: string
  resultPlanId?: number
  startedAt: number
  updatedAt: number
}

/** GET /sessions 行（reconcile 输入；与后端 SessionStatusOut 对齐，lib/api.ts 同构返回）。 */
export interface SessionStatusItem {
  session_id: number
  title: string
  created_at: string
  status: string // idle|running|paused_clarify|done|error|interrupted
  updated_at: string | null
  has_materials: boolean
  latest_plan: { id: number; version: number; topic: string; created_at: string } | null
  error: { code?: string; message?: string } | null
  live: {
    current_tool: string | null
    step_index: number
    total_steps: number
    cost: number
    last_event_id: number
  } | null
}

export const planTaskId = (sessionId: number) => `plan:${sessionId}`
export const materialsTaskId = (planId: number) => `materials:${planId}`

const TERMINAL: readonly TaskPhase[] = ['done', 'error', 'cancelled', 'interrupted']
export const isTerminalPhase = (p: TaskPhase) => TERMINAL.includes(p)
/** 活动任务（任务中心计数/标题角标）：进行中或待用户澄清。 */
export const isActivePhase = (p: TaskPhase) => p === 'running' || p === 'clarify'

const STORAGE_KEY = 'dt_tasks_v1'

interface PersistedTask {
  id: string
  kind: TaskKind
  sessionId?: number
  planId?: number
  title: string
  phase: TaskPhase
  rawText?: string
  lastEventId?: string
  questions?: string[]
  errorCode?: string
  errorMessage?: string
  resultPlanId?: number
  materialsTotal?: number
  materialsUnits?: MaterialsUnitProgress[]
  startedAt: number
  updatedAt: number
}

function toPersisted(t: TaskEntry): PersistedTask {
  return {
    id: t.id,
    kind: t.kind,
    sessionId: t.sessionId,
    planId: t.planId,
    title: t.title,
    phase: t.phase,
    rawText: t.rawText,
    lastEventId: t.lastEventId,
    questions: t.questions,
    errorCode: t.errorCode,
    errorMessage: t.errorMessage,
    resultPlanId: t.resultPlanId,
    materialsTotal: t.materialsTotal,
    materialsUnits: t.materialsUnits,
    startedAt: t.startedAt,
    updatedAt: t.updatedAt,
  }
}

function fromPersisted(p: PersistedTask): TaskEntry {
  return {
    ...p,
    steps: [],
    cost: 0,
    tokens: 0,
    conn: 'closed', // 恢复后由引擎 attach 置 live/reconnecting
  }
}

function loadPersisted(): Record<string, TaskEntry> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const arr = JSON.parse(raw) as PersistedTask[]
    if (!Array.isArray(arr)) return {}
    const out: Record<string, TaskEntry> = {}
    for (const p of arr) {
      if (p && typeof p.id === 'string' && p.id) out[p.id] = fromPersisted(p)
    }
    return out
  } catch {
    return {}
  }
}

function persist(tasks: Record<string, TaskEntry>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.values(tasks).map(toPersisted)))
  } catch {
    /* 隐私模式等：台账仍在内存中工作 */
  }
}

export interface UpsertTaskInput {
  id: string
  kind: TaskKind
  sessionId?: number
  planId?: number
  title?: string
  phase?: TaskPhase
  rawText?: string
}

interface TasksState {
  tasks: Record<string, TaskEntry>
  /** 建/并条目：不存在的字段不动；updatedAt 由本 action 刷新。 */
  upsert: (input: UpsertTaskInput) => void
  /** 低层补丁（引擎专用；语义化状态迁移尽量走 applyEvent）。 */
  update: (id: string, patch: Partial<TaskEntry>) => void
  /** 按任务类型应用一个 SSE 事件（plan：tool_start/tool_end/plan_chunk/clarify/done/error 等；materials：unit_start/unit_done/done 等）。 */
  applyEvent: (id: string, event: string, data: Record<string, unknown>) => void
  setConn: (id: string, conn: TaskConn) => void
  remove: (id: string) => void
  /** 清空全部终态条目（「清空已完成记录」；只清台账不动服务端）。 */
  clearFinished: () => void
  /** 服务端对账（boot 时）：running/待澄清补轻量条目待 attach；终态单向覆盖本地非终态。 */
  reconcile: (items: SessionStatusItem[]) => void
}

function mkEntry(input: UpsertTaskInput, prev?: TaskEntry): TaskEntry {
  const now = Date.now()
  return {
    id: input.id,
    kind: input.kind,
    sessionId: input.sessionId ?? prev?.sessionId,
    planId: input.planId ?? prev?.planId,
    title: input.title ?? prev?.title ?? '',
    phase: input.phase ?? prev?.phase ?? 'running',
    steps: prev?.steps ?? [],
    cost: prev?.cost ?? 0,
    tokens: prev?.tokens ?? 0,
    conn: prev?.conn ?? 'closed',
    partialPlan: prev?.partialPlan,
    materialsTotal: prev?.materialsTotal,
    materialsUnits: prev?.materialsUnits,
    rawText: input.rawText ?? prev?.rawText,
    lastEventId: prev?.lastEventId,
    questions: prev?.questions,
    errorCode: prev?.errorCode,
    errorMessage: prev?.errorMessage,
    resultPlanId: prev?.resultPlanId,
    startedAt: prev?.startedAt ?? now,
    updatedAt: now,
  }
}

/** 服务端状态 -> 台账相位（null = idle 无需跟踪）。 */
function serverPhase(it: SessionStatusItem): TaskPhase | null {
  switch (it.status) {
    case 'running':
      return 'running'
    case 'paused_clarify':
      return 'clarify'
    case 'done':
      return 'done'
    case 'error':
      return it.error?.code === 'cancelled' ? 'cancelled' : 'error'
    case 'interrupted':
      return 'interrupted'
    default:
      return null
  }
}

export const useTaskStore = create<TasksState>((set, get) => ({
  tasks: loadPersisted(),

  upsert: (input) => {
    set((s) => ({ tasks: { ...s.tasks, [input.id]: mkEntry(input, s.tasks[input.id]) } }))
  },

  update: (id, patch) => {
    const cur = get().tasks[id]
    if (!cur) return
    set((s) => ({ tasks: { ...s.tasks, [id]: { ...cur, ...patch, id, updatedAt: Date.now() } } }))
  },

  applyEvent: (id, event, data) => {
    const cur = get().tasks[id]
    if (!cur) return
    const patch: Partial<TaskEntry> = {}
    if (cur.kind === 'plan') {
      if (event === 'tool_start') {
        const step: TaskStep = {
          tool: String(data.tool ?? ''),
          stepIndex: Number(data.step_index ?? 0),
          totalSteps: Number(data.total_steps ?? 0),
          attempt: Number(data.attempt ?? 1),
          status: 'running',
        }
        // 反馈循环重跑同工具 -> 追加新条目（attempt 递增，进度条可回退）
        const rest = cur.steps.filter((s) => !(s.tool === step.tool && s.attempt === step.attempt))
        patch.steps = [...rest, step]
      } else if (event === 'tool_end') {
        const tool = String(data.tool ?? '')
        const attempt = Number(data.attempt ?? 1)
        patch.steps = cur.steps.map((s) =>
          s.tool === tool && s.attempt === attempt
            ? { ...s, status: data.status === 'ok' ? 'done' : 'error' }
            : s,
        )
        const md = (data.metadata ?? {}) as Record<string, unknown>
        patch.cost = cur.cost + (Number(md.cost) || 0)
        patch.tokens = cur.tokens + (Number(md.input_tokens) || 0) + (Number(md.output_tokens) || 0)
      } else if (event === 'plan_chunk') {
        patch.partialPlan = data.partial_plan
      } else if (event === 'clarify_needed') {
        patch.phase = 'clarify'
        patch.questions = Array.isArray(data.questions) ? (data.questions as string[]) : []
        patch.conn = 'closed' // 澄清期间流暂停，等待用户回答
      } else if (event === 'budget_exceeded') {
        patch.phase = 'interrupted'
        patch.errorCode = 'budget_exceeded'
        patch.errorMessage = String(data.message ?? '预算超限，生成已中断')
        patch.conn = 'closed'
      } else if (event === 'error') {
        const code = String(data.code ?? 'error')
        patch.phase = code === 'cancelled' ? 'cancelled' : 'error'
        patch.errorCode = code
        patch.errorMessage = String(data.message ?? '')
        patch.conn = 'closed'
      } else if (event === 'done') {
        patch.phase = 'done'
        patch.resultPlanId = Number(data.plan_id) || undefined
        patch.conn = 'closed'
        const md = (data.metadata ?? {}) as Record<string, unknown>
        if (md.cost != null) patch.cost = Number(md.cost) || cur.cost // 终态费用为权威校准
      }
    } else {
      // materials：unit_start/unit_done/done/error/sync（幂等命中 JSON 由引擎映射为 sync）
      if (event === 'unit_start') {
        const order = Number(data.order ?? 0)
        const title = String(data.title ?? '')
        patch.materialsTotal = Number(data.total ?? cur.materialsTotal ?? 0)
        const units = (cur.materialsUnits ?? []).filter((u) => u.order !== order)
        const status: MaterialsUnitProgress['status'] = 'running'
        patch.materialsUnits = [...units, { order, title, status }].sort((a, b) => a.order - b.order)
      } else if (event === 'unit_done') {
        const order = Number(data.order ?? 0)
        patch.materialsUnits = (cur.materialsUnits ?? []).map((u) =>
          u.order === order ? { ...u, status: data.degraded ? 'degraded' : 'done' } : u,
        )
      } else if (event === 'done' || event === 'sync') {
        patch.phase = 'done'
        patch.conn = 'closed'
      } else if (event === 'error') {
        patch.phase = 'error'
        patch.errorCode = String(data.code ?? 'error')
        patch.errorMessage = String(data.message ?? '')
        patch.conn = 'closed'
      }
    }
    if (Object.keys(patch).length > 0) get().update(id, patch)
  },

  setConn: (id, conn) => get().update(id, { conn }),

  remove: (id) => {
    set((s) => {
      if (!(id in s.tasks)) return s
      const tasks = { ...s.tasks }
      delete tasks[id]
      return { tasks }
    })
  },

  clearFinished: () => {
    set((s) => {
      const tasks: Record<string, TaskEntry> = {}
      for (const [id, t] of Object.entries(s.tasks)) {
        if (!isTerminalPhase(t.phase)) tasks[id] = t
      }
      return { tasks }
    })
  },

  reconcile: (items) => {
    const now = Date.now()
    set((s) => {
      let changed = false
      const tasks = { ...s.tasks }
      for (const it of items) {
        const phase = serverPhase(it)
        if (phase === null) continue // idle：无状态行，不跟踪
        const id = planTaskId(it.session_id)
        const local = tasks[id]
        if (phase === 'running' || phase === 'clarify') {
          // 活动态：本地缺失 -> 补轻量条目待引擎 attach；存在 -> 服务端标题/相位优先
          if (!local) {
            tasks[id] = mkEntry({ id, kind: 'plan', sessionId: it.session_id, title: it.title, phase })
            changed = true
          } else if (local.phase !== phase || local.title !== it.title) {
            tasks[id] = {
              ...local,
              title: it.title || local.title,
              phase,
              // 重新进入活动态（他端重试）：清终态字段待重放重建
              ...(isTerminalPhase(local.phase) ? { errorCode: undefined, errorMessage: undefined, resultPlanId: undefined } : {}),
              updatedAt: now,
            }
            changed = true
          }
        } else if (local && !isTerminalPhase(local.phase)) {
          // 服务端终态 -> 单向覆盖本地非终态（他端完成/失败/取消，本端立即可见）
          tasks[id] = {
            ...local,
            title: it.title || local.title,
            phase,
            conn: 'closed',
            errorCode: it.error?.code,
            errorMessage: it.error?.message,
            resultPlanId: it.latest_plan?.id ?? local.resultPlanId,
            updatedAt: now,
          }
          changed = true
        }
        // 终态且本地无条目：不补（否则历史会话淹没台账；占位行由方案列表直接读服务端）
      }
      return changed ? { tasks } : s
    })
  },
}))

// 任意变更后持久化恢复字段（steps/cost 等易变数据不落盘，attach 重放重建）
useTaskStore.subscribe((s) => persist(s.tasks))

// 多标签页：他页写入 -> 按 updatedAt 大者合并恢复字段（各标签页各自挂 SSE，
// 后端扇出，故易变进度无需跨页同步；仅终态/恢复字段需要）。本页写入不触发本页事件，无回环。
if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key !== STORAGE_KEY || e.newValue == null) return
    let incoming: PersistedTask[]
    try {
      const arr = JSON.parse(e.newValue) as PersistedTask[]
      if (!Array.isArray(arr)) return
      incoming = arr
    } catch {
      return
    }
    useTaskStore.setState((state) => {
      let changed = false
      const tasks = { ...state.tasks }
      for (const p of incoming) {
        if (!p || typeof p.id !== 'string') continue
        const local = tasks[p.id]
        if (!local) {
          tasks[p.id] = fromPersisted(p) // 他页新建的任务 -> 本页补建
          changed = true
        } else if ((p.updatedAt ?? 0) > local.updatedAt) {
          // 仅采纳恢复字段；保留本页易变进度（本页引擎正在跑的 stream 更准）。
          // updatedAt 原样采纳（不再 bump）——否则两页互相视为「更新」而 ping-pong 回环
          tasks[p.id] = { ...local, ...fromPersisted(p), steps: local.steps, cost: local.cost, tokens: local.tokens, conn: local.conn, partialPlan: local.partialPlan }
          changed = true
        }
      }
      return changed ? { tasks } : state
    })
  })
}

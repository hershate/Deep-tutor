// API 客户端（plan/03 REST API）。
// 所有请求带 Authorization: Bearer 头（P4 多用户：localStorage JWT 优先，回退 VITE_AUTH_TOKEN，见 lib/auth.ts）。
// dev 环境经 Vite 代理 /api -> :8000；生产经 nginx 反代。

import { getToken, handleUnauthorized, setAuth, type AuthUser } from './auth'

const BASE = import.meta.env.VITE_API_BASE ?? '/api'

function token(): string {
  return getToken()
}

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) {
    super(message)
  }
}

/** 解析 SSE 事件 data（err.md D9：单条畸形 JSON 不再炸掉整个流/触发无谓重连）。 */
function parseSseData(raw: string): Record<string, unknown> {
  if (!raw) return {}
  try {
    const v = JSON.parse(raw) as unknown
    return v !== null && typeof v === 'object' ? (v as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

// err.md D15：apiFetch 默认超时（后端 hang 住时页面不再永远加载态）；
// 长任务端点（导出/测验等 LLM 链）经 init.timeoutMs 放宽。
const DEFAULT_TIMEOUT_MS = 120_000

export async function apiFetch<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const ac = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    ac.abort()
  }, init?.timeoutMs ?? DEFAULT_TIMEOUT_MS)
  // 合并调用方 signal（如组件卸载取消）
  const callerSignal = init?.signal
  if (callerSignal) {
    if (callerSignal.aborted) ac.abort()
    else callerSignal.addEventListener('abort', () => ac.abort(), { once: true })
  }
  let res: Response
  try {
    const { timeoutMs: _t, ...rest } = init ?? {}
    res = await fetch(`${BASE}${path}`, {
      ...rest,
      signal: ac.signal,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token()}`,
        ...(rest.headers ?? {}),
      },
    })
  } catch (e) {
    if (timedOut) throw new ApiError('upstream_timeout', '请求超时，请稍后重试', 504)
    if (callerSignal?.aborted) throw e // 调用方主动取消，原样上抛（页面按取消处理）
    throw new ApiError('network_error', '网络错误，请检查连接', 0)
  } finally {
    clearTimeout(timer)
  }
  if (!res.ok) {
    handleUnauthorized(res.status)
    const body = await res.json().catch(() => ({}))
    const err = body?.error ?? {}
    throw new ApiError(err.code ?? 'http_error', err.message ?? `HTTP ${res.status}`, res.status)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

// --- auth（P4 多用户，iter 59）---
// login 不走 apiFetch（无 token；401 为正常失败路径，不触发跳登录页）。
export async function login(username: string, password: string): Promise<AuthUser> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const err = body?.error ?? {}
    throw new ApiError(err.code ?? 'http_error', err.message ?? `HTTP ${res.status}`, res.status)
  }
  setAuth(body.token, body.user as AuthUser)
  return body.user as AuthUser
}

export const getMe = () => apiFetch<AuthUser>('/auth/me')

// --- 用户管理（admin，iter 59）---
export interface AdminUser extends AuthUser {
  created_at: string
}
export interface UserInput {
  username?: string
  password?: string
  name?: string
  is_admin?: boolean
}
export const listUsers = () => apiFetch<{ users: AdminUser[] }>('/users')
export const createUser = (body: Required<Pick<UserInput, 'username' | 'password'>> & UserInput) =>
  apiFetch<AdminUser>('/users', { method: 'POST', body: JSON.stringify(body) })
export const updateUser = (id: number, body: UserInput) =>
  apiFetch<AdminUser>(`/users/${id}`, { method: 'PUT', body: JSON.stringify(body) })
export const deleteUser = (id: number) => apiFetch<{ ok: boolean }>(`/users/${id}`, { method: 'DELETE' })

// --- sessions ---
export const createSession = (title = '') =>
  apiFetch<{ session_id: number; title: string }>('/sessions', {
    method: 'POST',
    body: JSON.stringify({ title }),
  })

export const deleteSession = (id: number) =>
  apiFetch<{ ok: boolean }>(`/sessions/${id}`, { method: 'DELETE' })

// --- materials ---
export const createTextMaterial = (sessionId: number, text: string) =>
  apiFetch<{ material_id: number; kind: string; meta: Record<string, unknown> }>('/materials/text', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, text }),
  })

// 网页链接资料（plan/03 §二 输入形式「网页链接」）：后端抓取+正文提取（trafilatura）
export const createUrlMaterial = (sessionId: number, url: string) =>
  apiFetch<{ material_id: number; kind: string; meta: Record<string, unknown> }>('/materials/url', {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, url }),
  })

// 大文件上传（plan/09 §七 前端边界：字节级进度）。用 XHR 而非 fetch——
// fetch 的上传进度（Request duplex stream）浏览器支持差，XHR upload.onprogress 全量可用。
export function uploadMaterial(
  sessionId: number,
  file: File,
  onProgress?: (percent: number) => void,
): Promise<{ material_id: number; kind: string; meta: Record<string, unknown> }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${BASE}/materials/upload`)
    xhr.setRequestHeader('Authorization', `Bearer ${token()}`)
    xhr.timeout = 10 * 60 * 1000 // err.md D15：大文件上传 10 分钟超时（此前永不超时）
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100))
    }
    xhr.onload = () => {
      const body = (() => {
        try {
          return JSON.parse(xhr.responseText)
        } catch {
          return {}
        }
      })()
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body)
      } else {
        handleUnauthorized(xhr.status)
        const err = body?.error ?? {}
        reject(new ApiError(err.code ?? 'http_error', err.message ?? `HTTP ${xhr.status}`, xhr.status))
      }
    }
    xhr.onerror = () => reject(new ApiError('network_error', '网络错误', 0))
    xhr.ontimeout = () => reject(new ApiError('upstream_timeout', '上传超时', 504))
    const form = new FormData()
    form.append('session_id', String(sessionId))
    form.append('file', file)
    xhr.send(form)
  })
}

// --- plans ---
export interface PlanSummary {
  id: number
  session_id: number
  version: number
  created_at: string
  topic: string
}
export const listPlans = (sessionId?: number, page = 1) => {
  // err.md D18：URLSearchParams 拼 query（此前手工插值，session_id 异常值可注入额外参数）
  const q = new URLSearchParams({ page: String(page) })
  if (sessionId) q.set('session_id', String(sessionId))
  return apiFetch<{ items: PlanSummary[]; total: number }>(`/plans?${q.toString()}`)
}

export const getPlan = (id: number) => apiFetch<unknown>(`/plans/${id}`)

export const exportPlan = (planId: number, format = 'md') =>
  apiFetch<{
    file_url: string
    format: string
    warning?: string
    slide_count?: number
    html?: string
    // P5：ics / anki 随响应返回内容，前端 Blob 下载；iter 73：md 同样随响应返回正文
    content?: string
    filename?: string
    ics?: string
    event_count?: number
    apkg_b64?: string
    card_count?: number
    pdf_b64?: string
  // err.md D15：导出走 LLM 链（slides 等），放宽至 5 分钟
  }>(`/export/${planId}`, { method: 'POST', body: JSON.stringify({ format }), timeoutMs: 300_000 })

// 自学材料（P2，plan/07 §五；iter 47：SSE 逐单元渐进显示）。
// 内容协商：Accept: text/event-stream 且需生成 -> SSE 流（unit_start/unit_done ×N -> done）；
// 幂等命中（已持久化未 refresh）仍返回 JSON -> onSync。一次性流，不做断线重连（后端同）。
export interface MaterialsSyncResult {
  unit_count: number
  markdown: string
  warning?: string
}
export interface MaterialsStreamCallbacks {
  onUnitStart?: (d: { order: number; title: string; total: number }) => void
  onUnitDone?: (d: { order: number; title: string; degraded: boolean }) => void
  onDone?: (d: { unit_count: number; degraded_count: number }) => void
  onSync?: (r: MaterialsSyncResult) => void // 幂等命中 JSON
}
export async function generateMaterialsStream(
  planId: number,
  depth: 'brief' | 'full',
  refresh: boolean,
  cb: MaterialsStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/plans/${planId}/materials`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token()}`,
      Accept: 'text/event-stream',
    },
    body: JSON.stringify({ depth, refresh }),
    signal,
  })
  if (!res.ok) {
    handleUnauthorized(res.status) // err.md D5：SSE 通道 401 同样触发登录跳转
    const body = await res.json().catch(() => ({}))
    const err = body?.error ?? {}
    throw new ApiError(err.code ?? 'http_error', err.message ?? `HTTP ${res.status}`, res.status)
  }
  const ctype = res.headers.get('content-type') ?? ''
  if (ctype.includes('application/json')) {
    cb.onSync?.((await res.json()) as MaterialsSyncResult) // 幂等命中：直接返回已存单元
    return
  }
  if (!res.body) throw new ApiError('http_error', 'SSE 响应无 body', res.status)
  await readSSEStream(res.body, (ev) => {
    const data = parseSseData(ev.data)
    const event = ev.event ?? 'message'
    if (event === 'unit_start') cb.onUnitStart?.(data as { order: number; title: string; total: number })
    else if (event === 'unit_done') cb.onUnitDone?.(data as { order: number; title: string; degraded: boolean })
    else if (event === 'done') cb.onDone?.(data as { unit_count: number; degraded_count: number })
    else if (event === 'error') throw new ApiError(String(data.code ?? 'error'), String(data.message ?? '材料生成失败'), res.status)
  })
}

// 测验（P2，plan/02 §8）：同步一次 LLM 调用 -> 题目 JSON（前端交互判分）
export interface QuizItemData {
  id?: number // quizzes 表 DB id（iter 42 持久化后必有；判分走 /quizzes/{id}/submit）
  node_id: string
  node_name: string
  type: 'choice' | 'fill' | 'short'
  question: string
  options: string[]
  answer: string
  explanation: string
}
export const generateQuiz = (planId: number, signal?: AbortSignal) =>
  apiFetch<{ title: string; quiz_count: number; quizzes: QuizItemData[]; warning?: string }>(
    `/plans/${planId}/quiz`,
    // err.md D15：测验为一次 LLM 调用 + 修复重试，放宽至 5 分钟
    { method: 'POST', body: JSON.stringify({}), signal, timeoutMs: 300_000 },
  )

// 提交作答（plan/03 §一）：choice 服务器判分；fill/short 自评 correct=null
export const submitQuiz = (quizId: number, userAnswer: Record<string, unknown>) =>
  apiFetch<{ correct: boolean | null; answer: string; explanation: string }>(
    `/quizzes/${quizId}/submit`,
    { method: 'POST', body: JSON.stringify({ user_answer: userAnswer }) },
  )

// --- progress（P2，plan/03 §一）---
export type ProgressStatus = 'not_started' | 'learning' | 'done'
export interface ProgressNode {
  node_id: string // knowledge_nodes.sid（plan 内容 knowledge_map 原始 id）
  name: string
  status: ProgressStatus
  mastery: number // 0-1
  next_review: string | null // ISO 日期；无进度行时为 null
}
export const getProgress = (planId: number) =>
  apiFetch<{ plan_id: number; nodes: ProgressNode[]; mastery_avg: number }>(`/progress/${planId}`)
export const updateProgress = (planId: number, nodeId: string, status: ProgressStatus, quizScore?: number) =>
  apiFetch<{ updated: boolean; warning?: string; status?: ProgressStatus; mastery?: number; next_review?: string }>(
    '/progress',
    {
      method: 'POST',
      body: JSON.stringify({
        plan_id: planId,
        node_id: nodeId,
        status,
        ...(quizScore !== undefined ? { quiz_score: quizScore } : {}),
      }),
    },
  )

// 自学单元（P2，plan/03 §一）：列表元数据 / 单元内容 / 标记已学（iter 43-44）
export interface StudyUnitMeta {
  id: number
  title: string
  objectives: string[]
  prerequisites: string[]
  estimated_time: string
  order: number
  done: boolean
}
export const listStudyUnits = (planId: number, signal?: AbortSignal) =>
  apiFetch<{ units: StudyUnitMeta[]; index_md?: string }>(`/plans/${planId}/materials`, { signal })
export interface UnitResource {
  title: string
  url: string
  verified: string // ok=已验证 | uncertain=不确定（同资源 ✅⚠️ 标记）
  note: string
}
export const getStudyUnitContent = (unitId: number, signal?: AbortSignal) =>
  apiFetch<{ unit_id: number; title: string; content_md: string; resources: UnitResource[] }>(
    `/materials/${unitId}`,
    { signal },
  )
export const markUnitDone = (unitId: number, done: boolean) =>
  apiFetch<{ unit_id: number; done: boolean }>(`/materials/${unitId}/done`, {
    method: 'POST',
    body: JSON.stringify({ done }),
  })

// --- learning logs 打卡（P2，plan/01 §四；学习中心页用）---
export interface LearningLogItem {
  id: number
  date: string // ISO 日期
  minutes: number
  note: string | null
}
export const listLearningLogs = (planId: number) =>
  apiFetch<{ logs: LearningLogItem[]; total_minutes: number }>(`/progress/logs/${planId}`)
export const addLearningLog = (planId: number, minutes: number, note = '') =>
  apiFetch<{ id: number; date: string; minutes: number; note: string | null }>('/progress/logs', {
    method: 'POST',
    body: JSON.stringify({ plan_id: planId, minutes, note: note || null }),
  })

// --- cancel（取消运行中生成，plan/10 §十一）---
export const cancelGeneration = (sessionId: number) =>
  apiFetch<{ ok: boolean; status: string }>(`/plans/${sessionId}/cancel`, { method: 'POST' })

// --- usage ---
export const getUsage = () =>
  apiFetch<{ total_tokens: number; total_cost: number; cache_hit_rate: number; calls: number }>('/usage')

// --- LLM 供应商档案（自定义供应商多档案切换，iter 51-52）---
// api_key 只写不读：响应仅含掩码末 4 位；更新时 api_key 留空 -> 保留原值。
export interface ProviderProfile {
  id: number
  name: string
  base_url: string // 空 = Anthropic 官方端点
  model: string // 空 = 服务端默认模型
  is_active: boolean
  api_key_masked: string
  created_at: string
}
export interface ProviderInput {
  name: string
  api_key?: string // 创建必填；更新可留空（保留原值）
  base_url?: string
  model?: string
}
export const listProviders = () => apiFetch<{ providers: ProviderProfile[] }>('/providers')
export const createProvider = (body: ProviderInput) =>
  apiFetch<ProviderProfile>('/providers', { method: 'POST', body: JSON.stringify(body) })
export const updateProvider = (id: number, body: ProviderInput) =>
  apiFetch<ProviderProfile>(`/providers/${id}`, { method: 'PUT', body: JSON.stringify(body) })
export const deleteProvider = (id: number) =>
  apiFetch<{ ok: boolean }>(`/providers/${id}`, { method: 'DELETE' })
export const activateProvider = (id: number) =>
  apiFetch<ProviderProfile>(`/providers/${id}/activate`, { method: 'POST', body: JSON.stringify({}) })
export const deactivateProviders = () =>
  apiFetch<{ ok: boolean }>('/providers/deactivate', { method: 'POST', body: JSON.stringify({}) })
export const testProvider = (id: number) =>
  apiFetch<{ ok: boolean; model: string; error?: string }>(`/providers/${id}/test`, {
    method: 'POST',
    body: JSON.stringify({}),
  })

// --- settings（只读配置快照，plan/06 §四.7；Key 仅返回是否已设，不返回明文）---
export interface AppSettings {
  anthropic_model: string
  budget_per_session_usd: number
  daily_budget_usd: number
  max_tool_calls: number
  max_upload_mb: number
  search_engine: string
  anthropic_api_key_set: boolean
  bing_api_key_set: boolean
  auth_token_set: boolean
  ocr_available: boolean // 图片 OCR 依赖可用（P3，R9）
  whisper_available: boolean // 音视频转写依赖可用（P3，R9）
  whisper_model: string
  media_max_minutes: number
}
export const getSettings = () => apiFetch<AppSettings>('/settings')

// --- generate / clarify（SSE POST，见 lib/sse.ts）---
// generate 支持断线续接：流未到终态即带 Last-Event-ID 重连，指数退避≤3（plan/06 §七）。
// clarify 不重连（端点非幂等；续接应走 /generate attach，非 /clarify）。
export function generateStream(
  sessionId: number,
  rawText: string,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  return ssePostWithReconnect(`/plans/generate`, { session_id: sessionId, raw_text: rawText }, onEvent, signal)
}

// 局部重生成（plan/10 §十三）：从 scope 起级联重跑，复用断线重连。
export function regenerateStream(
  planId: number,
  scope: string,
  feedback: string,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  return ssePostWithReconnect(`/plans/${planId}/regenerate`, { scope, feedback }, onEvent, signal)
}

export function clarifyStream(
  sessionId: number,
  answers: Record<string, unknown>,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<string | undefined> {
  return ssePost(`/plans/${sessionId}/clarify`, { answers }, onEvent, signal)
}

import { openSSE, readSSEStream, SseError } from './sse'

const SSE_MAX_RETRIES = 3 // plan/06 §七：指数退避重连最多 3 次
const SSE_BACKOFF_BASE_MS = 1000
const SSE_BACKOFF_MAX_MS = 8000

/** 流结束即代表完成的终态事件（收到则不重连）。 */
function isTerminalEvent(event: string): boolean {
  return event === 'done' || event === 'error' || event === 'budget_exceeded' || event === 'clarify_needed'
}

function sseBackoff(attempt: number): number {
  return Math.min(SSE_BACKOFF_BASE_MS * 2 ** (attempt - 1), SSE_BACKOFF_MAX_MS)
}

function sseSleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal?.aborted) return resolve()
    const t = setTimeout(resolve, ms)
    signal?.addEventListener('abort', () => { clearTimeout(t); resolve() }, { once: true })
  })
}

async function ssePost(
  path: string,
  body: Record<string, unknown>,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<string | undefined> {
  return openSSE(`${BASE}${path}`, {
    method: 'POST',
    body: JSON.stringify(body),
    token: token(),
    signal,
    onEvent: (ev) => {
      onEvent(ev.event ?? 'message', parseSseData(ev.data))
    },
  })
}

/**
 * SSE POST + 断线续接：流未到终态即带 Last-Event-ID 重连，指数退避≤3。
 * - 收到终态事件或被取消 -> 正常结束。
 * - 4xx 非瞬态（如 409 stale 会话）-> 不重连，直接抛。
 * - 网络错误 / 5xx / 超时 -> 重连；超限抛「连接中断，重连失败」。
 * 后端按 id > last_event_id 重放，故无重复事件/重复计费。
 */
async function ssePostWithReconnect(
  path: string,
  body: Record<string, unknown>,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  signal?: AbortSignal,
): Promise<void> {
  let lastId: string | undefined
  let attempt = 0
  for (;;) {
    if (signal?.aborted) return
    let terminalReached = false
    try {
      await openSSE(`${BASE}${path}`, {
        method: 'POST',
        body: JSON.stringify(body),
        token: token(),
        signal,
        lastEventId: lastId,
        onEvent: (ev) => {
          // 边投递边推进游标：openSSE 的返回值只在流「干净结束」时拿到，
          // 网络中断时 readSSEStream 直接抛出、返回值丢失——若不在这里
          // 实时记录 lastId，重连将从 undefined 重放全部事件（事件重复送达）
          if (ev.id) lastId = ev.id
          const eventName = ev.event ?? 'message'
          if (isTerminalEvent(eventName)) terminalReached = true
          onEvent(eventName, parseSseData(ev.data))
        },
      })
    } catch (e) {
      if (signal?.aborted) return
      // 4xx 非瞬态（408 超时 / 429 限流除外）-> 不重连，直接抛
      if (
        e instanceof SseError &&
        e.status !== undefined &&
        e.status >= 400 &&
        e.status < 500 &&
        e.status !== 408 &&
        e.status !== 429
      ) {
        throw e
      }
      // 网络错误 / 5xx / 超时 -> 走重连
    }
    if (terminalReached || signal?.aborted) return
    attempt++
    if (attempt > SSE_MAX_RETRIES) {
      throw new Error('连接中断，重连失败，请重试')
    }
    await sseSleep(sseBackoff(attempt), signal)
  }
}

// --- 协作分享（P5）：只读分享链接 ---
export interface ShareStatus {
  shared: boolean
  token?: string
  path?: string
  created_at?: string
}
export interface SharedPlanData {
  title: string
  created_at: string
  content: {
    topic?: string
    stages?: Array<{
      index: number
      name: string
      goal?: string
      milestone?: string
      topics?: Array<{ name: string; must_learn?: boolean }>
      resources?: Array<{ title?: string; url?: string; type?: string; verified?: string }>
    }>
    quizzes?: Array<{ question?: string }>
  }
}
export const createShare = (planId: number) =>
  apiFetch<{ token: string; path: string; created_at: string }>(`/plans/${planId}/share`, { method: 'POST' })
export const revokeShare = (planId: number) => apiFetch<void>(`/plans/${planId}/share`, { method: 'DELETE' })
export const getShare = (planId: number) => apiFetch<ShareStatus>(`/plans/${planId}/share`)
export const getShared = (token: string) => apiFetch<SharedPlanData>(`/shared/${token}`)

// --- 首次设置向导（iter 89 零配置开箱即用）---
// /setup/* 为公开端点：status 无需认证；setup/dismiss 在开放模式或 admin JWT 下可用。
export interface SetupStatus {
  open_mode: boolean
  llm_configured: boolean
  dismissed: boolean
  needs_setup: boolean
}
export interface SetupInput {
  password?: string
  api_key?: string
  base_url?: string
  model?: string
}
export interface SetupResult {
  ok: boolean
  password_set: boolean
  provider_configured: boolean
  token?: string
  user?: AuthUser
}
export const getSetupStatus = () => apiFetch<SetupStatus>('/setup/status')
export const postSetup = (body: SetupInput) =>
  apiFetch<SetupResult>('/setup', { method: 'POST', body: JSON.stringify(body) })
export const dismissSetup = () => apiFetch<{ ok: boolean }>('/setup/dismiss', { method: 'POST' })

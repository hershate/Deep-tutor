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

// 会话源资料管理（iter 104）：清单 + 单个删除（传错资料可移除后重生成）
export interface SessionMaterial {
  material_id: number
  kind: string // file | url | text
  source: string // 可读摘要：文件名 / URL / 文本前 200 字
  parsed_chars: number
  meta: Record<string, unknown>
  created_at: string | null
}

export const getSessionMaterials = (sessionId: number) =>
  apiFetch<{ items: SessionMaterial[]; total: number }>(`/sessions/${sessionId}/materials`)

export const deleteSessionMaterial = (sessionId: number, materialId: number) =>
  apiFetch<{ ok: boolean }>(`/sessions/${sessionId}/materials/${materialId}`, { method: 'DELETE' })

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
export const listPlans = (sessionId?: number, page = 1, q = '') => {
  // err.md D18：URLSearchParams 拼 query（此前手工插值，session_id 异常值可注入额外参数）
  const params = new URLSearchParams({ page: String(page) })
  if (sessionId) params.set('session_id', String(sessionId))
  if (q.trim()) params.set('q', q.trim()) // iter 128：主题搜索
  return apiFetch<{ items: PlanSummary[]; total: number }>(`/plans?${params.toString()}`)
}

export const getPlan = (id: number) => apiFetch<unknown>(`/plans/${id}`)

export const exportPlan = (planId: number, format = 'md', wrongOnly = false) =>
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
  }>(`/export/${planId}`, { method: 'POST', body: JSON.stringify({ format, wrong_only: wrongOnly }), timeoutMs: 300_000 })

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
  unitIds?: number[], // iter 98：按单元重生成（选中若干单元单独重新生成；与 refresh/scope 互斥由后端保证）
): Promise<void> {
  const res = await fetch(`${BASE}/plans/${planId}/materials`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token()}`,
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(unitIds?.length ? { depth, unit_ids: unitIds } : { depth, refresh }),
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

// 错题重练（iter 114）：最近作答判错的题原题重练，零 LLM 成本（同步 GET）
export const getWrongQuizzes = (planId: number, signal?: AbortSignal) =>
  apiFetch<{ title: string; quiz_count: number; quizzes: QuizItemData[]; warning?: string }>(
    `/plans/${planId}/quiz/wrong`,
    { signal },
  )

// 提交作答（plan/03 §一）：choice 服务器判分；fill/short 自评 correct=null
export const submitQuiz = (quizId: number, userAnswer: Record<string, unknown>) =>
  apiFetch<{ correct: boolean | null; answer: string; explanation: string }>(
    `/quizzes/${quizId}/submit`,
    { method: 'POST', body: JSON.stringify({ user_answer: userAnswer }) },
  )

// --- 个人知识库（iter 100，Wiki 页）---
export interface KnowledgeItem {
  node_id: number
  sid: string
  name: string
  difficulty: number // 1-5
  must_learn: boolean
  plan_id: number
  plan_topic: string
  status: ProgressStatus
  mastery: number // 0-1
  has_note: boolean // iter 105：是否有笔记（Wiki 显示 📝）
}
// iter 161：个人笔记总览（Wiki「笔记」视图）
export interface NoteOverviewItem {
  node_id: number
  node_name: string
  plan_id: number
  plan_topic: string
  content_preview: string
  truncated: boolean
  updated_at: string | null
}
export const getNotesOverview = () =>
  apiFetch<{ items: NoteOverviewItem[]; total: number }>('/knowledge/notes')
export interface KnowledgeResult {
  items: KnowledgeItem[]
  total: number
  stats: Record<ProgressStatus, number>
}
export const getKnowledge = (params: { q?: string; status?: ProgressStatus; plan_id?: number } = {}) => {
  const q = new URLSearchParams()
  if (params.q?.trim()) q.set('q', params.q.trim())
  if (params.status) q.set('status', params.status)
  if (params.plan_id != null) q.set('plan_id', String(params.plan_id))
  const qs = q.toString()
  return apiFetch<KnowledgeResult>(`/knowledge${qs ? `?${qs}` : ''}`)
}

// 知识点笔记（iter 105）：每用户每节点一条；空内容保存即删除
export interface NodeNote {
  node_id: number
  content: string
  updated_at: string | null
}
export const getNodeNote = (nodeId: number) => apiFetch<NodeNote>(`/knowledge/notes/${nodeId}`)
export const putNodeNote = (nodeId: number, content: string) =>
  apiFetch<NodeNote>(`/knowledge/notes/${nodeId}`, {
    method: 'PUT',
    body: JSON.stringify({ content }),
  })

// --- progress（P2，plan/03 §一）---
export type ProgressStatus = 'not_started' | 'learning' | 'done'
// 全局今日待复习（iter 119）：跨方案到期节点
export interface ReviewDueItem {
  plan_id: number
  plan_topic: string
  node_id: string // knowledge_nodes.sid
  name: string
  status: ProgressStatus
  mastery: number
  next_review: string // ISO 日期
}
export const getReviewDue = () =>
  apiFetch<{ items: ReviewDueItem[]; total: number }>('/progress/review/due')

// 全局学习统计（iter 122）：累计/本周分钟 + 连续打卡
export interface StudyStats {
  total_minutes: number
  week_minutes: number
  streak_days: number
  total_logs: number
}
export const getStudyStats = () => apiFetch<StudyStats>('/progress/stats/summary')

// iter 139：打卡日历热力图——近 N 日按日分钟数（无打卡日补 0）
export interface CalendarDay { date: string; minutes: number }
export const getStatsCalendar = (days = 84) =>
  apiFetch<{ items: CalendarDay[] }>(`/progress/stats/calendar?days=${days}`)

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
// iter 150：删除打错的卡
export const deleteLearningLog = (logId: number) =>
  apiFetch<{ deleted: boolean; id: number }>(`/progress/logs/${logId}`, { method: 'DELETE' })

// --- cancel（取消运行中生成，plan/10 §十一）---
export const cancelGeneration = (sessionId: number) =>
  apiFetch<{ ok: boolean; status: string }>(`/plans/${sessionId}/cancel`, { method: 'POST' })

// iter 152：测验历史统计（作答次数/判分正确率/当前错题数）
export interface QuizStats {
  quiz_count: number
  attempts: number
  graded: number
  correct: number
  accuracy: number | null
  wrong_count: number
}
export const getQuizStats = (planId: number) =>
  apiFetch<QuizStats>(`/plans/${planId}/quiz/stats`)

// iter 156：跨方案错题总览（Wiki 全局错题卡）
export interface WrongOverviewItem { plan_id: number; topic: string; wrong_count: number }
export const getWrongOverview = () =>
  apiFetch<{ items: WrongOverviewItem[]; total: number }>('/quizzes/wrong-overview')

// --- usage ---
export const getUsage = () =>
  apiFetch<{ total_tokens: number; total_cost: number; cache_hit_rate: number; calls: number; today_cost: number; daily_budget_usd: number }>('/usage')

// iter 131：近 N 日按日用量（无调用日补 0，供趋势图）
export interface UsageDailyItem { date: string; cost: number; tokens: number; calls: number }
export const getUsageDaily = (days = 14) =>
  apiFetch<{ items: UsageDailyItem[] }>(`/usage/daily?days=${days}`)

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
  daily_budget_effective: number // iter 171：用户覆盖后的有效日预算
  daily_budget_is_override: boolean
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

// iter 171：用户级日预算覆盖；value 为 null = 清除覆盖回退全局默认（0 = 不限）
export interface PrefsResult {
  daily_budget_usd: number
  daily_budget_default: number
  daily_budget_is_override: boolean
}
export const updatePrefs = (dailyBudgetUsd: number | null) =>
  apiFetch<PrefsResult>('/settings/prefs', {
    method: 'PUT',
    body: JSON.stringify({ daily_budget_usd: dailyBudgetUsd }),
  })

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

// --- 数据备份（iter 93）：整库导出 JSON 下载 / 追加式导入 ---

export interface BackupImportResult {
  ok: boolean
  sessions: number
  materials: number
  plans: number
  knowledge_nodes: number
  study_units: number
  quizzes: number
  progress: number
  learning_logs: number
  provider_profiles: number
  skipped: string[]
}

/** 非 JSON 响应的错误解析（与 apiFetch 同一约定）。 */
async function throwIfNotOk(res: Response): Promise<void> {
  if (res.ok) return
  handleUnauthorized(res.status)
  const body = (await res.json().catch(() => ({}))) as { error?: { code?: string; message?: string } }
  const err = body?.error ?? {}
  throw new ApiError(err.code ?? 'http_error', err.message ?? `HTTP ${res.status}`, res.status)
}

/** 触发浏览器下载 Blob（文件名优先取 Content-Disposition 的 filename 星号形式，支持 RFC 5987 中文名）。 */
function saveBlob(blob: Blob, disposition: string | null, fallback: string): void {
  const cd = disposition ?? ''
  const star = /filename\*=UTF-8''([^;]+)/.exec(cd)
  const plain = /filename="?([^";]+)"?/.exec(cd)
  const name = (star ? decodeURIComponent(star[1]) : plain?.[1]) ?? fallback
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = name
  a.click()
  URL.revokeObjectURL(a.href)
}

/** 前端侧文本下载（如单个学习单元 markdown）。 */
export function saveTextFile(text: string, filename: string): void {
  saveBlob(new Blob([text], { type: 'text/markdown;charset=utf-8' }), null, filename)
}

/** 导出当前用户全部数据并触发浏览器下载（文件名取后端 Content-Disposition）。 */
export async function downloadBackup(): Promise<void> {
  const res = await fetch(`${BASE}/backup/export`, {
    headers: { Authorization: `Bearer ${token()}` },
  })
  await throwIfNotOk(res)
  saveBlob(await res.blob(), res.headers.get('content-disposition'), 'deep-tutor-backup.json')
}

/** 全案自学材料 zip 下载（iter 99）。 */
export async function downloadMaterialsZip(planId: number): Promise<void> {
  const res = await fetch(`${BASE}/plans/${planId}/materials/export`, {
    headers: { Authorization: `Bearer ${token()}` },
  })
  await throwIfNotOk(res)
  saveBlob(await res.blob(), res.headers.get('content-disposition'), `materials-${planId}.zip`)
}

/** 个人知识手册 Markdown 下载（iter 116）。 */
export async function downloadKnowledgeHandbook(): Promise<void> {
  const res = await fetch(`${BASE}/knowledge/export`, {
    headers: { Authorization: `Bearer ${token()}` },
  })
  await throwIfNotOk(res)
  saveBlob(await res.blob(), res.headers.get('content-disposition'), '知识手册.md')
}

/** 导入备份 JSON（追加式：全部以新数据重建，不覆盖现有数据）。 */
export async function importBackup(file: File): Promise<BackupImportResult> {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(`${BASE}/backup/import`, {
    method: 'POST',
    // 不设 Content-Type：浏览器自动带 multipart boundary（apiFetch 的 json 头不适用）
    headers: { Authorization: `Bearer ${token()}` },
    body: fd,
  })
  await throwIfNotOk(res)
  return (await res.json()) as BackupImportResult
}

/** 备份导入预检（iter 159）：只读计数不落库，导入前确认内容。 */
export interface BackupPreview {
  version: number
  exported_at: string | null
  username: string
  sessions: number
  plans: number
  materials: number
  knowledge_nodes: number
  node_notes: number
  progress: number
  learning_logs: number
  quizzes: number
  study_units: number
  provider_profiles: number
}
export async function previewBackup(file: File): Promise<BackupPreview> {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(`${BASE}/backup/preview`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token()}` },
    body: fd,
  })
  await throwIfNotOk(res)
  return (await res.json()) as BackupPreview
}

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

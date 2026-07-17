// API 客户端（plan/03 REST API）。
// 所有请求带 Authorization: Bearer 头（本地单用户认证，plan/03 §一）。
// dev 环境经 Vite 代理 /api -> :8000；生产经 nginx 反代。

const BASE = import.meta.env.VITE_API_BASE ?? '/api'

function token(): string {
  return import.meta.env.VITE_AUTH_TOKEN ?? ''
}

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) {
    super(message)
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token()}`,
      ...(init?.headers ?? {}),
    },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const err = body?.error ?? {}
    throw new ApiError(err.code ?? 'http_error', err.message ?? `HTTP ${res.status}`, res.status)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

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

export async function uploadMaterial(sessionId: number, file: File) {
  const form = new FormData()
  form.append('session_id', String(sessionId))
  form.append('file', file)
  const res = await fetch(`${BASE}/materials/upload`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token()}` },
    body: form,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(body?.error?.code ?? 'http_error', body?.error?.message ?? `HTTP ${res.status}`, res.status)
  }
  return res.json()
}

// --- plans ---
export const listPlans = (sessionId?: number) =>
  apiFetch<{ items: unknown[]; total: number }>(`/plans${sessionId ? `?session_id=${sessionId}` : ''}`)

export const getPlan = (id: number) => apiFetch<unknown>(`/plans/${id}`)

export const exportPlan = (planId: number, format = 'md') =>
  apiFetch<{ file_url: string; format: string; warning?: string }>(`/export/${planId}`, {
    method: 'POST',
    body: JSON.stringify({ format }),
  })

// --- cancel（取消运行中生成，plan/10 §十一）---
export const cancelGeneration = (sessionId: number) =>
  apiFetch<{ ok: boolean; status: string }>(`/plans/${sessionId}/cancel`, { method: 'POST' })

// --- usage ---
export const getUsage = () =>
  apiFetch<{ total_tokens: number; total_cost: number; cache_hit_rate: number; calls: number }>('/usage')

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

import { openSSE, SseError } from './sse'

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
      const data = ev.data ? JSON.parse(ev.data) : {}
      onEvent(ev.event ?? 'message', data)
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
      lastId = await openSSE(`${BASE}${path}`, {
        method: 'POST',
        body: JSON.stringify(body),
        token: token(),
        signal,
        lastEventId: lastId,
        onEvent: (ev) => {
          const eventName = ev.event ?? 'message'
          if (isTerminalEvent(eventName)) terminalReached = true
          const data = ev.data ? JSON.parse(ev.data) : {}
          onEvent(eventName, data)
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

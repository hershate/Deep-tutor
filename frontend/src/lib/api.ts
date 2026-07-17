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

// --- usage ---
export const getUsage = () => apiFetch<{ total_tokens: number; total_cost: number; cache_hit_rate: number; calls: number }>('/usage')

// --- generate / clarify（SSE POST，见 lib/sse.ts）---
export function generateStream(
  sessionId: number,
  rawText: string,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  signal?: AbortSignal,
) {
  return ssePost(`/plans/generate`, { session_id: sessionId, raw_text: rawText }, onEvent, signal)
}

export function clarifyStream(
  sessionId: number,
  answers: Record<string, unknown>,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  signal?: AbortSignal,
) {
  return ssePost(`/plans/${sessionId}/clarify`, { answers }, onEvent, signal)
}

import { openSSE } from './sse'

async function ssePost(
  path: string,
  body: Record<string, unknown>,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  signal?: AbortSignal,
) {
  await openSSE(`${BASE}${path}`, {
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

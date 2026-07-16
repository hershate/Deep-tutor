// API 客户端骨架（plan/03 REST API）。
// 所有请求带 X-Auth-Token 头（本地单用户认证，plan/09 §十七 AUTH_TOKEN）。
// dev 环境经 Vite 代理 /api -> :8000；生产经 nginx 反代。

const BASE = import.meta.env.VITE_API_BASE ?? '/api'

function authHeaders(): Record<string, string> {
  const token = import.meta.env.VITE_AUTH_TOKEN ?? ''
  return token ? { 'X-Auth-Token': token } : {}
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
      ...authHeaders(),
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

export async function checkHealth(): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/health')
}

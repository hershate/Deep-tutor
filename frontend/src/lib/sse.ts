// SSE 流式：fetch + ReadableStream 携带鉴权头（plan 决策：SSE 头鉴权用 fetch+ReadableStream，
// 因 EventSource 不能带自定义头）。支持断线续接（Last-Event-ID，plan/10 SSE 续接游标）。

/** SSE 层错误（携带 HTTP 状态码，供上层区分瞬态/非瞬态以决定是否重连）。 */
export class SseError extends Error {
  constructor(public status: number | undefined, message: string) {
    super(message)
    this.name = 'SseError'
  }
}

export interface SSEEvent {
  id?: string
  event?: string
  data: string
}

export interface SSEOptions {
  token?: string
  lastEventId?: string
  signal?: AbortSignal
  method?: 'GET' | 'POST'
  body?: string
  onEvent: (ev: SSEEvent) => void
  onError?: (err: unknown) => void
}

/**
 * 打开 SSE 流。逐行解析 `text/event-stream`，按 SSE 规范处理 field/event/id。
 * 支持 POST（/plans/generate 等流式端点）。断线续接：传入上次最后的 event id 作为 Last-Event-ID 头。
 *
 * 返回流中见到的最后一个 `id`（供续接时回传 Last-Event-ID，plan/10 §十二）。
 */
export async function openSSE(url: string, opts: SSEOptions): Promise<string | undefined> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  if (opts.token) headers['Authorization'] = `Bearer ${opts.token}`
  if (opts.lastEventId) headers['Last-Event-ID'] = opts.lastEventId
  if (opts.body) headers['Content-Type'] = 'application/json'

  const res = await fetch(url, {
    method: opts.method ?? 'GET',
    headers,
    body: opts.body,
    signal: opts.signal,
  })
  if (!res.ok || !res.body) {
    throw new SseError(res.status, `SSE 连接失败: HTTP ${res.status}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let cur: SSEEvent = { data: '' }
  let lastId: string | undefined

  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (line === '') {
        if (cur.data !== '' || cur.event || cur.id) {
          if (cur.id) lastId = cur.id
          opts.onEvent({ ...cur })
        }
        cur = { data: '' }
        continue
      }
      if (line.startsWith(':')) continue // 注释/心跳
      const idx = line.indexOf(':')
      const field = idx >= 0 ? line.slice(0, idx) : line
      const val = idx >= 0 ? line.slice(idx + 1).replace(/^ /, '') : ''
      if (field === 'data') cur.data += (cur.data ? '\n' : '') + val
      else if (field === 'event') cur.event = val
      else if (field === 'id') cur.id = val
    }
  }
  return lastId
}

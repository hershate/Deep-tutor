// SSE 流式：fetch + ReadableStream 携带鉴权头（plan 决策：SSE 头鉴权用 fetch+ReadableStream，
// 因 EventSource 不能带自定义头）。支持断线续接（Last-Event-ID，plan/10 SSE 续接游标）。

export interface SSEEvent {
  id?: string
  event?: string
  data: string
}

export interface SSEOptions {
  token?: string
  lastEventId?: string
  signal?: AbortSignal
  onEvent: (ev: SSEEvent) => void
  onError?: (err: unknown) => void
}

/**
 * 打开 SSE 流。逐行解析 `text/event-stream`，按 SSE 规范处理 field/event/id。
 * 断线续接：传入上次最后的 event id 作为 Last-Event-ID 头。
 */
export async function openSSE(url: string, opts: SSEOptions): Promise<void> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  if (opts.token) headers['X-Auth-Token'] = opts.token
  if (opts.lastEventId) headers['Last-Event-ID'] = opts.lastEventId

  const res = await fetch(url, { headers, signal: opts.signal })
  if (!res.ok || !res.body) {
    throw new Error(`SSE 连接失败: HTTP ${res.status}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let cur: SSEEvent = { data: '' }

  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (line === '') {
        if (cur.data !== '' || cur.event || cur.id) opts.onEvent({ ...cur })
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
}

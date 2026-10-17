// SSE 流式：fetch + ReadableStream 携带鉴权头（plan 决策：SSE 头鉴权用 fetch+ReadableStream，
// 因 EventSource 不能带自定义头）。支持断线续接（Last-Event-ID，plan/10 SSE 续接游标）。

import { handleUnauthorized } from './auth'

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
    handleUnauthorized(res.status) // err.md D5：SSE 通道 401 触发登录跳转（此前仅显示连接失败）
    throw new SseError(res.status, `SSE 连接失败: HTTP ${res.status}`)
  }
  return readSSEStream(res.body, opts.onEvent)
}

/**
 * 解析已建立的 SSE 响应体（逐行 field/event/id，注释/心跳行跳过）。
 * 从 openSSE 拆出，供需先按 content-type 分流（JSON 幂等 vs SSE）的调用方复用（如材料生成，iter 47）。
 * 返回流中见到的最后一个 `id`。
 *
 * iter 244 F9 空闲看门狗：代理/浏览器缓冲可让整个流（含后端 15s 心跳）永远不到达，
 * reader.read() 无限挂起、任务假 live——idleTimeoutMs（默认 60s，4× 心跳）无任何字节
 * 即抛 SseError(undefined)（上层按可重连错误处理）；超时即 cancel reader 释放连接。
 */
export async function readSSEStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (ev: SSEEvent) => void,
  idleTimeoutMs = 60_000,
): Promise<string | undefined> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let cur: SSEEvent = { data: '' }
  let lastId: string | undefined

  const readWithWatchdog = () =>
    new Promise<ReadableStreamReadResult<Uint8Array>>((resolve, reject) => {
      const timer = setTimeout(() => {
        reader.cancel().catch(() => undefined) // 释放挂起的连接
        reject(new SseError(undefined, `SSE 空闲超时（${Math.round(idleTimeoutMs / 1000)}s 无数据）`))
      }, idleTimeoutMs)
      reader.read().then(
        (r) => {
          clearTimeout(timer)
          resolve(r)
        },
        (e: unknown) => {
          clearTimeout(timer)
          reject(e instanceof Error ? e : new Error(String(e)))
        },
      )
    })

  let firstChunk = true
  for (;;) {
    const { value, done } = await readWithWatchdog()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // iter 371：流首 BOM 剥除（WHATWG SSE 要求）；整流改按三种行尾（CR LF / LF / 裸 CR）
    // 统一切分——此前 split('\n') 遇裸 CR 行尾的流永不出空行，事件全部静默丢弃
    if (firstChunk) {
      if (buffer.charCodeAt(0) === 0xfeff) buffer = buffer.slice(1)
      firstChunk = false
    }
    const lines = buffer.split(/\r\n|\r|\n/)
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (line === '') {
        // 规范：data 为空的块不派发（仅 id/event 的块丢弃）；id 仍记账供 Last-Event-ID 续接
        if (cur.id) lastId = cur.id
        if (cur.data !== '') onEvent({ ...cur })
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

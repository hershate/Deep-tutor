// AI 对话（记忆系统，iter 345）：DeepSeek 式实时流式 UI。
// 左：会话列表；右：对话流（流式逐字 + 可折叠思考 + 工具调用卡片）+ 记忆面板。
// Agent 对话经 SSE POST /api/threads/{id}/runs（lib/sse.ts），事件 text_delta/thinking_delta/
// tool_call/tool_result/message_end/done 实时渲染。

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { openSSE } from '../lib/sse'
import { getToken } from '../lib/auth'
import {
  createThread,
  deleteThread,
  listMessages,
  listThreads,
  listThreadMemory,
  type ChatMessage,
  type Thread,
  type MemoryItem,
} from '../lib/api'

const API_BASE = import.meta.env.VITE_API_BASE ?? '/api'

interface ToolChip {
  name: string
  status: 'running' | 'ok' | 'error'
}

interface Streaming {
  text: string
  thinking: string
  tools: Record<string, ToolChip>
}

function blocksText(content: unknown): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return (content as Array<{ type?: string; text?: string }>)
      .filter((b) => b.type === 'text' || b.type === 'thinking')
      .map((b) => b.text ?? '')
      .join('')
  }
  return ''
}

function blocksTools(content: unknown): Array<{ id: string; name: string }> {
  if (!Array.isArray(content)) return []
  return (content as Array<{ type?: string; id?: string; name?: string }>)
    .filter((b) => b.type === 'tool_use')
    .map((b) => ({ id: b.id ?? '', name: b.name ?? '' }))
}

export default function Chat() {
  const queryClient = useQueryClient()
  const [activeId, setActiveId] = useState<number | null>(null)
  const [input, setInput] = useState('')
  const [stream, setStream] = useState<Streaming | null>(null)
  const [error, setError] = useState('')
  const [showMemory, setShowMemory] = useState(false)

  const threadsQ = useQuery({ queryKey: ['threads'], queryFn: () => listThreads() })
  const msgsQ = useQuery({
    queryKey: ['messages', activeId],
    queryFn: () => listMessages(activeId!),
    enabled: activeId != null,
  })
  const memQ = useQuery({
    queryKey: ['thread-memory', activeId],
    queryFn: () => listThreadMemory(activeId!),
    enabled: activeId != null && showMemory,
  })

  const createM = useMutation({
    mutationFn: () => createThread({ title: '新对话', kind: 'chat' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['threads'] }),
  })

  async function send() {
    const text = input.trim()
    if (!text || !activeId || stream) return
    setInput('')
    setError('')
    setStream({ text: '', thinking: '', tools: {} })
    const tid = activeId
    await openSSE(`${API_BASE}/threads/${tid}/runs`, {
      method: 'POST',
      body: JSON.stringify({ message: text }),
      token: getToken() || undefined,
      onEvent: (ev) => {
        let d: Record<string, unknown> = {}
        try {
          d = ev.data ? (JSON.parse(ev.data) as Record<string, unknown>) : {}
        } catch {
          /* ignore */
        }
        const t = ev.event
        if (t === 'text_delta') setStream((s) => (s ? { ...s, text: s.text + (d.text as string) } : s))
        else if (t === 'thinking_delta')
          setStream((s) => (s ? { ...s, thinking: s.thinking + (d.text as string) } : s))
        else if (t === 'tool_call')
          setStream((s) => (s ? { ...s, tools: { ...s.tools, [d.id as string]: { name: d.name as string, status: 'running' } } } : s))
        else if (t === 'tool_result')
          setStream((s) => {
            if (!s) return s
            const id = d.id as string
            return { ...s, tools: { ...s.tools, [id]: { ...(s.tools[id] ?? { name: '', status: 'running' }), status: d.is_error ? 'error' : 'ok' } } }
          })
        else if (t === 'error') setError(String(d.message ?? '出错了'))
        else if (t === 'done') {
          setStream(null)
          queryClient.invalidateQueries({ queryKey: ['messages', tid] })
          queryClient.invalidateQueries({ queryKey: ['threads'] })
        }
      },
      onError: (e) => setError(e instanceof Error ? e.message : '连接失败'),
    })
  }

  const messages = msgsQ.data?.items ?? []
  const streaming = stream

  return (
    <div className="flex h-[calc(100vh-7rem)] gap-3">
      {/* 会话列表 */}
      <aside className="hidden md:flex w-56 shrink-0 flex-col rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-900">
        <button
          onClick={() => createM.mutate()}
          className="mb-2 rounded-md bg-blue-600 px-2 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
        >
          ＋ 新对话
        </button>
        <div className="flex-1 overflow-y-auto">
          {(threadsQ.data?.items ?? []).map((t: Thread) => (
            <button
              key={t.id}
              onClick={() => setActiveId(t.id)}
              className={`mb-1 flex w-full items-center gap-1 rounded-md px-2 py-1.5 text-left text-sm ${
                activeId === t.id ? 'bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300' : 'hover:bg-slate-100 dark:hover:bg-slate-800'
              }`}
            >
              <span className="truncate flex-1">{t.title || `对话 ${t.id}`}</span>
              {t.running && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-green-500" title="运行中" />}
            </button>
          ))}
          {threadsQ.data?.items.length === 0 && <p className="px-2 py-4 text-xs text-slate-400">尚无对话，点上方新建</p>}
        </div>
      </aside>

      {/* 对话区 */}
      <section className="flex min-w-0 flex-1 flex-col rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900">
        {!activeId ? (
          <div className="flex flex-1 items-center justify-center text-sm text-slate-400">
            <div className="text-center">
              <p className="mb-3 text-lg">💬 AI 对话助手</p>
              <button onClick={() => createM.mutate()} className="rounded-md bg-blue-600 px-3 py-1.5 text-white hover:bg-blue-700">
                开始一个新对话
              </button>
            </div>
          </div>
        ) : (
          <>
            <header className="flex items-center justify-between border-b border-slate-200 px-3 py-2 dark:border-slate-700">
              <span className="truncate text-sm font-medium">{threadsQ.data?.items.find((x) => x.id === activeId)?.title || `对话 ${activeId}`}</span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setShowMemory((v) => !v)}
                  className="rounded px-2 py-0.5 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                >
                  🧠 记忆
                </button>
                <button
                  onClick={() => {
                    if (confirm('删除此对话及其全部记录？')) {
                      deleteThread(activeId).then(() => {
                        queryClient.invalidateQueries({ queryKey: ['threads'] })
                        setActiveId(null)
                      })
                    }
                  }}
                  className="rounded px-2 py-0.5 text-xs text-red-500 hover:bg-red-50 dark:hover:bg-red-950"
                >
                  删除
                </button>
              </div>
            </header>

            {showMemory && <MemoryPanel items={memQ.data?.items ?? []} loading={memQ.isLoading} />}

            <div className="flex-1 space-y-3 overflow-y-auto p-3">
              {messages.map((m: ChatMessage) => (
                <MessageBubble key={m.id} role={m.role} content={m.content} />
              ))}
              {streaming && <StreamingBubble stream={streaming} />}
              {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-950 dark:text-red-400">{error}</p>}
            </div>

            <div className="border-t border-slate-200 p-2 dark:border-slate-700">
              <div className="flex gap-2">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      send()
                    }
                  }}
                  placeholder="输入消息，Enter 发送（Shift+Enter 换行）"
                  rows={2}
                  className="flex-1 resize-none rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm outline-none focus:border-blue-500 dark:border-slate-600 dark:bg-slate-800"
                />
                <button
                  onClick={send}
                  disabled={!input.trim() || !!stream}
                  className="rounded-md bg-blue-600 px-4 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
                >
                  {stream ? '生成中…' : '发送'}
                </button>
              </div>
              <p className="mt-1 text-[11px] text-slate-400">助手可读写工作区文件、联网搜索、记忆要点；复杂产出在工作区草拟后由你确认提升。</p>
            </div>
          </>
        )}
      </section>
    </div>
  )
}

function MessageBubble({ role, content }: { role: string; content: unknown }) {
  const text = blocksText(content)
  const tools = blocksTools(content)
  const isUser = role === 'user'
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${isUser ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-100'}`}>
        {tools.length > 0 && (
          <div className="mb-1 flex flex-wrap gap-1">
            {tools.map((t) => (
              <span key={t.id} className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-700 dark:bg-amber-900 dark:text-amber-200">
                🔧 {t.name}
              </span>
            ))}
          </div>
        )}
        <div className="whitespace-pre-wrap break-words">{text}</div>
      </div>
    </div>
  )
}

function StreamingBubble({ stream }: { stream: Streaming }) {
  const toolList = Object.entries(stream.tools)
  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] rounded-lg bg-slate-100 px-3 py-2 text-sm dark:bg-slate-800">
        {stream.thinking && (
          <details className="mb-1 rounded bg-slate-200/60 px-2 py-1 text-xs text-slate-500 dark:bg-slate-700/60">
            <summary className="cursor-pointer">💭 思考过程</summary>
            <div className="mt-1 whitespace-pre-wrap">{stream.thinking}</div>
          </details>
        )}
        {toolList.length > 0 && (
          <div className="mb-1 flex flex-wrap gap-1">
            {toolList.map(([id, t]) => (
              <span key={id} className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-700 dark:bg-amber-900 dark:text-amber-200">
                🔧 {t.name} {t.status === 'running' ? '…' : t.status === 'error' ? '✗' : '✓'}
              </span>
            ))}
          </div>
        )}
        <div className="whitespace-pre-wrap break-words">
          {stream.text}
          <span className="ml-0.5 inline-block h-3 w-1.5 animate-pulse bg-slate-400 align-middle" />
        </div>
      </div>
    </div>
  )
}

function MemoryPanel({ items, loading }: { items: MemoryItem[]; loading: boolean }) {
  return (
    <div className="max-h-40 overflow-y-auto border-b border-slate-200 bg-slate-50 px-3 py-2 text-xs dark:border-slate-700 dark:bg-slate-800/50">
      <p className="mb-1 font-medium text-slate-500">记忆库（{items.length}）</p>
      {loading ? (
        <p className="text-slate-400">加载中…</p>
      ) : items.length === 0 ? (
        <p className="text-slate-400">暂无记忆。助手会按需保存要点。</p>
      ) : (
        <ul className="space-y-1">
          {items.map((m) => (
            <li key={m.id} className="rounded bg-white px-2 py-1 dark:bg-slate-800">
              <span className="mr-1 text-[10px] text-slate-400">[{m.kind}]</span>
              {m.content.slice(0, 120)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

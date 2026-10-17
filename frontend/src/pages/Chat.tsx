// AI 对话（记忆系统，iter 345）：DeepSeek 式实时流式 UI。
// 左：会话列表；右：对话流（流式逐字 + 可折叠思考 + 工具调用卡片）+ 记忆面板。
// Agent 对话经 SSE POST /api/threads/{id}/runs（lib/sse.ts），事件 text_delta/thinking_delta/
// tool_call/tool_result/done 实时渲染。
//
// iter 360（对话稳定性修复）：切走再切回不再丢消息/中断——SSE 绑 AbortController 随组件
// 生命周期清理（切走即中止）、切会话即重置流态、切回时若会话仍有 running run 则经
// GET /threads/{id}/runs/active 附流续看（而非 POST 触发 409）；用户消息乐观渲染、run 结束
// 待消息刷新落定再清流式气泡（免闪烁）、错误清流、流式自动滚动跟随。

import { memo, useEffect, useRef, useState, type UIEvent } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { openSSE, SseError, type SSEEvent } from '../lib/sse'
import { getToken } from '../lib/auth'
import Markdown from '../components/Markdown'
import { toast } from '../components/Toast'
import {
  createThread,
  deleteThread,
  patchThread,
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
      .filter((b) => b.type === 'text')
      .map((b) => b.text ?? '')
      .join('')
  }
  return ''
}

function blocksThinking(content: unknown): string {
  if (!Array.isArray(content)) return ''
  return (content as Array<{ type?: string; text?: string }>)
    .filter((b) => b.type === 'thinking')
    .map((b) => b.text ?? '')
    .join('')
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
  const [pendingUser, setPendingUser] = useState<string | null>(null) // 乐观渲染的用户消息（iter 360 BUG-5）
  const [error, setError] = useState('')
  const [showMemory, setShowMemory] = useState(false)

  // 生命周期/重连控制（iter 360）
  const abortRef = useRef<AbortController | null>(null) // 当前会话的 SSE 中止器（切走/切会话即 abort）
  const reconnectAttemptedRef = useRef<number | null>(null) // 已尝试附流续看的会话 id（防重复）
  const doneRef = useRef<number | null>(null) // run 已 done，待 messages 刷新落定再清流（免闪烁 BUG-6）
  const bottomRef = useRef<HTMLDivElement | null>(null)
  // iter 369 B16：贴底跟随——用户上翻阅读（离底 >40px）时暂停自动滚动，回到底部自动恢复
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const pinnedRef = useRef(true)

  // iter 369 B16：会话列表改无限查询（20 条/页，后端 /threads?page= 分页）——此前只取
  // 首页 20 条且无翻页入口，更早会话永久不可达（列表看起来是全部，实则截断）。
  const threadsQ = useInfiniteQuery({
    queryKey: ['threads'],
    queryFn: ({ pageParam }) => listThreads(pageParam),
    initialPageParam: 1,
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((n, p) => n + p.items.length, 0)
      return last.items.length > 0 && loaded < last.total ? all.length + 1 : undefined
    },
  })
  const threads = threadsQ.data?.pages.flatMap((p) => p.items) ?? []
  const threadsTotal = threadsQ.data?.pages[threadsQ.data.pages.length - 1]?.total ?? 0
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

  const messages = msgsQ.data?.items ?? []
  const streaming = stream
  // 「加载更早」翻页态（msgsQ 只取最近 100 条，更早历史按 before_id 向上翻）
  const [noMoreMsgs, setNoMoreMsgs] = useState(false)
  const [loadingEarlier, setLoadingEarlier] = useState(false)
  const [deletingThread, setDeletingThread] = useState(false)

  const createM = useMutation({
    mutationFn: () => createThread({ title: '新对话', kind: 'chat' }),
    onSuccess: (t) => {
      queryClient.invalidateQueries({ queryKey: ['threads'] })
      setActiveId(t.id)
    },
    // iter 388（R31）：创建失败此前零反馈——按钮弹回但什么都没发生，用户无从得知
    onError: (e) => {
      toast.error(e instanceof Error ? e.message : '创建对话失败，请重试')
    },
  })

  // 切会话 / 重挂：中止旧 SSE、重置全部流态（iter 360 BUG-1/BUG-2）
  useEffect(() => {
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac
    setStream(null)
    setPendingUser(null)
    setError('')
    setInput('') // iter 363：切会话清空输入框，防跨会话草稿串味
    doneRef.current = null
    reconnectAttemptedRef.current = null
    setNoMoreMsgs(false) // iter 369 B16：新会话翻页状态复位
    pinnedRef.current = true // 切会话默认贴底（首屏滚到底）
    return () => ac.abort() // 切走或卸载即中止本会话的 SSE（流不再向已卸载组件投递）
  }, [activeId])

  // 切回时若会话仍有 running run 且本地无流 -> 附流续看（iter 360 BUG-3）
  useEffect(() => {
    if (activeId == null || reconnectAttemptedRef.current === activeId) return
    const thread = threads.find((t) => t.id === activeId)
    if (thread?.running) {
      reconnectAttemptedRef.current = activeId
      void reconnectActive(activeId)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId, threadsQ.data])

  // run done 后待 messages 刷新落定再清流式气泡 + 乐观消息（持久化消息已就位，免闪烁 BUG-6）
  useEffect(() => {
    if (doneRef.current != null && !msgsQ.isFetching && doneRef.current === activeId) {
      setStream(null)
      setPendingUser(null)
      doneRef.current = null
    }
  }, [msgsQ.isFetching, msgsQ.dataUpdatedAt, activeId])

  // 流式增长 / 消息变化 -> 仅当用户贴底时自动滚动到底（iter 360 BUG-11 原为无条件滚；
  // iter 369 B16：长回复/历史长会话中用户上翻阅读时被强拉回底部，阅读位置丢失）
  useEffect(() => {
    if (pinnedRef.current) bottomRef.current?.scrollIntoView({ behavior: 'auto', block: 'end' })
  }, [stream?.text, stream?.thinking, messages.length, pendingUser, error])

  function onScrollMsgs(e: UIEvent<HTMLDivElement>) {
    const el = e.currentTarget
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }

  // iter 369 B16：加载更早消息（before_id 向上翻页）。注意 run done/失效刷新会把缓存
  // 重置为最近 100 条（queryFn 不带 before_id），已展开的历史随之折叠——数据无损，可再点开。
  async function loadEarlier() {
    if (activeId == null || loadingEarlier || messages.length === 0) return
    setLoadingEarlier(true)
    try {
      const res = await listMessages(activeId, 100, messages[0].id)
      if (res.items.length < 100) setNoMoreMsgs(true)
      if (res.items.length > 0) {
        // 前插后保持视口位置：记录插前滚动几何，DOM 更新后按高度差补偿
        const el = scrollRef.current
        const prevHeight = el?.scrollHeight ?? 0
        const prevTop = el?.scrollTop ?? 0
        queryClient.setQueryData<{ items: ChatMessage[] }>(['messages', activeId], (old) =>
          old ? { items: [...res.items, ...old.items] } : old,
        )
        requestAnimationFrame(() => {
          if (!el) return
          el.scrollTop = prevTop + (el.scrollHeight - prevHeight)
        })
      }
    } catch {
      /* 翻页失败静默：按钮保留，用户可重试 */
    } finally {
      setLoadingEarlier(false)
    }
  }

  // 统一事件处理：POST 新 run 与 GET 附流续看共用（tid 锁定发起/续看会话）
  function makeEventHandler(tid: number) {
    return (ev: SSEEvent) => {
      let d: Record<string, unknown> = {}
      try {
        d = ev.data ? (JSON.parse(ev.data) as Record<string, unknown>) : {}
      } catch {
        /* ignore */
      }
      const t = ev.event
      if (t === 'text_delta')
        setStream((s) => (s ? { ...s, text: s.text + (d.text as string) } : s))
      else if (t === 'thinking_delta')
        setStream((s) => (s ? { ...s, thinking: s.thinking + (d.text as string) } : s))
      else if (t === 'tool_call')
        setStream((s) =>
          s ? { ...s, tools: { ...s.tools, [d.id as string]: { name: d.name as string, status: 'running' } } } : s,
        )
      else if (t === 'tool_result')
        setStream((s) => {
          if (!s) return s
          const id = d.id as string
          return {
            ...s,
            tools: {
              ...s.tools,
              [id]: { ...(s.tools[id] ?? { name: '', status: 'running' }), status: d.is_error ? 'error' : 'ok' },
            },
          }
        })
      else if (t === 'error') {
        // iter 360 BUG-7：错误也清流，避免卡在「生成中」禁用发送
        setError(String(d.message ?? '出错了'))
        setStream(null)
        setPendingUser(null)
        queryClient.invalidateQueries({ queryKey: ['messages', tid] })
        queryClient.invalidateQueries({ queryKey: ['threads'] })
      } else if (t === 'done') {
        // 不立即清流——待 messages 刷新落定由上面的 effect 清，避免终态瞬间气泡消失闪烁
        doneRef.current = tid
        queryClient.invalidateQueries({ queryKey: ['messages', tid] })
        queryClient.invalidateQueries({ queryKey: ['threads'] })
      }
    }
  }

  async function startRun(tid: number, message: string) {
    const ac = abortRef.current ?? new AbortController()
    abortRef.current = ac
    setPendingUser(message)
    setError('')
    setStream({ text: '', thinking: '', tools: {} })
    // iter 365 E1：本会话已有 POST 流——标记防 threadsQ refetch（窗口聚焦/网络重连时
    // thread.running=true）触发 reconnectActive 清空气泡 + 开第二路 GET 流，致 text_delta
    // 被两路 SSE 同时投递而翻倍。reconnectAttemptedRef 此处语义扩为「本会话已有活动流」。
    reconnectAttemptedRef.current = tid
    try {
      await openSSE(`${API_BASE}/threads/${tid}/runs`, {
        method: 'POST',
        body: JSON.stringify({ message }),
        token: getToken() || undefined,
        signal: ac.signal,
        onEvent: makeEventHandler(tid),
        onError: (e) => {
          if (!ac.signal.aborted) setError(e instanceof Error ? e.message : '连接失败')
        },
      })
      // iter 367（批次六 M2）：干净 EOF 但无终态事件（反代切断/后端异常静默收流）——
      // 不兜底则永久卡「生成中」（发送禁用 + 气泡不消）。按 done 收敛：刷新消息与
      // threads；run 若仍在跑，清 409 防重连标记让 running 检测 effect 重新附流
      if (!ac.signal.aborted && doneRef.current !== tid) {
        doneRef.current = tid
        if (reconnectAttemptedRef.current === tid) reconnectAttemptedRef.current = null
        queryClient.invalidateQueries({ queryKey: ['messages', tid] })
        queryClient.invalidateQueries({ queryKey: ['threads'] })
      }
    } catch (e) {
      if (!ac.signal.aborted) {
        if (e instanceof SseError && e.status === 409) setError('该对话已有运行中的请求，请稍候')
        else setError(e instanceof Error ? e.message : '连接失败')
        setStream(null)
        setPendingUser(null)
        setInput(message) // iter 363：网络失败/409 回填输入，免用户凭记忆重打
      }
    }
  }

  async function reconnectActive(tid: number) {
    const ac = abortRef.current
    if (!ac) return
    setError('')
    setStream({ text: '', thinking: '', tools: {} }) // 从重放事件重建
    try {
      await openSSE(`${API_BASE}/threads/${tid}/runs/active`, {
        token: getToken() || undefined,
        signal: ac.signal,
        onEvent: makeEventHandler(tid),
        onError: (e) => {
          if (!ac.signal.aborted) setError(e instanceof Error ? e.message : '连接失败')
        },
      })
      // iter 367（批次六 M2）：附流路径同样兜底干净 EOF 无终态（不清防重连标记——
      // 避免服务端异常收流时自动重连循环；用户可手动刷新）
      if (!ac.signal.aborted && doneRef.current !== tid) {
        doneRef.current = tid
        queryClient.invalidateQueries({ queryKey: ['messages', tid] })
        queryClient.invalidateQueries({ queryKey: ['threads'] })
      }
    } catch (e) {
      // 404 = 无活跃 run（已结束/无流）：静默，正常拉消息即可；其它错误提示
      if (!ac.signal.aborted) {
        if (!(e instanceof SseError && e.status === 404)) {
          setError(e instanceof Error ? e.message : '连接失败')
        }
        setStream(null)
      }
    }
  }

  async function send() {
    const text = input.trim()
    if (!text || !activeId || stream) return
    setInput('')
    await startRun(activeId, text)
  }

  function selectThread(id: number) {
    if (id === activeId) return
    setActiveId(id) // 切换会话触发上面的 effect：abort 旧流 + 重置状态
  }

  return (
    <div className="flex h-[calc(100vh-7rem)] gap-3">
      {/* 会话列表 */}
      <aside className="hidden md:flex w-56 shrink-0 flex-col rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-900">
        <button
          onClick={() => createM.mutate()}
          disabled={createM.isPending}
          className="mb-2 rounded-md bg-blue-600 px-2 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          ＋ 新对话
        </button>
        <div className="flex-1 overflow-y-auto">
          {threads.map((t: Thread) => (
            <div
              key={t.id}
              role="button"
              tabIndex={0}
              onClick={() => selectThread(t.id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  selectThread(t.id)
                }
              }}
              className={`group mb-1 flex w-full cursor-pointer items-center gap-1 rounded-md px-2 py-1.5 text-left text-sm ${
                activeId === t.id ? 'bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300' : 'hover:bg-slate-100 dark:hover:bg-slate-800'
              }`}
            >
              <span className="truncate flex-1">{t.title || `对话 ${t.id}`}</span>
              {t.running && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-green-500" title="运行中" />}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  // iter 346（F4）：重命名会话（后端 PATCH /threads/{id} 已就绪）
                  const next = window.prompt('重命名对话', t.title || '')
                      if (next && next.trim() && next.trim() !== (t.title || '')) {
                        // iter 388（R31）：重命名失败改 toast——setError 仅在选中会话时可见，
                        // activeId 为 null（刚进页面在列表直接重命名）时失败完全不可见
                        patchThread(t.id, { title: next.trim() })
                          .then(() => queryClient.invalidateQueries({ queryKey: ['threads'] }))
                          .catch((err) => toast.error(err instanceof Error ? err.message : '重命名失败，请重试'))
                      }
                }}
                className="hidden shrink-0 rounded px-1 py-0.5 text-xs text-slate-400 hover:bg-slate-200 hover:text-slate-700 group-hover:block dark:hover:bg-slate-700 dark:hover:text-slate-200"
                aria-label={`重命名对话「${t.title || t.id}」`}
                title="重命名"
              >
                ✏️
              </button>
            </div>
          ))}
          {threads.length < threadsTotal && (
            <button
              onClick={() => void threadsQ.fetchNextPage()}
              disabled={!threadsQ.hasNextPage || threadsQ.isFetchingNextPage}
              className="mb-1 w-full rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800"
            >
              {threadsQ.isFetchingNextPage ? '加载中…' : `加载更多（${threads.length}/${threadsTotal}）`}
            </button>
          )}
          {/* iter 388（R31）：列表加载失败不得落「尚无对话」空态（假信息——用户会以为
              会话全丢）；错误卡 + 重试，与 Notes/Plans 同口径 */}
          {threadsQ.isError && (
            <div role="alert" className="mb-1 rounded-md border border-red-200 bg-red-50 px-2 py-1.5 text-xs text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              <p>会话列表加载失败：{threadsQ.error instanceof Error ? threadsQ.error.message : '未知错误'}</p>
              <button
                onClick={() => void threadsQ.refetch()}
                className="mt-1 rounded border border-red-300 px-1.5 py-0.5 transition-colors hover:bg-red-100 dark:border-red-800 dark:hover:bg-red-900"
              >
                重试
              </button>
            </div>
          )}
          {!threadsQ.isError && threads.length === 0 && <p className="px-2 py-4 text-xs text-slate-400">尚无对话，点上方新建</p>}
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
              <span className="truncate text-sm font-medium">{threads.find((x) => x.id === activeId)?.title || `对话 ${activeId}`}</span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setShowMemory((v) => !v)}
                  className="rounded px-2 py-0.5 text-xs text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800"
                >
                  🧠 记忆
                </button>
                <button
                  onClick={() => {
                    if (deletingThread) return
                    if (confirm('删除此对话及其全部记录？')) {
                      setDeletingThread(true)
                      deleteThread(activeId)
                        .then(() => {
                          queryClient.invalidateQueries({ queryKey: ['threads'] })
                          setActiveId(null)
                        })
                        .catch((e) => setError(e instanceof Error ? e.message : '删除失败，请重试'))
                        .finally(() => setDeletingThread(false))
                    }
                  }}
                  disabled={deletingThread}
                  className="rounded px-2 py-0.5 text-xs text-red-500 hover:bg-red-50 disabled:opacity-50 dark:hover:bg-red-950"
                >
                  {deletingThread ? '删除中…' : '删除'}
                </button>
              </div>
            </header>

            {showMemory && (
              <MemoryPanel items={memQ.data?.items ?? []} total={memQ.data?.total ?? 0} loading={memQ.isLoading} />
            )}

            <div ref={scrollRef} onScroll={onScrollMsgs} className="flex-1 space-y-3 overflow-y-auto p-3">
              {/* iter 388（R31）：消息加载失败此前渲染成空白会话（messages 回退 [] 且无任何
                  提示）——错误卡 + 重试，不用假空白掩盖 */}
              {msgsQ.isError && (
                <div role="alert" className="mx-auto max-w-md rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
                  <p>消息加载失败：{msgsQ.error instanceof Error ? msgsQ.error.message : '未知错误'}</p>
                  <button
                    onClick={() => void msgsQ.refetch()}
                    className="mt-1 rounded border border-red-300 px-1.5 py-0.5 text-xs transition-colors hover:bg-red-100 dark:border-red-800 dark:hover:bg-red-900"
                  >
                    重试
                  </button>
                </div>
              )}
              {messages.length >= 100 && !noMoreMsgs && (
                <button
                  onClick={() => void loadEarlier()}
                  disabled={loadingEarlier}
                  className="mx-auto block rounded-md border border-slate-200 px-3 py-1 text-xs text-slate-500 hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800"
                >
                  {loadingEarlier ? '加载中…' : '加载更早的消息'}
                </button>
              )}
              {messages.map((m: ChatMessage) => (
                <MessageBubble key={m.id} role={m.role} content={m.content} />
              ))}
              {/* 乐观渲染的用户消息：发送即显示，done 后由消息刷新取代（iter 360 BUG-5） */}
              {pendingUser && <MessageBubble role="user" content={pendingUser} />}
              {streaming && <StreamingBubble stream={streaming} />}
              {error && <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-950 dark:text-red-400">{error}</p>}
              <div ref={bottomRef} />
            </div>

            <div className="border-t border-slate-200 p-2 dark:border-slate-700">
              <div className="flex gap-2">
                <textarea
                  aria-label="对话输入"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
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

// iter 369 B16：memo 化——流式期间 stream.text 每 delta 触发整页重渲染，长会话下
// 全部历史气泡（含 Markdown 重解析）反复重算；消息列表不变时跳过。
const MessageBubble = memo(function MessageBubble({ role, content }: { role: string; content: unknown }) {
  const text = blocksText(content)
  const thinking = blocksThinking(content)
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
        {!isUser && thinking && (
          <details className="mb-1 rounded bg-slate-200/60 px-2 py-1 text-xs text-slate-500 dark:bg-slate-700/60">
            <summary className="cursor-pointer">💭 思考过程</summary>
            <div className="mt-1 whitespace-pre-wrap">{thinking}</div>
          </details>
        )}
        {isUser ? (
          <div className="whitespace-pre-wrap break-words">{text}</div>
        ) : (
          <div className="markdown-body break-words">
            <Markdown>{text}</Markdown>
          </div>
        )}
      </div>
    </div>
  )
})

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
        <div className="markdown-body break-words">
          <Markdown>{stream.text}</Markdown>
          <span className="ml-0.5 inline-block h-3 w-1.5 animate-pulse bg-slate-400 align-middle" />
        </div>
      </div>
    </div>
  )
}

function MemoryPanel({ items, total, loading }: { items: MemoryItem[]; total: number; loading: boolean }) {
  // 速览小窗只渲染最近 50 条（后端列表本身已封顶 200 + 给全量 total）；条数多时全量渲染
  // 会把 160px 高的小窗塞进上千节点
  const shown = items.slice(0, 50)
  return (
    <div className="max-h-40 overflow-y-auto border-b border-slate-200 bg-slate-50 px-3 py-2 text-xs dark:border-slate-700 dark:bg-slate-800/50">
      <p className="mb-1 font-medium text-slate-500">记忆库（{total}）</p>
      {loading ? (
        <p className="text-slate-400">加载中…</p>
      ) : shown.length === 0 ? (
        <p className="text-slate-400">暂无记忆。助手会按需保存要点。</p>
      ) : (
        <>
          <ul className="space-y-1">
            {shown.map((m) => (
              <li key={m.id} className="rounded bg-white px-2 py-1 dark:bg-slate-800">
                <span className="mr-1 text-[10px] text-slate-400">[{m.kind}]</span>
                {m.content.slice(0, 120)}
              </li>
            ))}
          </ul>
          {total > shown.length && (
            <p className="mt-1 text-[10px] text-slate-400">仅显示最近 {shown.length} 条（共 {total} 条）</p>
          )}
        </>
      )}
    </div>
  )
}

// 知识库问答面板（iter 220 持久化版）：历史记录服务端持久化（kb_chat_records），
// 打开面板即加载，便于日后参考；发送成功失效刷新历史；失败（无答案）不入库、本地临时展示。
// 文档 chips 多选限定问答范围（缺省=全部库内文档）、引用 chips 点击打开查看器、
// 错误特化（no_llm_provider→引导设置页配供应商；budget_exceeded→提示调整日预算）。
import { useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import { toast } from './Toast'
import {
  ApiError,
  deleteKbChat,
  kbChat,
  listKbChats,
  type KbCitation,
  type KbDocument,
} from '../lib/api'

// 问答范围上限（后端 KbChatIn document_ids max_length=20 对齐）
const MAX_CHAT_DOCS = 20
// 历史加载上限（后端 limit ≤200；超出翻页留作后续增强）
const HISTORY_LIMIT = 200

interface EphemeralError {
  text: string
  code: string
}

interface Props {
  docs: KbDocument[]
  onOpenDoc: (doc: KbDocument) => void
}

export default function KbChatPanel({ docs, onOpenDoc }: Props) {
  const qc = useQueryClient()
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [pendingQ, setPendingQ] = useState<string | null>(null)
  const [errors, setErrors] = useState<EphemeralError[]>([])
  // 空集 = 全部文档（不传 document_ids）；非空 = 仅选中篇目
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [pickerOpen, setPickerOpen] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)

  const { data } = useQuery({
    queryKey: ['kb-chats'],
    queryFn: () => listKbChats({ limit: HISTORY_LIMIT }),
    staleTime: 10_000,
  })
  // 服务端新→旧，展示翻转为旧→新（聊天流阅读习惯）
  const records = useMemo(() => [...(data?.items ?? [])].reverse(), [data])

  function scrollToBottom() {
    requestAnimationFrame(() => {
      listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
    })
  }

  function togglePick(id: number) {
    setPicked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else {
        if (next.size >= MAX_CHAT_DOCS) {
          toast.error(`一次问答最多限定 ${MAX_CHAT_DOCS} 篇文档`)
          return prev
        }
        next.add(id)
      }
      return next
    })
  }

  async function send() {
    const question = input.trim()
    if (!question || busy) return
    setInput('')
    setBusy(true)
    setPendingQ(question)
    scrollToBottom()
    try {
      await kbChat(question, picked.size ? [...picked] : undefined)
      qc.invalidateQueries({ queryKey: ['kb-chats'] }).then(scrollToBottom)
    } catch (e) {
      const code = e instanceof ApiError ? e.code : 'unknown'
      const text =
        code === 'no_llm_provider'
          ? '尚未配置 LLM 供应商'
          : code === 'budget_exceeded'
            ? '今日 LLM 额度已用尽'
            : e instanceof ApiError
              ? e.message
              : '问答失败，请重试'
      setErrors((prev) => [...prev, { text, code }])
      scrollToBottom()
    } finally {
      setBusy(false)
      setPendingQ(null)
    }
  }

  async function onDeleteRecord(id: number, question: string) {
    if (!window.confirm(`确定删除这条问答记录？\n问：${question.slice(0, 50)}${question.length > 50 ? '…' : ''}`)) return
    try {
      await deleteKbChat(id)
      toast.success('记录已删除')
      qc.invalidateQueries({ queryKey: ['kb-chats'] })
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '删除失败')
    }
  }

  function openCitation(c: KbCitation) {
    const d = docs.find((x) => x.id === c.doc_id)
    if (d) onOpenDoc(d)
    else toast.info(`文档「${c.title}」已不在当前列表（可能已删除或被筛选）`)
  }

  return (
    <section
      aria-label="知识库问答"
      className="animate-fade-in flex flex-col rounded-xl border border-violet-200 bg-white shadow-sm dark:border-violet-900 dark:bg-slate-800"
    >
      <header className="flex flex-wrap items-center gap-2 border-b px-4 py-2.5 dark:border-slate-700">
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">💬 知识库问答</h3>
        <button
          onClick={() => setPickerOpen((o) => !o)}
          aria-expanded={pickerOpen}
          className="rounded-full bg-violet-50 px-2.5 py-0.5 text-xs text-violet-700 transition-colors hover:bg-violet-100 dark:bg-violet-950 dark:text-violet-300 dark:hover:bg-violet-900"
        >
          {picked.size ? `范围：已选 ${picked.size} 篇 ▾` : `范围：全部 ${docs.length} 篇 ▾`}
        </button>
        <p className="ml-auto text-xs text-slate-400">
          问答自动保存{data ? ` · 共 ${data.total} 条` : ''}
        </p>
      </header>

      {/* 文档范围 chips 多选 */}
      {pickerOpen && (
        <div className="animate-fade-in border-b px-4 py-2 dark:border-slate-700">
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              onClick={() => setPicked(new Set())}
              disabled={picked.size === 0}
              className="rounded-full border px-2 py-0.5 text-xs transition-all hover:bg-slate-50 active:scale-95 disabled:opacity-40 dark:border-slate-600 dark:hover:bg-slate-700"
            >
              重置为全部
            </button>
            {docs
              .filter((d) => d.status === 'parsed') // iter 225：未解析文档无文本可答，不出现在范围选择
              .map((d) => (
                <button
                  key={d.id}
                  onClick={() => togglePick(d.id)}
                  aria-pressed={picked.has(d.id)}
                  title={d.title}
                  className={`max-w-40 truncate rounded-full px-2 py-0.5 text-xs transition-all active:scale-95 ${
                    picked.has(d.id)
                      ? 'bg-violet-600 text-white shadow-sm'
                      : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-300 dark:hover:bg-slate-600'
                  }`}
                >
                  {d.title}
                </button>
              ))}
          </div>
          <p className="mt-1 text-xs text-slate-400">
            不选任何篇目 = 基于全部文档作答；点选后仅依据所选篇目（≤{MAX_CHAT_DOCS} 篇）
          </p>
        </div>
      )}

      <div ref={listRef} className="max-h-96 min-h-24 space-y-3 overflow-y-auto px-4 py-3">
        {records.length === 0 && !pendingQ && errors.length === 0 && (
          <p className="py-4 text-center text-xs text-slate-400">
            向你的知识库提问——AI 仅依据库内文档回答并标注出处；问答记录自动保存，便于日后参考
          </p>
        )}
        {records.map((r) => (
          <div key={r.id} className="group animate-fade-in space-y-2">
            <div className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl bg-blue-600 px-3.5 py-2 text-sm text-white shadow-sm">
                <p className="whitespace-pre-wrap">{r.question}</p>
              </div>
            </div>
            <div className="flex justify-start">
              <div className="max-w-[85%] rounded-2xl border bg-slate-50 px-3.5 py-2 text-sm text-slate-800 shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100">
                <div className="markdown-body text-sm">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
                    {r.answer}
                  </ReactMarkdown>
                </div>
                {r.citations.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5 border-t border-slate-200 pt-2 dark:border-slate-700">
                    <span className="text-xs text-slate-400">出处：</span>
                    {r.citations.map((c) => (
                      <button
                        key={c.doc_id}
                        onClick={() => openCitation(c)}
                        title={`查看「${c.title}」`}
                        className="rounded-full bg-violet-100 px-2 py-0.5 text-xs text-violet-700 transition-colors hover:bg-violet-200 dark:bg-violet-950 dark:text-violet-300 dark:hover:bg-violet-900"
                      >
                        📄 {c.title}
                      </button>
                    ))}
                  </div>
                )}
                {r.omitted_titles.length > 0 && (
                  <p className="mt-1.5 text-xs text-amber-600 dark:text-amber-400">
                    上下文超长，以下 {r.omitted_titles.length} 篇未纳入本轮：{r.omitted_titles.join('、')}
                  </p>
                )}
                <div className="mt-1.5 flex items-center gap-2 text-xs text-slate-400">
                  <time dateTime={r.created_at}>{r.created_at.slice(5, 16).replace('T', ' ')}</time>
                  <button
                    onClick={() => onDeleteRecord(r.id, r.question)}
                    aria-label="删除这条问答记录"
                    className="rounded px-1 text-slate-300 opacity-0 transition-all hover:text-red-500 group-hover:opacity-100 dark:text-slate-600 dark:hover:text-red-400"
                  >
                    🗑 删除
                  </button>
                </div>
              </div>
            </div>
          </div>
        ))}
        {pendingQ && (
          <div className="animate-fade-in space-y-2">
            <div className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl bg-blue-600 px-3.5 py-2 text-sm text-white shadow-sm">
                <p className="whitespace-pre-wrap">{pendingQ}</p>
              </div>
            </div>
            <div className="flex justify-start">
              <div className="rounded-2xl border bg-slate-50 px-3.5 py-2 text-sm text-slate-400 dark:border-slate-700 dark:bg-slate-900">
                思考中…
              </div>
            </div>
          </div>
        )}
        {errors.map((err, i) => (
          <div key={i} className="animate-fade-in flex justify-start">
            <div className="max-w-[85%] rounded-2xl border border-red-200 bg-red-50 px-3.5 py-2 text-sm text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              <p>⚠️ {err.text}</p>
              {(err.code === 'no_llm_provider' || err.code === 'budget_exceeded') && (
                <p className="mt-1 text-xs">
                  请到{' '}
                  <Link
                    to="/settings"
                    className="font-medium underline underline-offset-2 hover:text-red-800 dark:hover:text-red-200"
                  >
                    设置页
                  </Link>
                  {err.code === 'no_llm_provider' ? ' 配置 LLM 供应商（API Key）后再提问。' : ' 调整日预算，或明天再试。'}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="flex items-end gap-2 border-t px-3 py-2.5 dark:border-slate-700">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send()
            }
          }}
          placeholder={docs.length ? '输入问题，Enter 发送（Shift+Enter 换行）' : '文档库为空，请先入库文档'}
          disabled={docs.length === 0 || busy}
          rows={1}
          maxLength={4000}
          aria-label="问答输入"
          className="max-h-32 min-h-9 flex-1 resize-y rounded-lg border px-3 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-violet-500/60 disabled:opacity-50 dark:border-slate-600 dark:bg-slate-900"
        />
        <button
          onClick={send}
          disabled={busy || !input.trim() || docs.length === 0}
          className="shrink-0 rounded-lg bg-violet-600 px-4 py-1.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-violet-700 active:scale-[.98] disabled:opacity-50"
        >
          {busy ? '…' : '发送'}
        </button>
      </div>
    </section>
  )
}

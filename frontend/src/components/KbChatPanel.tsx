// 知识库问答面板（iter 218 正式版）：本地内存消息（不持久化，刷新即清）、
// 文档 chips 多选限定问答范围（缺省=全部库内文档）、引用 chips 点击打开查看器、
// 错误特化（no_llm_provider→引导设置页配供应商；budget_exceeded→提示调整日预算）。
import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import { toast } from './Toast'
import { ApiError, kbChat, type KbCitation, type KbDocument } from '../lib/api'

// 问答范围上限（后端 KbChatIn document_ids max_length=20 对齐）
const MAX_CHAT_DOCS = 20

interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
  citations?: KbCitation[]
  omitted?: string[]
  errorCode?: string
}

interface Props {
  docs: KbDocument[]
  onOpenDoc: (doc: KbDocument) => void
}

export default function KbChatPanel({ docs, onOpenDoc }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  // 空集 = 全部文档（不传 document_ids）；非空 = 仅选中篇目
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [pickerOpen, setPickerOpen] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)

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
    setMessages((prev) => [...prev, { role: 'user', text: question }])
    scrollToBottom()
    try {
      const r = await kbChat(question, picked.size ? [...picked] : undefined)
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', text: r.answer, citations: r.citations, omitted: r.omitted_titles },
      ])
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
      setMessages((prev) => [...prev, { role: 'assistant', text, errorCode: code }])
    } finally {
      setBusy(false)
      scrollToBottom()
    }
  }

  function openCitation(c: KbCitation) {
    const d = docs.find((x) => x.id === c.doc_id)
    if (d) onOpenDoc(d)
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
        <p className="ml-auto text-xs text-slate-400">历史不保存，刷新即清</p>
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
            {docs.map((d) => (
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
        {messages.length === 0 && (
          <p className="py-4 text-center text-xs text-slate-400">
            向你的知识库提问——AI 仅依据库内文档回答并标注出处
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`animate-fade-in flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[85%] rounded-2xl px-3.5 py-2 text-sm shadow-sm ${
                m.role === 'user'
                  ? 'bg-blue-600 text-white'
                  : m.errorCode
                    ? 'border border-red-200 bg-red-50 text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-400'
                    : 'border bg-slate-50 text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100'
              }`}
            >
              {m.role === 'user' ? (
                <p className="whitespace-pre-wrap">{m.text}</p>
              ) : (
                <div className="markdown-body text-sm">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
                    {m.text}
                  </ReactMarkdown>
                </div>
              )}
              {m.errorCode === 'no_llm_provider' && (
                <p className="mt-1 text-xs">
                  请到{' '}
                  <Link to="/settings" className="font-medium underline underline-offset-2 hover:text-red-800 dark:hover:text-red-200">
                    设置页
                  </Link>{' '}
                  配置 LLM 供应商（API Key）后再提问。
                </p>
              )}
              {m.errorCode === 'budget_exceeded' && (
                <p className="mt-1 text-xs">
                  请到{' '}
                  <Link to="/settings" className="font-medium underline underline-offset-2 hover:text-red-800 dark:hover:text-red-200">
                    设置页
                  </Link>{' '}
                  调整日预算，或明天再试。
                </p>
              )}
              {m.citations && m.citations.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5 border-t border-slate-200 pt-2 dark:border-slate-700">
                  <span className="text-xs text-slate-400">出处：</span>
                  {m.citations.map((c) => (
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
              {m.omitted && m.omitted.length > 0 && (
                <p className="mt-1.5 text-xs text-amber-600 dark:text-amber-400">
                  上下文超长，以下 {m.omitted.length} 篇未纳入本轮：{m.omitted.join('、')}
                </p>
              )}
            </div>
          </div>
        ))}
        {busy && (
          <div className="flex justify-start">
            <div className="rounded-2xl border bg-slate-50 px-3.5 py-2 text-sm text-slate-400 dark:border-slate-700 dark:bg-slate-900">
              思考中…
            </div>
          </div>
        )}
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

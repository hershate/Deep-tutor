// 知识库问答面板（iter 217 壳版）：本地内存消息（不持久化，刷新即清）、
// 缺省用全部库内文档作答、引用 chips 点击打开查看器。
// iter 218 升级：文档 chips 多选 + 错误特化（no_llm_provider/budget_exceeded）。
import { useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import { ApiError, kbChat, type KbCitation, type KbDocument } from '../lib/api'

interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
  citations?: KbCitation[]
  omitted?: string[]
  error?: boolean
}

interface Props {
  docs: KbDocument[]
  onOpenDoc: (doc: KbDocument) => void
}

export default function KbChatPanel({ docs, onOpenDoc }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const listRef = useRef<HTMLDivElement>(null)

  function scrollToBottom() {
    requestAnimationFrame(() => {
      listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
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
      const r = await kbChat(question)
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', text: r.answer, citations: r.citations, omitted: r.omitted_titles },
      ])
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : '问答失败，请重试'
      setMessages((prev) => [...prev, { role: 'assistant', text: `⚠️ ${msg}`, error: true }])
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
      <header className="flex items-center justify-between border-b px-4 py-2.5 dark:border-slate-700">
        <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">💬 知识库问答</h3>
        <p className="text-xs text-slate-400">基于全部 {docs.length} 篇文档作答 · 历史不保存，刷新即清</p>
      </header>

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
                  : m.error
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

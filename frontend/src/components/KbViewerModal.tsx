// 知识库文档查看器（iter 217 壳版）：全部类型先统一走「解析文本」视图——
// 每种文档入库时必然产出 parsed 文本，因此壳版即可查看所有文档。
// iter 218 升级为 blob-URL 原生渲染（pdf iframe / img / audio / video）。
import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import { toast } from './Toast'
import { ApiError, downloadKbRaw, getKbParsed, type KbDocument, type KbParsed } from '../lib/api'

interface Props {
  doc: KbDocument
  docs: KbDocument[] // 同列表上下文：供上/下一篇导航
  onClose: () => void
  onNavigate: (doc: KbDocument) => void
}

export default function KbViewerModal({ doc, docs, onClose, onNavigate }: Props) {
  const [parsed, setParsed] = useState<KbParsed | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  // Esc 关闭（仿 QuizModal）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // 切换文档时重新拉取解析文本
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadError(null)
    setParsed(null)
    getKbParsed(doc.id)
      .then((p) => {
        if (!cancelled) setParsed(p)
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e instanceof ApiError ? e.message : '解析文本加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [doc.id])

  const idx = docs.findIndex((d) => d.id === doc.id)
  const prev = idx > 0 ? docs[idx - 1] : null
  const next = idx >= 0 && idx < docs.length - 1 ? docs[idx + 1] : null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`查看文档：${doc.title}`}
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-3xl animate-fade-in flex-col overflow-hidden rounded-2xl bg-white shadow-2xl dark:bg-slate-800"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部：标题 + 导航 + 下载 + 关闭 */}
        <div className="flex items-center gap-2 border-b px-4 py-3 dark:border-slate-700">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{doc.title}</h2>
            <p className="mt-0.5 truncate text-xs text-slate-400">
              {doc.kind}
              {doc.ext ? ` · ${doc.ext}` : ''} · 来源：{doc.source}
            </p>
          </div>
          <button
            onClick={() => prev && onNavigate(prev)}
            disabled={!prev}
            title="上一篇"
            className="rounded-lg border px-2 py-1 text-sm transition-all hover:bg-slate-50 active:scale-95 disabled:opacity-40 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            ←
          </button>
          <button
            onClick={() => next && onNavigate(next)}
            disabled={!next}
            title="下一篇"
            className="rounded-lg border px-2 py-1 text-sm transition-all hover:bg-slate-50 active:scale-95 disabled:opacity-40 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            →
          </button>
          {doc.has_original && (
            <button
              onClick={() => {
                downloadKbRaw(doc.id).catch((e) =>
                  toast.error(e instanceof ApiError ? e.message : '下载失败'),
                )
              }}
              className="rounded-lg border px-2.5 py-1 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
            >
              ⬇ 原件
            </button>
          )}
          <button
            onClick={onClose}
            aria-label="关闭"
            className="rounded-lg px-2 py-1 text-lg leading-none text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-700"
          >
            ×
          </button>
        </div>

        {/* 正文：解析文本 Markdown 视图 */}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {loading && (
            <div className="space-y-2" aria-label="加载中">
              <div className="h-4 w-3/4 animate-pulse rounded bg-slate-100 dark:bg-slate-700" />
              <div className="h-4 w-full animate-pulse rounded bg-slate-100 dark:bg-slate-700" />
              <div className="h-4 w-5/6 animate-pulse rounded bg-slate-100 dark:bg-slate-700" />
            </div>
          )}
          {loadError && (
            <p
              role="alert"
              className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-400"
            >
              {loadError}
            </p>
          )}
          {parsed && (
            <>
              {parsed.truncated && (
                <p className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-700 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-400">
                  解析文本较长，已截断显示（共 {parsed.parsed_chars.toLocaleString()} 字）
                </p>
              )}
              <div className="markdown-body text-sm">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
                  {parsed.text || '（无文本内容）'}
                </ReactMarkdown>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

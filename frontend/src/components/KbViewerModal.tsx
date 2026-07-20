// 知识库文档查看器（iter 218 正式版）：按类型分派渲染——
//   pdf → iframe blob（浏览器原生 PDF 查看器，免 pdfjs）
//   图片 → <img> blob；音频/视频 → 原生 <audio>/<video controls> blob
//   其余（txt/md/docx/pptx/text/url 或无原件）→ 解析文本 Markdown 视图
// 鉴权：img/iframe/video 不能带 Authorization 头，一律 fetch 带 Bearer 取 blob
//   → URL.createObjectURL；切文档/卸载时 AbortController 中止 + revokeObjectURL。
// blob 加载失败（或浏览器禁用内嵌 PDF）自动退化解析文本视图 + 固定「下载原件」兜底。
import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import { toast } from './Toast'
import { ApiError, downloadKbRaw, fetchKbRawBlob, getKbParsed, type KbDocument, type KbParsed } from '../lib/api'

type ViewMode = 'pdf' | 'image' | 'audio' | 'video' | 'parsed'

function viewModeOf(doc: KbDocument): ViewMode {
  if (doc.has_original) {
    if (doc.ext === 'pdf') return 'pdf'
    if (doc.kind === 'image') return 'image'
    if (doc.kind === 'audio') return 'audio'
    if (doc.kind === 'video') return 'video'
  }
  return 'parsed'
}

interface Props {
  doc: KbDocument
  docs: KbDocument[] // 同列表上下文：供上/下一篇导航
  onClose: () => void
  onNavigate: (doc: KbDocument) => void
}

export default function KbViewerModal({ doc, docs, onClose, onNavigate }: Props) {
  const mode = viewModeOf(doc)
  const [objectUrl, setObjectUrl] = useState<string | null>(null)
  const [blobFailed, setBlobFailed] = useState(false)
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

  // 切换文档时重载：blob 模式取原件，parsed 模式取解析文本
  useEffect(() => {
    const ac = new AbortController()
    setLoading(true)
    setLoadError(null)
    setBlobFailed(false)
    setParsed(null)
    setObjectUrl(null)
    if (mode === 'parsed') {
      // iter 225：未解析文档直接引导，不发 404 请求
      if (doc.status && doc.status !== 'parsed') {
        setLoadError('该文档尚未解析——请先在文档列表中确认并解析')
        setLoading(false)
      } else {
        getKbParsed(doc.id)
          .then((p) => !ac.signal.aborted && setParsed(p))
          .catch((e) => {
            if (!ac.signal.aborted) setLoadError(e instanceof ApiError ? e.message : '解析文本加载失败')
          })
          .finally(() => !ac.signal.aborted && setLoading(false))
      }
    } else {
      fetchKbRawBlob(doc.id, ac.signal)
        .then((blob) => {
          if (ac.signal.aborted) return
          setObjectUrl(URL.createObjectURL(blob))
        })
        .catch(() => {
          if (ac.signal.aborted) return
          // blob 失败退化解析文本视图（仍可阅读），并提示可下载原件
          setBlobFailed(true)
          getKbParsed(doc.id)
            .then((p) => !ac.signal.aborted && setParsed(p))
            .catch((pe) => {
              if (!ac.signal.aborted)
                setLoadError(pe instanceof ApiError ? pe.message : '原件与解析文本均加载失败')
            })
            .finally(() => !ac.signal.aborted && setLoading(false))
        })
    }
    return () => {
      ac.abort()
      setObjectUrl((url) => {
        if (url) URL.revokeObjectURL(url)
        return null
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc.id, mode])

  // blob 成功到达后结束 loading（objectUrl 设置即渲染）
  useEffect(() => {
    if (objectUrl) setLoading(false)
  }, [objectUrl])

  const idx = docs.findIndex((d) => d.id === doc.id)
  const prev = idx > 0 ? docs[idx - 1] : null
  const next = idx >= 0 && idx < docs.length - 1 ? docs[idx + 1] : null
  const showParsed = mode === 'parsed' || blobFailed

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`查看文档：${doc.title}`}
      onClick={onClose}
    >
      <div
        className="flex max-h-[88vh] w-full max-w-4xl animate-fade-in flex-col overflow-hidden rounded-2xl bg-white shadow-2xl dark:bg-slate-800"
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

        {/* 正文：按模式分派 */}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {blobFailed && (
            <p className="shrink-0 border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-xs text-amber-700 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-400">
              原件内嵌预览不可用，已回退解析文本视图——可点「⬇ 原件」下载后用本地应用打开
            </p>
          )}
          {loading && (
            <div className="space-y-2 overflow-y-auto px-5 py-4" aria-label="加载中">
              <div className="h-4 w-3/4 animate-pulse rounded bg-slate-100 dark:bg-slate-700" />
              <div className="h-4 w-full animate-pulse rounded bg-slate-100 dark:bg-slate-700" />
              <div className="h-4 w-5/6 animate-pulse rounded bg-slate-100 dark:bg-slate-700" />
            </div>
          )}
          {!loading && loadError && (
            <p
              role="alert"
              className="m-5 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-400"
            >
              {loadError}
            </p>
          )}
          {!loading && !showParsed && objectUrl && mode === 'pdf' && (
            <iframe title={`PDF 预览：${doc.title}`} src={objectUrl} className="min-h-0 flex-1" />
          )}
          {!loading && !showParsed && objectUrl && mode === 'image' && (
            <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto bg-slate-50 p-4 dark:bg-slate-900">
              <img src={objectUrl} alt={doc.title} className="max-h-full max-w-full rounded-lg object-contain shadow" />
            </div>
          )}
          {!loading && !showParsed && objectUrl && mode === 'audio' && (
            <div className="flex flex-1 items-center justify-center p-8">
              <audio controls src={objectUrl} className="w-full max-w-md">
                您的浏览器不支持音频播放
              </audio>
            </div>
          )}
          {!loading && !showParsed && objectUrl && mode === 'video' && (
            <div className="flex min-h-0 flex-1 items-center justify-center bg-black p-2">
              <video controls src={objectUrl} className="max-h-full max-w-full">
                您的浏览器不支持视频播放
              </video>
            </div>
          )}
          {!loading && showParsed && parsed && (
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
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
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

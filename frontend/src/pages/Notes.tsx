// 独立自由笔记页（iter 227）：不依附方案的笔记本——左列表右编辑器双栏。
// 列表 content 为 300 字预览，点选后 GET 全文进编辑器；正文支持 Markdown 预览切换；
// 保存=新建 POST / 更新 PUT；删除有确认。空标题且空正文后端 400，前端先拦截提示。
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import {
  ApiError,
  createFreeNote,
  deleteFreeNote,
  getFreeNote,
  listFreeNotes,
  saveTextFile,
  updateFreeNote,
  type FreeNote,
} from '../lib/api'
import { SkeletonLines } from '../components/Skeleton'
import { toast } from '../components/Toast'

interface Draft {
  id: number | null // null = 新建未保存
  title: string
  content: string
  updatedAt: string | null
}

const EMPTY_DRAFT: Draft = { id: null, title: '', content: '', updatedAt: null }

export default function Notes() {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [dirty, setDirty] = useState(false) // 有未保存修改（切换/新建前确认防静默丢失）
  const [preview, setPreview] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loadingId, setLoadingId] = useState<number | null>(null)
  const openSeq = useRef(0) // iter 248：openNote 在途序号（迟到响应丢弃）

  const { data, isLoading, error } = useQuery({
    queryKey: ['free-notes'],
    queryFn: listFreeNotes,
    staleTime: 10_000,
  })

  // iter 249：深链 /notes?note=<id>（全局搜索结果直达）——列表加载后打开该笔记；
  // 参数即消费（replace 不堆历史），目标不存在（已删）仅清参数不报错
  const [params, setParams] = useSearchParams()
  const deepLinkConsumed = useRef<number | null>(null)
  useEffect(() => {
    const raw = params.get('note')
    if (!raw || !data?.items) return
    const id = Number(raw)
    if (!Number.isInteger(id) || deepLinkConsumed.current === id) return
    deepLinkConsumed.current = id
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.delete('note')
        return next
      },
      { replace: true },
    )
    const target = data.items.find((n) => n.id === id)
    if (target) void openNote(target)
    // openNote 为本渲染闭包（一次性消费，无需依赖）；data 变化仅在参数未消费时有意义
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, data])

  function refresh() {
    return qc.invalidateQueries({ queryKey: ['free-notes'] })
  }

  // 有未保存修改时切换对象需用户确认（防草稿被静默覆盖）
  function confirmDiscard(): boolean {
    return !dirty || window.confirm('当前笔记有未保存的修改，继续将丢失这些修改。确定继续？')
  }

  async function openNote(n: FreeNote) {
    if (busy || loadingId !== null) return
    if (draft.id !== n.id && !confirmDiscard()) return
    // iter 248：在途序号——打开 A 期间点「新建」，A 的迟到响应不得覆写新草稿
    const seq = ++openSeq.current
    setLoadingId(n.id)
    try {
      const full = await getFreeNote(n.id) // 列表仅 300 字预览，取全文编辑
      if (seq !== openSeq.current) return // 已有更新的选择（新建/另开），丢弃迟到响应
      setDraft({ id: full.id, title: full.title, content: full.content, updatedAt: full.updated_at })
      setDirty(false)
      setPreview(false)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '笔记加载失败')
    } finally {
      setLoadingId((cur) => (cur === n.id ? null : cur))
    }
  }

  async function save() {
    if (busy) return
    if (!draft.title.trim() && !draft.content.trim()) {
      toast.error('标题与正文不可同时为空')
      return
    }
    setBusy(true)
    try {
      const saved = draft.id
        ? await updateFreeNote(draft.id, draft.title, draft.content)
        : await createFreeNote(draft.title, draft.content)
      setDraft({ id: saved.id, title: saved.title, content: saved.content, updatedAt: saved.updated_at })
      setDirty(false)
      toast.success('笔记已保存')
      await refresh()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '保存失败，请重试')
    } finally {
      setBusy(false)
    }
  }

  // iter 252：单篇导出 .md——导出当前编辑器内容（未保存草稿亦可），
  // 文件名取标题（清洗路径不安全字符），正文前补一级标题
  function exportNote() {
    const title = draft.title.trim()
    const content = draft.content
    if (!title && !content.trim()) return
    const md = title ? `# ${title}\n\n${content}` : content
    const safe = (title || '笔记').replace(/[\\/:*?"<>|\r\n]+/g, '_').trim().slice(0, 50) || '笔记'
    saveTextFile(md, `${safe}.md`)
    toast.success('已导出 Markdown 文件')
  }

  async function remove() {
    if (busy || draft.id === null) return
    if (!window.confirm(`确定删除笔记「${draft.title || '无题'}」？删除后不可恢复。`)) return
    setBusy(true)
    try {
      await deleteFreeNote(draft.id)
      toast.success('笔记已删除')
      setDraft(EMPTY_DRAFT)
      await refresh()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '删除失败，请重试')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="animate-fade-in-up space-y-1">
        <h2 className="bg-gradient-to-r from-blue-600 to-indigo-500 bg-clip-text text-2xl font-bold text-transparent dark:from-blue-400 dark:to-indigo-300">
          我的笔记
        </h2>
        <p className="text-slate-600 dark:text-slate-300">
          自由笔记本——不依附任何方案，随想随记。知识点的批注仍在知识库页面维护。
        </p>
      </div>

      <div className="grid animate-fade-in-up gap-4 md:grid-cols-[280px_1fr]" style={{ animationDelay: '60ms' }}>
        {/* 左：笔记列表 */}
        <section className="rounded-xl border bg-white p-3 shadow-sm dark:border-slate-700 dark:bg-slate-800">
          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-semibold">全部笔记{data ? `（${data.total}）` : ''}</h3>
            <button
              onClick={() => {
                if (!confirmDiscard()) return
                openSeq.current += 1 // 作废在途 openNote 响应（防覆写新草稿）
                setDraft(EMPTY_DRAFT)
                setDirty(false)
                setPreview(false)
              }}
              className="rounded-lg bg-blue-600 px-2.5 py-1 text-xs font-medium text-white shadow-sm transition-all hover:bg-blue-700 active:scale-95"
            >
              ＋ 新建
            </button>
          </div>
          {isLoading && <SkeletonLines lines={3} />}
          {error && (
            <div role="alert" className="flex items-center justify-between gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              <span>列表加载失败：{error instanceof ApiError ? error.message : '网络错误'}</span>
              <button
                onClick={() => refresh()}
                className="shrink-0 rounded border border-red-300 px-2 py-0.5 text-xs transition-colors hover:bg-red-100 dark:border-red-800 dark:hover:bg-red-900"
              >
                重试
              </button>
            </div>
          )}
          {data && data.total === 0 && (
            <p className="px-1 py-4 text-center text-sm text-slate-400">还没有笔记——点「＋ 新建」写下第一条</p>
          )}
          {data && data.total > 0 && (
            <ul className="max-h-[60vh] space-y-1 overflow-y-auto">
              {data.items.map((n) => (
                <li key={n.id}>
                  <button
                    onClick={() => openNote(n)}
                    aria-current={draft.id === n.id}
                    className={`w-full rounded-lg border px-2.5 py-1.5 text-left transition-all hover:border-blue-300 dark:hover:border-blue-700 ${
                      draft.id === n.id
                        ? 'border-blue-400 bg-blue-50 dark:border-blue-700 dark:bg-blue-950'
                        : 'border-transparent bg-slate-50 dark:bg-slate-900'
                    }`}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                        {loadingId === n.id ? '加载中…' : n.title || '无题'}
                      </span>
                      <span className="shrink-0 text-xs text-slate-500 dark:text-slate-400 tabular-nums">
                        {n.updated_at ? n.updated_at.slice(5, 10) : ''}
                      </span>
                    </span>
                    {n.content && (
                      <span className="mt-0.5 line-clamp-2 block text-xs text-slate-500 dark:text-slate-400">
                        {n.content}
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* 右：编辑器 */}
        <section className="rounded-xl border bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800">
          <div className="mb-2 flex items-center gap-2">
            <input
              value={draft.title}
              onChange={(e) => {
                setDraft((d) => ({ ...d, title: e.target.value }))
                setDirty(true)
              }}
              maxLength={256}
              placeholder="标题（可与正文二选一）"
              aria-label="笔记标题"
              className="min-w-0 flex-1 rounded-lg border px-3 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500/60 dark:border-slate-600 dark:bg-slate-900"
            />
            <button
              onClick={() => setPreview((p) => !p)}
              aria-pressed={preview}
              className={`shrink-0 rounded-lg border px-2.5 py-1.5 text-xs transition-all active:scale-95 ${
                preview
                  ? 'border-violet-300 bg-violet-50 text-violet-700 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-300'
                  : 'hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700'
              }`}
            >
              {preview ? '✏️ 编辑' : '👁 预览'}
            </button>
          </div>
          {preview ? (
            <div className="markdown-body min-h-60 rounded-lg border px-3 py-2 text-sm dark:border-slate-600">
              {draft.content.trim() ? (
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
                  {draft.content}
                </ReactMarkdown>
              ) : (
                <p className="text-slate-400">（正文为空）</p>
              )}
            </div>
          ) : (
            <textarea
              value={draft.content}
              onChange={(e) => {
                setDraft((d) => ({ ...d, content: e.target.value }))
                setDirty(true)
              }}
              rows={14}
              maxLength={20_000}
              placeholder="正文（支持 Markdown）…"
              aria-label="笔记正文"
              className="w-full resize-y rounded-lg border px-3 py-2 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500/60 dark:border-slate-600 dark:bg-slate-900"
            />
          )}
          <div className="mt-2 flex items-center justify-between gap-2">
            <span className={`text-xs ${dirty ? 'text-amber-600 dark:text-amber-400' : 'text-slate-400'}`}>
              {dirty
                ? '● 未保存的修改'
                : draft.id === null
                  ? '新笔记（保存后入库）'
                  : draft.updatedAt
                    ? `更新于 ${new Date(draft.updatedAt).toLocaleString()}`
                    : ''}
            </span>
            <div className="flex gap-2">
              <button
                onClick={exportNote}
                disabled={busy || (!draft.title.trim() && !draft.content.trim())}
                title="导出当前内容为 Markdown 文件"
                className="rounded-lg border px-3 py-1.5 text-xs transition-all hover:bg-slate-50 active:scale-95 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-700"
              >
                导出 .md
              </button>
              {draft.id !== null && (
                <button
                  onClick={remove}
                  disabled={busy}
                  className="rounded-lg border border-red-200 px-3 py-1.5 text-xs text-red-600 transition-all hover:bg-red-50 active:scale-95 disabled:opacity-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
                >
                  删除
                </button>
              )}
              <button
                onClick={save}
                disabled={busy}
                className="rounded-lg bg-blue-600 px-4 py-1.5 text-xs font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98] disabled:opacity-50"
              >
                {busy ? '保存中…' : '保存笔记'}
              </button>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}

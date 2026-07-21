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

// iter 272：未保存草稿保护——dirty 此前仅守卫页内切换（confirmDiscard），
// 刷新/关标签/侧栏跳路由均静默丢稿（BrowserRouter 无 useBlocker，迁数据路由器风险不值）。
// 双保险：① dirty 时 beforeunload 浏览器原生确认（刷新/关标签）；
// ② localStorage 自动草稿（500ms 防抖写）——路由跳走回来可恢复。
// 恢复仅当草稿基于同一服务器版本（baseUpdatedAt 一致，防他端已保存后旧草稿复活）；
// 保存/删除/确认放弃时清除；隐私模式等读写失败静默降级（iter 145 同款模式）。
interface StoredDraft {
  title: string
  content: string
  baseUpdatedAt: string | null
  savedAt: string
}
const draftKey = (id: number | null) => `dt-note-draft-${id ?? 'new'}`

function readStoredDraft(id: number | null): StoredDraft | null {
  try {
    const raw = localStorage.getItem(draftKey(id))
    if (!raw) return null
    const p = JSON.parse(raw) as Partial<StoredDraft>
    if (typeof p.title === 'string' && typeof p.content === 'string') {
      return { title: p.title, content: p.content, baseUpdatedAt: p.baseUpdatedAt ?? null, savedAt: p.savedAt ?? '' }
    }
    return null
  } catch {
    return null
  }
}

function writeStoredDraft(id: number | null, d: Draft) {
  try {
    localStorage.setItem(
      draftKey(id),
      JSON.stringify({ title: d.title, content: d.content, baseUpdatedAt: d.updatedAt, savedAt: new Date().toISOString() } satisfies StoredDraft),
    )
  } catch {
    /* 写失败仅失去草稿保护，不影响编辑 */
  }
}

function clearStoredDraft(id: number | null) {
  try {
    localStorage.removeItem(draftKey(id))
  } catch {
    /* 同上 */
  }
}

export default function Notes() {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT)
  const [dirty, setDirty] = useState(false) // 有未保存修改（切换/新建前确认防静默丢失）
  const [preview, setPreview] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loadingId, setLoadingId] = useState<number | null>(null)
  // iter 283：批量选择 + 批量删除（左列表作用域，与 Plans iter 282 同模式）
  const [selectMode, setSelectMode] = useState(false)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [batchDeleting, setBatchDeleting] = useState(false)
  const [batchProgress, setBatchProgress] = useState('')
  const openSeq = useRef(0) // iter 248：openNote 在途序号（迟到响应丢弃）

  // iter 272 ①：刷新/关标签时浏览器原生确认（仅 dirty 期间挂监听）
  useEffect(() => {
    if (!dirty) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [dirty])

  // iter 272 ②：自动草稿（防抖 500ms）——路由跳走/崩溃后回来可恢复
  useEffect(() => {
    if (!dirty) return
    const t = setTimeout(() => writeStoredDraft(draft.id, draft), 500)
    return () => clearTimeout(t)
  }, [draft, dirty])

  // iter 272 ②：进页恢复「新建」草稿（编辑器为空才恢复，静默——无可失内容）
  useEffect(() => {
    const sd = readStoredDraft(null)
    if (sd && (sd.title.trim() || sd.content.trim())) {
      setDraft({ id: null, title: sd.title, content: sd.content, updatedAt: null })
      setDirty(true)
    }
  }, [])

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

  // 有未保存修改时切换对象需用户确认（防草稿被静默覆盖）；
  // iter 272：确认放弃即清本地自动草稿（用户明示丢弃，不应之后复活提示）
  function confirmDiscard(): boolean {
    if (!dirty) return true
    if (!window.confirm('当前笔记有未保存的修改，继续将丢失这些修改。确定继续？')) return false
    clearStoredDraft(draft.id)
    return true
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
      // iter 272：同版本本地草稿提示恢复（baseUpdatedAt 不一致 = 他端已保存，旧草稿静默清除）
      const sd = readStoredDraft(full.id)
      if (sd && sd.baseUpdatedAt === full.updated_at && (sd.title !== full.title || sd.content !== full.content)) {
        const when = sd.savedAt ? sd.savedAt.slice(0, 16).replace('T', ' ') : ''
        if (window.confirm(`检测到该笔记的未保存草稿${when ? `（${when}）` : ''}，恢复吗？\n「确定」恢复草稿，「取消」丢弃草稿使用已保存版本。`)) {
          setDraft({ id: full.id, title: sd.title, content: sd.content, updatedAt: full.updated_at })
          setDirty(true)
          setPreview(false)
          return
        }
      }
      if (sd) clearStoredDraft(full.id)
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
      clearStoredDraft(draft.id) // iter 272：保存成功清草稿（防下次打开复活已存内容）
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
      clearStoredDraft(draft.id) // iter 272：删除即清草稿
      toast.success('笔记已删除')
      setDraft(EMPTY_DRAFT)
      await refresh()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '删除失败，请重试')
    } finally {
      setBusy(false)
    }
  }

  // iter 283：批量删除——左列表作用域选择；顺序 await（同 Plans iter 282，避免并发写争用）；
  // 复用既有 deleteFreeNote，逐条清草稿；若正在编辑的笔记被删则重置编辑器。
  function toggleSelect(id: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  function exitSelectMode() {
    setSelectMode(false)
    setSelected(new Set())
  }
  async function onBatchDelete() {
    const notes = data?.items ?? []
    const targets = notes.filter((n) => selected.has(n.id))
    if (targets.length === 0 || batchDeleting) return
    const dirtyOpen = dirty && draft.id !== null && selected.has(draft.id)
    const preview = targets.map((n) => n.title || '无题').slice(0, 5).join('、')
    const more = targets.length > 5 ? ` 等 ${targets.length} 篇` : ''
    const warn = dirtyOpen ? '\n⚠️ 其中包含你正在编辑且未保存的笔记，其未保存修改将一并丢失。' : ''
    if (!window.confirm(`确定删除选中的 ${targets.length} 篇笔记？\n${preview}${more}${warn}\n删除后不可恢复。`)) return
    setBatchDeleting(true)
    setBatchProgress('')
    let ok = 0
    let failed = 0
    let openDeleted = false
    for (let i = 0; i < targets.length; i++) {
      const n = targets[i]
      setBatchProgress(`删除中 ${i + 1}/${targets.length}`)
      try {
        await deleteFreeNote(n.id)
        clearStoredDraft(n.id)
        if (draft.id === n.id) openDeleted = true
        ok++
      } catch {
        failed++
      }
    }
    if (openDeleted) {
      setDraft(EMPTY_DRAFT)
      setDirty(false)
      setPreview(false)
    }
    setSelected(new Set())
    setSelectMode(false)
    setBatchProgress('')
    setBatchDeleting(false)
    await refresh()
    if (failed > 0) toast.error(`已删除 ${ok} 篇，${failed} 篇失败`)
    else if (ok > 0) toast.success(`已删除 ${ok} 篇笔记`)
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
            <div className="flex items-center gap-1">
              {/* iter 283：批量选择模式开关 */}
              {data && data.total > 0 && (
                <button
                  type="button"
                  onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
                  aria-pressed={selectMode}
                  className={`rounded-lg border px-2.5 py-1 text-xs transition-all active:scale-95 dark:border-slate-600 ${
                    selectMode
                      ? 'border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300'
                      : 'hover:bg-slate-50 dark:hover:bg-slate-700'
                  }`}
                >
                  {selectMode ? '完成' : '选择'}
                </button>
              )}
              {!selectMode && (
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
              )}
            </div>
          </div>
          {/* iter 283：批量工具栏（已选计数 + 全选/清空 + 删除选中 + 取消） */}
          {selectMode && data && data.total > 0 && (
            <div className="mb-2 flex flex-wrap items-center gap-1.5 rounded-lg border border-blue-200 bg-blue-50/70 px-2.5 py-1.5 text-xs dark:border-blue-900 dark:bg-slate-700/60">
              <span className="font-medium tabular-nums text-blue-700 dark:text-blue-300">已选 {selected.size}</span>
              <button
                type="button"
                onClick={() => setSelected(new Set((data?.items ?? []).map((n) => n.id)))}
                className="rounded px-1.5 py-0.5 text-slate-600 transition-colors hover:bg-white dark:text-slate-300 dark:hover:bg-slate-600"
              >
                全选
              </button>
              <button
                type="button"
                onClick={() => setSelected(new Set())}
                disabled={selected.size === 0}
                className="rounded px-1.5 py-0.5 text-slate-600 transition-colors hover:bg-white disabled:opacity-40 dark:text-slate-300 dark:hover:bg-slate-600"
              >
                清空
              </button>
              <span className="flex-1" />
              <button
                type="button"
                onClick={onBatchDelete}
                disabled={selected.size === 0 || batchDeleting}
                className="rounded border border-red-300 bg-white px-2 py-0.5 font-medium text-red-700 transition-all hover:bg-red-50 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-900 dark:bg-slate-800 dark:text-red-300 dark:hover:bg-red-950"
              >
                {batchDeleting ? batchProgress || '删除中…' : `删除选中（${selected.size}）`}
              </button>
              <button
                type="button"
                onClick={exitSelectMode}
                disabled={batchDeleting}
                className="rounded px-1.5 py-0.5 text-slate-500 transition-colors hover:bg-white disabled:opacity-50 dark:text-slate-400 dark:hover:bg-slate-600"
              >
                取消
              </button>
            </div>
          )}
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
                  {selectMode ? (
                    <button
                      type="button"
                      onClick={() => toggleSelect(n.id)}
                      aria-pressed={selected.has(n.id)}
                      className={`w-full rounded-lg border px-2.5 py-1.5 text-left transition-all ${
                        selected.has(n.id)
                          ? 'border-blue-400 bg-blue-50 dark:border-blue-700 dark:bg-blue-950'
                          : 'border-transparent bg-slate-50 hover:border-slate-300 dark:bg-slate-900 dark:hover:border-slate-600'
                      }`}
                    >
                      <span className="flex items-center gap-2">
                        <span
                          aria-hidden="true"
                          className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] transition-colors ${
                            selected.has(n.id)
                              ? 'border-blue-500 bg-blue-500 text-white'
                              : 'border-slate-300 text-transparent dark:border-slate-500'
                          }`}
                        >
                          ✓
                        </span>
                        <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                          {n.title || '无题'}
                        </span>
                        <span className="shrink-0 text-xs tabular-nums text-slate-500 dark:text-slate-400">
                          {n.updated_at ? n.updated_at.slice(5, 10) : ''}
                        </span>
                      </span>
                      {n.content && (
                        <span className="mt-0.5 block pl-6 line-clamp-2 text-xs text-slate-500 dark:text-slate-400">
                          {n.content}
                        </span>
                      )}
                    </button>
                  ) : (
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
                        <span className="mt-0.5 block line-clamp-2 text-xs text-slate-500 dark:text-slate-400">
                          {n.content}
                        </span>
                      )}
                    </button>
                  )}
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

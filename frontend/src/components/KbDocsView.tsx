// 知识库文档库视图（iter 217）：LLMWiki 式独立文档库——文件/文本/链接入库、
// 搜索/类型筛选、查看/重命名/删除、ZIP 导出、选择生成学习方案、问答面板入口。
// 数据：/api/kb/*（不依赖学习方案——LLMWiki raw 层，用户直传直看直用）。
import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import EmptyState from './EmptyState'
import KbChatPanel from './KbChatPanel'
import KbViewerModal from './KbViewerModal'
import { SkeletonCards } from './Skeleton'
import { toast } from './Toast'
import {
  ApiError,
  confirmKbDocument,
  createKbTextDoc,
  createKbUrlDoc,
  deleteKbDocument,
  downloadKbExport,
  getKbDocument,
  kbGenerate,
  listKbDocuments,
  parseKbDocument,
  renameKbDocument,
  uploadKbDocument,
  type KbDocument,
} from '../lib/api'

const KIND_ICON: Record<KbDocument['kind'], string> = {
  file: '📄',
  text: '📝',
  url: '🔗',
  image: '🖼️',
  audio: '🎵',
  video: '🎬',
}
const KIND_LABEL: Record<KbDocument['kind'], string> = {
  file: '文档',
  text: '文本',
  url: '网页',
  image: '图片',
  audio: '音频',
  video: '视频',
}
const UPLOAD_ACCEPT =
  '.pdf,.docx,.pptx,.txt,.md,.png,.jpg,.jpeg,.bmp,.gif,.webp,.tiff,.tif,.mp3,.wav,.m4a,.ogg,.flac,.mp4,.mkv,.mov,.webm,.avi'
// 生成方案选择上限（后端 KbGenerateIn document_ids max_length=20 对齐）
const MAX_GENERATE_SELECT = 20
// 选择超此数提示生成管线资料总量 100k 截断护栏（plans.py _MAX_MATERIALS_TOTAL_CHARS）
const TRUNC_WARN_SELECT = 5

interface UploadItem {
  file: File
  progress: number
  status: 'pending' | 'uploading' | 'done' | 'error'
  error?: string
}

export default function KbDocsView() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [q, setQ] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')
  const [kind, setKind] = useState<KbDocument['kind'] | null>(null)
  const [uploads, setUploads] = useState<UploadItem[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [addMode, setAddMode] = useState<'text' | 'url' | null>(null)
  const [addTitle, setAddTitle] = useState('')
  const [addContent, setAddContent] = useState('')
  const [addBusy, setAddBusy] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editingTitle, setEditingTitle] = useState('')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [generateBusy, setGenerateBusy] = useState(false)
  const [exportBusy, setExportBusy] = useState(false)
  const [chatOpen, setChatOpen] = useState(false)
  const [viewingDoc, setViewingDoc] = useState<KbDocument | null>(null)
  // iter 225：解析方式弹窗（是否转换/怎么转换由用户决定）
  const [parseTarget, setParseTarget] = useState<KbDocument | null>(null)
  const [parseMode, setParseMode] = useState<'full' | 'fast'>('full')
  const [parseBusy, setParseBusy] = useState(false)
  const [confirmBusyId, setConfirmBusyId] = useState<number | null>(null)
  const uploadingRef = useRef(false)
  const deepLinkHandledRef = useRef(false)

  // iter 257：全局搜索结果深链（/wiki?doc=<id>）——直接打开对应文档查看器。
  // 不依赖列表分页/筛选状态，按 id 拉单篇（GET /kb/documents/{id} 后端已有）；
  // 消费一次后清参（Notes ?note= 同款语义），id 非法静默清参，404 提示。
  useEffect(() => {
    const docParam = searchParams.get('doc')
    if (!docParam || deepLinkHandledRef.current) return
    deepLinkHandledRef.current = true
    setSearchParams({}, { replace: true })
    const id = Number(docParam)
    if (!Number.isInteger(id) || id <= 0) return
    let cancelled = false
    getKbDocument(id)
      .then((d) => {
        if (!cancelled) setViewingDoc(d)
      })
      .catch((e) => {
        if (!cancelled) toast.error(e instanceof ApiError ? e.message : '文档打开失败')
      })
    return () => {
      cancelled = true
    }
  }, [searchParams, setSearchParams])

  // 搜索防抖（300ms）：避免每击键一次请求（同 Wiki 知识点搜索）
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300)
    return () => clearTimeout(t)
  }, [q])

  // iter 248：解析弹窗 Esc 关闭——解析期间（最长 10 分钟）此前遮罩/取消全锁死，
  // 用户除刷新外无法脱离（刷新后服务端解析照跑，纯前端自锁）。关闭仅关弹窗，
  // 解析请求继续（submitParse 已捕获 target，完成时 toast + 刷新列表）
  useEffect(() => {
    if (!parseTarget) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setParseTarget(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [parseTarget])

  const { data, isLoading, error } = useQuery({
    queryKey: ['kb-docs', debouncedQ, kind],
    queryFn: () => listKbDocuments({ q: debouncedQ, kind: kind ?? undefined }),
    staleTime: 15_000,
  })
  const docs = data?.documents ?? []

  function invalidate() {
    qc.invalidateQueries({ queryKey: ['kb-docs'] })
  }

  // --- 上传（拖拽/多选，顺序上传逐文件进度；重复文件后端按内容哈希去重） ---
  function addFiles(files: FileList | null) {
    if (!files?.length) return
    setUploads((prev) => [
      ...prev,
      ...Array.from(files).map((file) => ({ file, progress: 0, status: 'pending' as const })),
    ])
  }

  // 顺序上传泵：任一时刻只传一个文件（防并发抢带宽/进度混乱）
  useEffect(() => {
    if (uploadingRef.current) return
    const next = uploads.findIndex((u) => u.status === 'pending')
    if (next === -1) return
    uploadingRef.current = true
    const item = uploads[next]
    setUploads((prev) => prev.map((u, i) => (i === next ? { ...u, status: 'uploading' } : u)))
    uploadKbDocument(item.file, (p) => {
      setUploads((prev) => prev.map((u, i) => (i === next ? { ...u, progress: p } : u)))
    })
      .then((doc) => {
        setUploads((prev) => prev.map((u, i) => (i === next ? { ...u, status: 'done', progress: 100 } : u)))
        if (doc.meta?.deduped) toast.info(`「${doc.title}」内容已存在，已复用既有文档`)
        else toast.info(`「${doc.title}」已暂存——请在列表中确认添加`)
        invalidate()
      })
      .catch((e) => {
        const msg = e instanceof ApiError ? e.message : '上传失败'
        setUploads((prev) => prev.map((u, i) => (i === next ? { ...u, status: 'error', error: msg } : u)))
        toast.error(`${item.file.name}：${msg}`)
      })
      .finally(() => {
        uploadingRef.current = false
        setUploads((prev) => [...prev]) // 触发泵检查下一个 pending
      })
  }, [uploads])

  function clearFinishedUploads() {
    setUploads((prev) => prev.filter((u) => u.status === 'pending' || u.status === 'uploading'))
  }

  // --- 文本/链接入库 ---
  async function submitAdd() {
    if (addBusy || !addMode) return
    const content = addContent.trim()
    if (!content) {
      toast.error(addMode === 'text' ? '文本为空' : '链接为空')
      return
    }
    setAddBusy(true)
    try {
      if (addMode === 'text') await createKbTextDoc(addContent, addTitle)
      else await createKbUrlDoc(content, addTitle)
      toast.success(addMode === 'text' ? '文本已入库' : '链接已抓取入库')
      setAddMode(null)
      setAddTitle('')
      setAddContent('')
      invalidate()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '入库失败，请重试')
    } finally {
      setAddBusy(false)
    }
  }

  // --- 重命名（inline：Enter/blur 保存，Esc 取消） ---
  function startRename(d: KbDocument) {
    setEditingId(d.id)
    setEditingTitle(d.title)
  }

  async function commitRename() {
    if (editingId === null) return
    const title = editingTitle.trim()
    const id = editingId
    setEditingId(null)
    if (!title) return
    try {
      await renameKbDocument(id, title)
      invalidate()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '重命名失败')
    }
  }

  const [deleteBusyId, setDeleteBusyId] = useState<number | null>(null) // 删除防重入：在途再点会 404 出多余错误 toast

  async function onDelete(d: KbDocument) {
    if (deleteBusyId !== null) return
    if (!window.confirm(`确定删除文档「${d.title}」？\n原件与解析文本将一并删除，且不可恢复。`)) return
    setDeleteBusyId(d.id)
    try {
      await deleteKbDocument(d.id)
      setSelected((prev) => {
        if (!prev.has(d.id)) return prev
        const next = new Set(prev)
        next.delete(d.id)
        return next
      })
      toast.success('已删除')
      invalidate()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '删除失败')
    } finally {
      setDeleteBusyId(null)
    }
  }

  // --- iter 225：确认添加 / 按需解析（是否转换、怎么转换由用户决定） ---
  async function onConfirm(d: KbDocument) {
    if (confirmBusyId !== null) return
    setConfirmBusyId(d.id)
    try {
      await confirmKbDocument(d.id)
      toast.success(`「${d.title}」已添加到知识库`)
      invalidate()
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '确认失败')
    } finally {
      setConfirmBusyId(null)
    }
  }

  function openParse(d: KbDocument) {
    setParseTarget(d)
    setParseMode('full')
  }

  async function submitParse() {
    if (!parseTarget || parseBusy) return
    setParseBusy(true)
    const target = parseTarget
    try {
      await parseKbDocument(target.id, parseMode)
      toast.success(`「${target.title}」解析完成`)
      setParseTarget(null)
      invalidate()
    } catch (e) {
      // 解析失败文档保留原状态（可换档重试）
      toast.error(e instanceof ApiError ? e.message : '解析失败，可换档重试')
    } finally {
      setParseBusy(false)
    }
  }

  // --- 选择 / 生成 / 导出 ---
  function toggleSelect(id: number) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else {
        if (next.size >= MAX_GENERATE_SELECT) {
          toast.error(`一次最多选择 ${MAX_GENERATE_SELECT} 篇文档`)
          return prev
        }
        next.add(id)
      }
      return next
    })
  }

  async function onGenerate() {
    if (generateBusy || selected.size === 0) return
    if (
      selected.size > TRUNC_WARN_SELECT &&
      !window.confirm(
        `已选 ${selected.size} 篇文档：生成管线的资料注入总量上限为 100,000 字，超出部分将被截断。\n仍要继续生成吗？`,
      )
    )
      return
    setGenerateBusy(true)
    try {
      const r = await kbGenerate([...selected])
      toast.success(`已创建会话（${r.material_count} 份资料），正在打开生成页`)
      navigate(r.open_path)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '创建生成会话失败')
    } finally {
      setGenerateBusy(false)
    }
  }

  async function onExport(ids?: number[]) {
    if (exportBusy) return
    setExportBusy(true)
    try {
      await downloadKbExport(ids)
      toast.success('知识库 ZIP 已开始下载')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '导出失败，请重试')
    } finally {
      setExportBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* 工具栏：搜索 / 类型筛选 / 添加 / 导出 / 问答 */}
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索文档标题…"
          aria-label="搜索知识库文档"
          className="w-52 rounded-lg border px-3 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500/60 dark:border-slate-600 dark:bg-slate-800"
        />
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="按类型筛选">
          {([null, 'file', 'text', 'url', 'image', 'audio', 'video'] as const).map((k) => (
            <button
              key={k ?? 'all'}
              onClick={() => setKind(k)}
              aria-pressed={kind === k}
              className={`rounded-full px-2.5 py-1 text-xs font-medium transition-all active:scale-95 ${
                kind === k
                  ? 'bg-blue-600 text-white shadow-sm'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-300 dark:hover:bg-slate-600'
              }`}
            >
              {k === null ? '全部' : `${KIND_ICON[k]} ${KIND_LABEL[k]}`}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setAddMode((m) => (m === 'text' ? null : 'text'))}
            className="rounded-lg border px-2.5 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            ＋ 文本
          </button>
          <button
            onClick={() => setAddMode((m) => (m === 'url' ? null : 'url'))}
            className="rounded-lg border px-2.5 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            ＋ 链接
          </button>
          <button
            onClick={() => onExport(selected.size ? [...selected] : undefined)}
            disabled={exportBusy || docs.length === 0}
            title={selected.size ? `导出所选 ${selected.size} 篇` : '导出全部文档'}
            className="rounded-lg border px-2.5 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            {exportBusy ? '导出中…' : selected.size ? `导出所选 (${selected.size})` : '导出 ZIP'}
          </button>
          <button
            onClick={() => setChatOpen((o) => !o)}
            aria-expanded={chatOpen}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-all active:scale-95 ${
              chatOpen
                ? 'bg-violet-600 text-white shadow-sm'
                : 'bg-violet-50 text-violet-700 hover:bg-violet-100 dark:bg-violet-950 dark:text-violet-300 dark:hover:bg-violet-900'
            }`}
          >
            💬 问答
          </button>
        </div>
      </div>

      {/* 文本/链接入库内联表单 */}
      {addMode && (
        <div className="animate-fade-in space-y-2 rounded-xl border bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800">
          <input
            value={addTitle}
            onChange={(e) => setAddTitle(e.target.value)}
            placeholder="标题（可选，缺省取内容开头）"
            maxLength={256}
            className="w-full rounded-lg border px-3 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500/60 dark:border-slate-600 dark:bg-slate-900"
          />
          {addMode === 'text' ? (
            <textarea
              value={addContent}
              onChange={(e) => setAddContent(e.target.value)}
              placeholder="粘贴或输入文本内容…"
              rows={5}
              className="w-full rounded-lg border px-3 py-2 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500/60 dark:border-slate-600 dark:bg-slate-900"
            />
          ) : (
            <input
              value={addContent}
              onChange={(e) => setAddContent(e.target.value)}
              placeholder="https://…（后端抓取正文，限每分钟 10 次）"
              maxLength={2048}
              className="w-full rounded-lg border px-3 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500/60 dark:border-slate-600 dark:bg-slate-900"
            />
          )}
          <div className="flex justify-end gap-2">
            <button
              onClick={() => {
                setAddMode(null)
                setAddTitle('')
                setAddContent('')
              }}
              className="rounded-lg border px-3 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
            >
              取消
            </button>
            <button
              onClick={submitAdd}
              disabled={addBusy}
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[.98] disabled:opacity-50"
            >
              {addBusy ? '入库中…' : '入库'}
            </button>
          </div>
        </div>
      )}

      {/* 问答面板（iter 218：文档范围 chips + 错误特化） */}
      {chatOpen && <KbChatPanel docs={docs} onOpenDoc={(d) => setViewingDoc(d)} />}

      {/* 上传区（拖拽 + 多选，顺序上传逐文件进度） */}
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          addFiles(e.dataTransfer.files)
        }}
        className={`rounded-xl border-2 border-dashed p-4 transition-all duration-200 ${
          dragOver
            ? 'scale-[1.01] border-blue-500 bg-blue-50 shadow-inner dark:bg-blue-950'
            : 'border-slate-300 hover:border-slate-400 dark:border-slate-600 dark:hover:border-slate-500'
        }`}
      >
        <input
          type="file"
          multiple
          accept={UPLOAD_ACCEPT}
          aria-label="上传文件到知识库"
          onChange={(e) => {
            addFiles(e.target.files)
            e.target.value = '' // 允许再次选择同一文件
          }}
          className="text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-blue-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-blue-700 file:transition-colors hover:file:bg-blue-100 dark:file:bg-blue-950 dark:file:text-blue-300 dark:hover:file:bg-blue-900"
        />
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          或将文件拖拽到此处——支持 PDF / Word / PPT / 文本 / 图片 / 音频 / 视频（单个 ≤50MB，重复内容自动去重）
        </p>
        {uploads.length > 0 && (
          <ul className="mt-2 space-y-1.5">
            {uploads.map((u, i) => (
              <li key={`${u.file.name}-${i}`} className="animate-fade-in text-sm">
                <div className="flex items-center gap-2">
                  <span className="min-w-0 truncate text-slate-600 dark:text-slate-300">{u.file.name}</span>
                  <span className="shrink-0 text-xs tabular-nums text-slate-400">
                    {u.status === 'uploading'
                      ? `${u.progress}%`
                      : u.status === 'done'
                        ? '✅ 已暂存'
                        : u.status === 'error'
                          ? `❌ ${u.error ?? '失败'}`
                          : '排队中'}
                  </span>
                </div>
                {(u.status === 'uploading' || u.status === 'pending') && (
                  <div className="mt-0.5 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700">
                    <div
                      className="h-full rounded-full bg-blue-500 transition-all duration-300"
                      style={{ width: `${u.progress}%` }}
                    />
                  </div>
                )}
              </li>
            ))}
            {uploads.some((u) => u.status === 'done' || u.status === 'error') && (
              <li>
                <button
                  onClick={clearFinishedUploads}
                  className="text-xs text-slate-500 dark:text-slate-400 underline-offset-2 transition-colors hover:text-slate-600 hover:underline dark:hover:text-slate-300"
                >
                  清除已完成记录
                </button>
              </li>
            )}
          </ul>
        )}
      </div>

      {/* 选择工具栏（选中时出现）：生成方案 / 清除 */}
      {selected.size > 0 && (
        <div className="sticky top-2 z-10 flex animate-fade-in items-center gap-2 rounded-xl border border-blue-200 bg-blue-50 px-4 py-2 shadow-sm dark:border-blue-900 dark:bg-blue-950">
          <span className="text-sm text-blue-800 dark:text-blue-200">
            已选 <span className="font-semibold tabular-nums">{selected.size}</span> 篇
          </span>
          <button
            onClick={onGenerate}
            disabled={generateBusy}
            className="rounded-lg bg-blue-600 px-3 py-1 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[.98] disabled:opacity-50"
          >
            {generateBusy ? '创建中…' : '生成学习方案 →'}
          </button>
          <button
            onClick={() => setSelected(new Set())}
            className="rounded-lg px-2.5 py-1 text-sm text-slate-500 transition-colors hover:bg-slate-100 dark:hover:bg-slate-700"
          >
            清除选择
          </button>
        </div>
      )}

      {/* 文档卡片网格 */}
      {isLoading && <SkeletonCards count={3} />}
      {error && (
        <p
          role="alert"
          className="animate-fade-in rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-400"
        >
          加载失败：{error instanceof Error ? error.message : '未知错误'}
        </p>
      )}
      {!isLoading && !error && docs.length === 0 && (
        <EmptyState
          icon="📚"
          title={debouncedQ || kind ? '没有匹配的文档' : '文档库还是空的'}
          description={
            debouncedQ || kind
              ? '换个关键词或清除类型筛选试试'
              : '上传文件、粘贴文本或收藏网页链接，构建你的个人知识库——之后可以直接查看、打包导出，或交给 AI 生成学习方案/回答问题'
          }
        />
      )}
      <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {docs.map((d, i) => (
          <li
            key={d.id}
            className={`animate-fade-in-up rounded-xl border bg-white p-3 shadow-sm transition-all hover:shadow-md dark:bg-slate-800 ${
              selected.has(d.id)
                ? 'border-blue-400 ring-2 ring-blue-200 dark:border-blue-600 dark:ring-blue-900'
                : 'dark:border-slate-700'
            }`}
            style={{ animationDelay: `${Math.min(i, 10) * 40}ms` }}
          >
            <div className="flex items-start gap-2">
              <input
                type="checkbox"
                checked={selected.has(d.id)}
                onChange={() => toggleSelect(d.id)}
                disabled={d.status !== 'parsed'}
                title={d.status !== 'parsed' ? '解析完成后才可用于生成/问答' : undefined}
                aria-label={`选择 ${d.title}`}
                className="mt-1 h-4 w-4 shrink-0 accent-blue-600 disabled:cursor-not-allowed disabled:opacity-40"
              />
              <div className="min-w-0 flex-1">
                {editingId === d.id ? (
                  <input
                    autoFocus
                    value={editingTitle}
                    onChange={(e) => setEditingTitle(e.target.value)}
                    onBlur={commitRename}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') commitRename()
                      if (e.key === 'Escape') setEditingId(null)
                    }}
                    maxLength={256}
                    aria-label="重命名文档"
                    className="w-full rounded border px-2 py-0.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/60 dark:border-slate-600 dark:bg-slate-900"
                  />
                ) : (
                  <button
                    onClick={() => setViewingDoc(d)}
                    title={`查看「${d.title}」\n来源：${d.source}`}
                    className="block w-full truncate text-left text-sm font-medium text-slate-800 underline-offset-2 hover:text-blue-700 hover:underline dark:text-slate-100 dark:hover:text-blue-300"
                  >
                    <span className="mr-1" aria-hidden="true">
                      {KIND_ICON[d.kind]}
                    </span>
                    {d.title}
                  </button>
                )}
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  {KIND_LABEL[d.kind]}
                  {d.ext ? ` · ${d.ext}` : ''}
                  {/* iter 225：状态徽标——待确认/未解析/已解析字数 */}
                  {d.status === 'pending' ? (
                    <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 font-medium text-amber-700 dark:bg-amber-950 dark:text-amber-300">
                      待确认
                    </span>
                  ) : d.status !== 'parsed' ? (
                    <span className="ml-1 rounded bg-sky-100 px-1.5 py-0.5 font-medium text-sky-700 dark:bg-sky-950 dark:text-sky-300">
                      未解析
                    </span>
                  ) : (
                    <>
                      {' '}· <span className="tabular-nums">{d.parsed_chars.toLocaleString()}</span> 字
                    </>
                  )}
                  {d.created_at ? ` · ${d.created_at.slice(0, 10)}` : ''}
                </p>
              </div>
            </div>
            <div className="mt-2 flex items-center gap-1 pl-6 text-xs">
              {/* iter 225：按状态给动作——待确认先确认添加；未解析/已解析可（重）解析选档 */}
              {d.status === 'pending' && (
                <button
                  onClick={() => onConfirm(d)}
                  disabled={confirmBusyId === d.id}
                  className="rounded bg-green-600 px-2 py-1 font-medium text-white transition-all hover:bg-green-700 active:scale-95 disabled:opacity-50"
                >
                  {confirmBusyId === d.id ? '确认中…' : '确认添加'}
                </button>
              )}
              {d.has_original && (
                <button
                  onClick={() => openParse(d)}
                  className="rounded px-2 py-1 text-violet-600 transition-colors hover:bg-violet-50 dark:text-violet-400 dark:hover:bg-violet-950"
                >
                  {d.status === 'parsed' ? '重新解析' : '解析…'}
                </button>
              )}
              <button
                onClick={() => setViewingDoc(d)}
                className="rounded px-2 py-1 text-blue-600 transition-colors hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-950"
              >
                查看
              </button>
              <button
                onClick={() => startRename(d)}
                className="rounded px-2 py-1 text-slate-500 transition-colors hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-700"
              >
                重命名
              </button>
              <button
                onClick={() => onDelete(d)}
                disabled={deleteBusyId !== null}
                className="rounded px-2 py-1 text-red-500 transition-colors hover:bg-red-50 disabled:opacity-50 dark:text-red-400 dark:hover:bg-red-950"
              >
                {deleteBusyId === d.id ? '删除中…' : '删除'}
              </button>
            </div>
          </li>
        ))}
      </ul>

      {/* 查看器（iter 218：pdf/图片/音频/视频 blob 原生渲染，其余解析文本视图） */}
      {viewingDoc && (
        <KbViewerModal doc={viewingDoc} docs={docs} onClose={() => setViewingDoc(null)} onNavigate={(d) => setViewingDoc(d)} />
      )}

      {/* 解析方式弹窗（iter 225）：是否转换、怎么转换由用户决定——PDF 双档，其他类型默认管线 */}
      {parseTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 animate-fade-in"
          role="dialog"
          aria-modal="true"
          aria-label={`解析 ${parseTarget.title}`}
          onClick={() => setParseTarget(null)}
        >
          <div
            className="w-full max-w-sm rounded-xl border bg-white p-4 shadow-xl dark:border-slate-700 dark:bg-slate-800"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
              解析「{parseTarget.title}」
            </h3>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              解析后文本入库，可用于查看 / AI 问答 / 生成学习方案；失败不改变文档状态，可换档重试。
            </p>
            {parseTarget.ext === 'pdf' ? (
              <div className="mt-3 space-y-2" role="radiogroup" aria-label="PDF 解析方式">
                <label className="flex cursor-pointer items-start gap-2 rounded-lg border p-2.5 transition-colors has-[:checked]:border-blue-400 has-[:checked]:bg-blue-50 dark:border-slate-600 dark:has-[:checked]:border-blue-600 dark:has-[:checked]:bg-blue-950">
                  <input
                    type="radio"
                    name="kb-parse-mode"
                    checked={parseMode === 'full'}
                    onChange={() => setParseMode('full')}
                    className="mt-0.5 accent-blue-600"
                  />
                  <span>
                    <span className="block text-sm font-medium">高质量（marker）</span>
                    <span className="block text-xs text-slate-500 dark:text-slate-400">
                      版面保留好、支持扫描件 OCR；大文件可能需要几分钟
                    </span>
                  </span>
                </label>
                <label className="flex cursor-pointer items-start gap-2 rounded-lg border p-2.5 transition-colors has-[:checked]:border-blue-400 has-[:checked]:bg-blue-50 dark:border-slate-600 dark:has-[:checked]:border-blue-600 dark:has-[:checked]:bg-blue-950">
                  <input
                    type="radio"
                    name="kb-parse-mode"
                    checked={parseMode === 'fast'}
                    onChange={() => setParseMode('fast')}
                    className="mt-0.5 accent-blue-600"
                  />
                  <span>
                    <span className="block text-sm font-medium">快速提取（pypdf）</span>
                    <span className="block text-xs text-slate-500 dark:text-slate-400">
                      秒级纯文本抽取；无 OCR，扫描件会得到空文本
                    </span>
                  </span>
                </label>
              </div>
            ) : (
              <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-900 dark:text-slate-400">
                该类型使用默认解析管线
                {parseTarget.kind === 'image'
                  ? '（图片 OCR，需 tesseract）'
                  : parseTarget.kind === 'audio' || parseTarget.kind === 'video'
                    ? '（faster-whisper 转写，较慢）'
                    : ''}
                。
              </p>
            )}
            <div className="mt-4 flex items-center justify-end gap-2">
              {parseBusy && (
                <span className="mr-auto text-xs text-slate-400 dark:text-slate-500">
                  关闭弹窗后解析在后台继续，完成时将提示
                </span>
              )}
              <button
                onClick={() => setParseTarget(null)}
                className="rounded-lg border px-3 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
              >
                {parseBusy ? '后台继续' : '取消'}
              </button>
              <button
                onClick={submitParse}
                disabled={parseBusy}
                className="rounded-lg bg-violet-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-violet-700 active:scale-[.98] disabled:opacity-50"
              >
                {parseBusy ? '解析中…' : '开始解析'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

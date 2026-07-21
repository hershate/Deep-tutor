import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, confirmMaterial, createSession, createUrlMaterial, deleteSession, parseMaterial, uploadMaterial } from '../lib/api'
import { SseError } from '../lib/sse'
import { engine } from '../lib/taskEngine'
import { toast } from '../components/Toast'
import WidgetSlot from '../components/WidgetSlot'

// iter 137：首页草稿 localStorage 持久化——刷新/误关页面不丢需求描述与链接（文件句柄无法持久化）
const DRAFT_KEY = 'dt-home-draft'

interface HomeDraft { rawText: string; urls: string[] }

// iter 225：每份资料的处理方式由用户决定——full=高质量解析（PDF 走 marker）/
// fast=快速提取（仅 PDF 有别，pypdf 秒级）/ none=不解析（仅存档，不参与生成）
type ParseChoice = 'full' | 'fast' | 'none'
interface UploadEntry { file: File; mode: ParseChoice }

function loadDraft(): HomeDraft | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY)
    if (!raw) return null
    const d = JSON.parse(raw) as Partial<HomeDraft>
    return {
      rawText: typeof d.rawText === 'string' ? d.rawText : '',
      urls: Array.isArray(d.urls) ? d.urls.filter((u): u is string => typeof u === 'string') : [],
    }
  } catch {
    return null // 损坏数据静默忽略
  }
}

export default function Home() {
  const [draft] = useState<HomeDraft | null>(loadDraft)
  const [rawText, setRawText] = useState(draft?.rawText ?? '')
  const [files, setFiles] = useState<UploadEntry[]>([])
  const [urls, setUrls] = useState<string[]>(draft?.urls ?? []) // 网页链接资料（plan/03 §二）
  const [draftRestored, setDraftRestored] = useState(Boolean(draft && (draft.rawText || draft.urls.length)))
  const [urlInput, setUrlInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState('')
  const [uploadPct, setUploadPct] = useState<number | null>(null) // 当前文件字节级进度（plan/09 §七）
  const [dragOver, setDragOver] = useState(false) // 拖拽悬停高亮（plan/06 §四.1 拖拽上传）
  const [error, setError] = useState('')
  const nav = useNavigate()

  // 草稿自动保存（输入即存，localStorage 同步写开销可忽略；空内容清除 key 防恢复空草稿）
  useEffect(() => {
    try {
      if (!rawText.trim() && urls.length === 0) localStorage.removeItem(DRAFT_KEY)
      else localStorage.setItem(DRAFT_KEY, JSON.stringify({ rawText, urls }))
    } catch {
      // 隐私模式/存储禁用/配额满：草稿降级为不持久化，不影响主流程
    }
  }, [rawText, urls])

  function clearDraft() {
    setRawText('')
    setUrls([])
    setDraftRestored(false)
    try {
      localStorage.removeItem(DRAFT_KEY)
    } catch { /* 同上，忽略 */ }
  }

  function addFiles(list: FileList | null) {
    if (!list?.length) return
    setFiles((prev) => [...prev, ...Array.from(list).map((file) => ({ file, mode: 'full' as const }))])
  }

  function setFileMode(index: number, mode: ParseChoice) {
    setFiles((prev) => prev.map((f, i) => (i === index ? { ...f, mode } : f)))
  }

  function addUrl() {
    const u = urlInput.trim()
    if (!u) return
    if (!/^https?:\/\//i.test(u)) {
      setError('链接需以 http:// 或 https:// 开头')
      return
    }
    if (urls.includes(u)) {
      setError('该链接已添加')
      return
    }
    setError('')
    setUrls((prev) => [...prev, u])
    setUrlInput('')
  }

  async function start() {
    setError('')
    if (!rawText.trim() && files.length === 0 && urls.length === 0) {
      setError('请输入需求描述，或上传资料 / 添加网页链接')
      return
    }
    setLoading(true)
    try {
      const { session_id } = await createSession(rawText.slice(0, 40) || '学习方案')
      try {
        // 批量导入（P2，plan/README §输入形式9）：多文件合并为一个资料集，逐份上传。
        // iter 225：上传仅落原件（pending）；处理方式由用户在文件列表逐项选定——
        // full/fast 再调解析（隐含确认），none 仅确认添加（不参与生成）
        for (let i = 0; i < files.length; i++) {
          setUploadProgress(`上传资料 ${i + 1}/${files.length}（${files[i].file.name}）`)
          setUploadPct(null)
          let materialId: number
          try {
            const m = await uploadMaterial(session_id, files[i].file, (pct) => setUploadPct(pct))
            materialId = m.material_id
          } catch (e) {
            throw new Error(
              `${files[i].file.name} 上传失败：${e instanceof ApiError ? e.message : '网络错误'}`,
            )
          }
          const mode = files[i].mode
          setUploadProgress(
            mode === 'none'
              ? `添加资料 ${i + 1}/${files.length}（${files[i].file.name}，不解析）`
              : `解析资料 ${i + 1}/${files.length}（${files[i].file.name}）`,
          )
          setUploadPct(null)
          try {
            if (mode === 'none') await confirmMaterial(materialId)
            else await parseMaterial(materialId, mode)
          } catch (e) {
            throw new Error(
              `${files[i].file.name} ${mode === 'none' ? '添加' : '解析'}失败：${e instanceof ApiError ? e.message : '网络错误'}`,
            )
          }
        }
        // 网页链接资料（plan/03 §二）：后端逐条抓取+正文提取
        for (let i = 0; i < urls.length; i++) {
          setUploadProgress(`抓取网页 ${i + 1}/${urls.length}（${urls[i]}）`)
          setUploadPct(null)
          try {
            await createUrlMaterial(session_id, urls[i])
          } catch (e) {
            throw new Error(
              `${urls[i]} 抓取失败：${e instanceof ApiError ? e.message : '网络错误'}`,
            )
          }
        }
      } catch (uploadErr) {
        // 上传失败：清理刚建的空会话，避免孤儿堆积（重试会建新会话）
        deleteSession(session_id).catch(() => {})
        throw uploadErr
      }
      // iter 194 F9：生成由任务引擎发起（SSE 连接 App 级常驻，切页不丢）；
      // 启动即败按异常码引导——已建会话不删（区别于上传失败），留 idle 占位可稍后继续
      try {
        await engine.startPlan(session_id, rawText, rawText.slice(0, 40) || '学习方案')
      } catch (genErr) {
        if (genErr instanceof SseError && genErr.status === 409) {
          toast.error('你已有生成任务运行中：本次会话已保留在方案列表，完成后可继续', {
            label: '查看任务',
            onClick: () => nav('/plans'),
          })
        } else if (genErr instanceof SseError && genErr.status === 503) {
          toast.error('当前生成任务较多，请稍后重试（会话已保留在方案列表）', {
            label: '查看任务',
            onClick: () => nav('/plans'),
          })
        } else if (genErr instanceof SseError && genErr.status === 402) {
          toast.error('今日预算已用完：会话已保留，可明日继续或调整日预算', {
            label: '调整日预算',
            onClick: () => nav('/settings'),
          })
        } else {
          setError(genErr instanceof Error ? genErr.message : '发起生成失败，请重试')
        }
        setLoading(false)
        setUploadProgress('')
        return
      }
      setLoading(false) // 路由异常未卸载时按钮不卡「创建中…」
      setUploadProgress('')
      nav(`/generate/${session_id}`)
      try {
        localStorage.removeItem(DRAFT_KEY) // 生成成功 -> 草稿使命完成
      } catch { /* 存储不可用，忽略 */ }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : e instanceof Error ? e.message : '创建失败')
      setLoading(false)
      setUploadProgress('')
    }
  }

  return (
    <div className="space-y-6">
      {/* iter 92：卡片化分区和错峰入场动画（animationDelay 递增） */}
      <div className="space-y-2 animate-fade-in-up">
        {/* iter 107：品牌渐变标题（蓝->靛），点缀首页视觉焦点 */}
        <h2 className="bg-gradient-to-r from-blue-600 to-indigo-500 bg-clip-text text-2xl font-bold text-transparent dark:from-blue-400 dark:to-indigo-300">
          开始生成你的学习方案
        </h2>
        <p className="text-slate-600 dark:text-slate-300">描述你想学的内容，可选上传资料（PDF/Word/PPT/TXT/MD，可多选合并为一个资料集）或添加网页链接。</p>
      </div>

      {/* iter 183：学习概览小组件宫格（插件 API home.dashboard 插槽；各组件无数据自行隐藏） */}
      <WidgetSlot slot="home.dashboard" layout="grid" className="animate-fade-in-up" />

      <div className="space-y-2 rounded-xl border bg-white p-4 shadow-sm animate-fade-in-up dark:border-slate-700 dark:bg-slate-800" style={{ animationDelay: '60ms' }}>
        <div className="flex items-center justify-between">
          <label htmlFor="home-rawtext" className="block text-sm font-medium text-slate-700 dark:text-slate-200">
            需求描述
          </label>
          {/* iter 137：草稿恢复提示 + 一键清除 */}
          {draftRestored && (
            <span className="flex animate-fade-in items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
              已恢复上次未提交的草稿
              <button
                type="button"
                onClick={clearDraft}
                className="rounded px-1.5 py-0.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-red-600 dark:hover:bg-slate-700 dark:hover:text-red-400"
              >
                清除
              </button>
            </span>
          )}
        </div>
        <textarea
          id="home-rawtext"
          className="w-full min-h-[120px] rounded-lg border border-slate-300 dark:border-slate-600 dark:bg-slate-900 p-3 transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500"
          placeholder="例如：我想学 React，零基础，目标是能独立做项目，每天 1-2 小时"
          value={rawText}
          onChange={(e) => setRawText(e.target.value)}
          maxLength={20000} // err.md D14：对齐后端 prompt 字段截断上限
        />
        {/* iter 103：字数实时提示（接近上限时 amber 预警） */}
        <p className={`text-right text-xs tabular-nums transition-colors ${rawText.length > 18000 ? 'text-amber-600 dark:text-amber-400' : 'text-slate-400'}`} aria-live="polite">
          {rawText.length.toLocaleString()} / 20,000
        </p>
      </div>

      <div className="space-y-2 rounded-xl border bg-white p-4 shadow-sm animate-fade-in-up dark:border-slate-700 dark:bg-slate-800" style={{ animationDelay: '120ms' }}>
        <label htmlFor="home-file" className="block text-sm font-medium text-slate-700 dark:text-slate-200">
          学习资料（可选，可多选 / 可拖拽）
        </label>
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
          className={`rounded-lg border-2 border-dashed p-4 transition-all duration-200 ${
            dragOver
              ? 'scale-[1.01] border-blue-500 bg-blue-50 shadow-inner dark:bg-blue-950'
              : 'border-slate-300 hover:border-slate-400 dark:border-slate-600 dark:hover:border-slate-500'
          }`}
        >
          <input
            id="home-file"
            type="file"
            multiple
            accept=".pdf,.docx,.pptx,.txt,.md,.png,.jpg,.jpeg,.bmp,.gif,.webp,.tiff,.tif,.mp3,.wav,.m4a,.ogg,.flac,.mp4,.mkv,.mov,.webm,.avi"
            onChange={(e) => {
              addFiles(e.target.files)
              e.target.value = '' // 允许再次选择同一文件
            }}
            // iter 105：file: 伪元素品牌化原生「选择文件」按钮
            className="text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-blue-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-blue-700 file:transition-colors hover:file:bg-blue-100 dark:file:bg-blue-950 dark:file:text-blue-300 dark:hover:file:bg-blue-900"
          />
          <p className="mt-1 text-xs text-slate-400">或将文件拖拽到此处</p>
        </div>
        {files.length > 0 && (
          <ul className="space-y-1 text-sm text-slate-600 dark:text-slate-300">
            {files.map((f, i) => {
              const isPdf = /\.pdf$/i.test(f.file.name)
              return (
                <li key={`${f.file.name}-${i}`} className="flex animate-fade-in items-center gap-2">
                  <span className="truncate">{f.file.name}</span>
                  {/* iter 225：处理方式由用户逐项决定（PDF 双档，其他类型默认管线） */}
                  <select
                    value={f.mode}
                    onChange={(e) => setFileMode(i, e.target.value as ParseChoice)}
                    aria-label={`${f.file.name} 处理方式`}
                    className="shrink-0 rounded border bg-white px-1.5 py-0.5 text-xs transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500/60 dark:border-slate-600 dark:bg-slate-800"
                  >
                    {isPdf ? (
                      <>
                        <option value="full">高质量解析（marker，较慢）</option>
                        <option value="fast">快速提取（pypdf，秒级）</option>
                      </>
                    ) : (
                      <option value="full">解析（默认管线）</option>
                    )}
                    <option value="none">不解析（仅存档）</option>
                  </select>
                  <button
                    type="button"
                    onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                    aria-label={`移除 ${f.file.name}`}
                    className="rounded px-1.5 text-xs text-slate-400 transition-colors hover:bg-slate-100 hover:text-red-600 dark:text-slate-500 dark:hover:bg-slate-700 dark:hover:text-red-400"
                  >
                    移除
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>

      <div className="space-y-2 rounded-xl border bg-white p-4 shadow-sm animate-fade-in-up dark:border-slate-700 dark:bg-slate-800" style={{ animationDelay: '180ms' }}>
        <label htmlFor="home-url" className="block text-sm font-medium text-slate-700 dark:text-slate-200">
          网页链接（可选，可多条）
        </label>
        <div className="flex gap-2">
          <input
            id="home-url"
            type="url"
            placeholder="https://example.com/tutorial"
            value={urlInput}
            onChange={(e) => setUrlInput(e.target.value)}
            maxLength={2048} // err.md D14：对齐后端 UrlIn.url 上限
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                addUrl()
              }
            }}
            className="flex-1 rounded-lg border border-slate-300 dark:border-slate-600 dark:bg-slate-900 px-3 py-2 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            type="button"
            onClick={addUrl}
            className="rounded-lg border px-4 py-2 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            添加
          </button>
        </div>
        {urls.length > 0 && (
          <ul className="space-y-1 text-sm text-slate-600 dark:text-slate-300">
            {urls.map((u, i) => (
              <li key={u} className="flex animate-fade-in items-center gap-2">
                <span className="truncate">{u}</span>
                <button
                  type="button"
                  onClick={() => setUrls((prev) => prev.filter((_, j) => j !== i))}
                  aria-label={`移除 ${u}`}
                  className="rounded px-1.5 text-xs text-slate-400 transition-colors hover:bg-slate-100 hover:text-red-600 dark:text-slate-500 dark:hover:bg-slate-700 dark:hover:text-red-400"
                >
                  移除
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {error && (
        <div role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950 p-3 text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {loading && uploadProgress && (
        <div className="space-y-1" aria-live="polite">
          <div className="text-sm text-slate-600 dark:text-slate-300">
            {uploadProgress}
            {uploadPct !== null && ` ${uploadPct}%`}
          </div>
          {uploadPct !== null && (
            <div
              className="h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700"
              role="progressbar"
              aria-valuenow={uploadPct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="文件上传进度"
            >
              <div
                className="h-full rounded-full bg-blue-600 transition-all duration-200"
                style={{ width: `${uploadPct}%` }}
              />
            </div>
          )}
        </div>
      )}

      <button
        onClick={start}
        disabled={loading}
        className="rounded-lg bg-blue-600 px-6 py-2.5 font-medium text-white shadow-sm transition-all duration-200 hover:bg-blue-700 hover:shadow-md active:scale-[.98] disabled:opacity-50 disabled:hover:shadow-sm disabled:active:scale-100"
      >
        {loading ? uploadProgress || '创建中…' : '生成学习方案'}
      </button>
    </div>
  )
}

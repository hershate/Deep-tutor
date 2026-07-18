import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, createSession, createUrlMaterial, deleteSession, uploadMaterial } from '../lib/api'

export default function Home() {
  const [rawText, setRawText] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [urls, setUrls] = useState<string[]>([]) // 网页链接资料（plan/03 §二）
  const [urlInput, setUrlInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState('')
  const [uploadPct, setUploadPct] = useState<number | null>(null) // 当前文件字节级进度（plan/09 §七）
  const [dragOver, setDragOver] = useState(false) // 拖拽悬停高亮（plan/06 §四.1 拖拽上传）
  const [error, setError] = useState('')
  const nav = useNavigate()

  function addFiles(list: FileList | null) {
    if (!list?.length) return
    setFiles((prev) => [...prev, ...Array.from(list)])
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
        // 批量导入（P2，plan/README §输入形式9）：多文件合并为一个资料集，逐份上传
        for (let i = 0; i < files.length; i++) {
          setUploadProgress(`上传资料 ${i + 1}/${files.length}（${files[i].name}）`)
          setUploadPct(null)
          try {
            await uploadMaterial(session_id, files[i], (pct) => setUploadPct(pct))
          } catch (e) {
            throw new Error(
              `${files[i].name} 上传失败：${e instanceof ApiError ? e.message : '网络错误'}`,
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
      nav(`/generate/${session_id}`, { state: { rawText } })
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
        <h2 className="text-2xl font-bold">开始生成你的学习方案</h2>
        <p className="text-slate-600 dark:text-slate-300">描述你想学的内容，可选上传资料（PDF/Word/PPT/TXT/MD，可多选合并为一个资料集）或添加网页链接。</p>
      </div>

      <div className="space-y-2 rounded-xl border bg-white p-4 shadow-sm animate-fade-in-up dark:border-slate-700 dark:bg-slate-800" style={{ animationDelay: '60ms' }}>
        <label htmlFor="home-rawtext" className="block text-sm font-medium text-slate-700 dark:text-slate-200">
          需求描述
        </label>
        <textarea
          id="home-rawtext"
          className="w-full min-h-[120px] rounded-lg border border-slate-300 dark:border-slate-600 p-3 transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500"
          placeholder="例如：我想学 React，零基础，目标是能独立做项目，每天 1-2 小时"
          value={rawText}
          onChange={(e) => setRawText(e.target.value)}
          maxLength={20000} // err.md D14：对齐后端 prompt 字段截断上限
        />
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
            className="text-sm"
          />
          <p className="mt-1 text-xs text-slate-400">或将文件拖拽到此处</p>
        </div>
        {files.length > 0 && (
          <ul className="space-y-1 text-sm text-slate-600 dark:text-slate-300">
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`} className="flex items-center gap-2">
                <span className="truncate">{f.name}</span>
                <button
                  type="button"
                  onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                  aria-label={`移除 ${f.name}`}
                  className="rounded px-1.5 text-xs text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 hover:text-red-600 dark:text-red-400"
                >
                  移除
                </button>
              </li>
            ))}
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
            className="flex-1 rounded-lg border border-slate-300 dark:border-slate-600 px-3 py-2 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <button
            type="button"
            onClick={addUrl}
            className="rounded-lg border px-4 py-2 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:hover:bg-slate-700"
          >
            添加
          </button>
        </div>
        {urls.length > 0 && (
          <ul className="space-y-1 text-sm text-slate-600 dark:text-slate-300">
            {urls.map((u, i) => (
              <li key={u} className="flex items-center gap-2">
                <span className="truncate">{u}</span>
                <button
                  type="button"
                  onClick={() => setUrls((prev) => prev.filter((_, j) => j !== i))}
                  aria-label={`移除 ${u}`}
                  className="rounded px-1.5 text-xs text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700 hover:text-red-600 dark:text-red-400"
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
            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200">
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

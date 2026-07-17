import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, createSession, deleteSession, uploadMaterial } from '../lib/api'

export default function Home() {
  const [rawText, setRawText] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [loading, setLoading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState('')
  const [error, setError] = useState('')
  const nav = useNavigate()

  function addFiles(list: FileList | null) {
    if (!list?.length) return
    setFiles((prev) => [...prev, ...Array.from(list)])
  }

  async function start() {
    setError('')
    if (!rawText.trim() && files.length === 0) {
      setError('请输入需求描述或上传资料')
      return
    }
    setLoading(true)
    try {
      const { session_id } = await createSession(rawText.slice(0, 40) || '学习方案')
      try {
        // 批量导入（P2，plan/README §输入形式9）：多文件合并为一个资料集，逐份上传
        for (let i = 0; i < files.length; i++) {
          setUploadProgress(`上传资料 ${i + 1}/${files.length}（${files[i].name}）…`)
          try {
            await uploadMaterial(session_id, files[i])
          } catch (e) {
            throw new Error(
              `${files[i].name} 上传失败：${e instanceof ApiError ? e.message : '网络错误'}`,
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
      <div className="space-y-2">
        <h2 className="text-2xl font-bold">开始生成你的学习方案</h2>
        <p className="text-slate-600">描述你想学的内容，可选上传资料（PDF/Word/PPT/TXT/MD，可多选合并为一个资料集）。</p>
      </div>

      <div className="space-y-2">
        <label htmlFor="home-rawtext" className="block text-sm font-medium text-slate-700">
          需求描述
        </label>
        <textarea
          id="home-rawtext"
          className="w-full min-h-[120px] rounded-lg border border-slate-300 p-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
          placeholder="例如：我想学 React，零基础，目标是能独立做项目，每天 1-2 小时"
          value={rawText}
          onChange={(e) => setRawText(e.target.value)}
        />
      </div>

      <div className="space-y-2">
        <label htmlFor="home-file" className="block text-sm font-medium text-slate-700">
          学习资料（可选，可多选）
        </label>
        <input
          id="home-file"
          type="file"
          multiple
          accept=".pdf,.docx,.pptx,.txt,.md"
          onChange={(e) => {
            addFiles(e.target.files)
            e.target.value = '' // 允许再次选择同一文件
          }}
          className="text-sm"
        />
        {files.length > 0 && (
          <ul className="space-y-1 text-sm text-slate-600">
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`} className="flex items-center gap-2">
                <span className="truncate">{f.name}</span>
                <button
                  type="button"
                  onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                  aria-label={`移除 ${f.name}`}
                  className="rounded px-1.5 text-xs text-slate-400 hover:bg-slate-100 hover:text-red-600"
                >
                  移除
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {error && (
        <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <button
        onClick={start}
        disabled={loading}
        className="rounded-lg bg-blue-600 px-6 py-2.5 font-medium text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? uploadProgress || '创建中…' : '生成学习方案'}
      </button>
    </div>
  )
}

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, createSession, uploadMaterial } from '../lib/api'

export default function Home() {
  const [rawText, setRawText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const nav = useNavigate()

  async function start() {
    setError('')
    if (!rawText.trim() && !file) {
      setError('请输入需求描述或上传资料')
      return
    }
    setLoading(true)
    try {
      const { session_id } = await createSession(rawText.slice(0, 40) || '学习方案')
      if (file) await uploadMaterial(session_id, file)
      nav(`/generate/${session_id}`, { state: { rawText } })
    } catch (e) {
      setError(e instanceof ApiError ? e.message : e instanceof Error ? e.message : '创建失败')
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h2 className="text-2xl font-bold">开始生成你的学习方案</h2>
        <p className="text-slate-600">描述你想学的内容，可选上传资料（PDF/Word/PPT/TXT/MD）。</p>
      </div>

      <textarea
        className="w-full min-h-[120px] rounded-lg border border-slate-300 p-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
        placeholder="例如：我想学 React，零基础，目标是能独立做项目，每天 1-2 小时"
        value={rawText}
        onChange={(e) => setRawText(e.target.value)}
      />

      <div className="flex items-center gap-3">
        <input
          type="file"
          accept=".pdf,.docx,.pptx,.txt,.md"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="text-sm"
        />
        {file && <span className="text-sm text-slate-500">已选: {file.name}</span>}
      </div>

      {error && <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      <button
        onClick={start}
        disabled={loading}
        className="rounded-lg bg-blue-600 px-6 py-2.5 font-medium text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? '创建中…' : '生成学习方案'}
      </button>
    </div>
  )
}

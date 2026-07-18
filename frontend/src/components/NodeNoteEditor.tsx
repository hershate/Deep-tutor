// 知识点笔记编辑器（iter 105，Wiki 节点卡片内联展开）：
// 展开时按需 GET 笔记；保存 PUT（空内容即删除）；保存后回调父级刷新 has_note。
import { useEffect, useState } from 'react'
import { ApiError, getNodeNote, putNodeNote } from '../lib/api'
import { toast } from './Toast'

interface Props {
  nodeId: number
  onSaved: (hasNote: boolean) => void
}

export default function NodeNoteEditor({ nodeId, onSaved }: Props) {
  const [content, setContent] = useState('')
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    getNodeNote(nodeId)
      .then((r) => {
        if (cancelled) return
        setContent(r.content)
        setUpdatedAt(r.updated_at)
      })
      .catch(() => {
        if (!cancelled) toast.error('笔记加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [nodeId])

  async function save() {
    if (busy) return
    setBusy(true)
    try {
      const r = await putNodeNote(nodeId, content)
      setContent(r.content)
      setUpdatedAt(r.updated_at)
      onSaved(r.content.trim().length > 0)
      toast.success(r.content.trim() ? '笔记已保存' : '笔记已清除')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '保存失败，请重试')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <p className="flex items-center gap-2 px-3 pb-2 text-xs text-slate-400" role="status">
        <span aria-hidden="true" className="h-3 w-3 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600 dark:border-slate-600 dark:border-t-blue-400" />
        加载笔记…
      </p>
    )
  }

  return (
    <div className="animate-fade-in border-t px-3 py-2 dark:border-slate-700" onClick={(e) => e.stopPropagation()}>
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={3}
        maxLength={20_000}
        placeholder="记录你对这个知识点的理解、口诀、疑问…"
        aria-label="知识点笔记"
        className="w-full rounded-lg border px-2 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500/60 dark:border-slate-600 dark:bg-slate-900"
      />
      <div className="mt-1 flex items-center justify-between gap-2">
        <span className="text-xs text-slate-400">
          {updatedAt ? `更新于 ${new Date(updatedAt).toLocaleString()}` : '尚未保存'}
        </span>
        <button
          onClick={save}
          disabled={busy}
          className="rounded-lg bg-blue-600 px-3 py-1 text-xs font-medium text-white shadow-sm transition-all hover:bg-blue-500 active:scale-95 disabled:opacity-50"
        >
          {busy ? '保存中…' : '保存笔记'}
        </button>
      </div>
    </div>
  )
}

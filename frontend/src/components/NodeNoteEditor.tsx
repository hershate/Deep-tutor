// 知识点笔记编辑器（iter 105，Wiki 节点卡片内联展开）：
// 展开时按需 GET 笔记；保存 PUT（空内容即删除）；保存后回调父级刷新 has_note。
// iter 367（批次六 M6）：未保存保护——收起/切节点即卸载组件，输入会静默丢失；
// 补 debounced 本地草稿（Notes iter 272 同款）+ 重开恢复确认 + 保存往返并发输入不覆盖。
import { useEffect, useRef, useState } from 'react'
import { ApiError, getNodeNote, putNodeNote } from '../lib/api'
import { toast } from './Toast'

interface Props {
  nodeId: number
  onSaved: (hasNote: boolean) => void
}

const nodeDraftKey = (id: number) => `dt-node-note-draft-${id}`

function readNodeDraft(id: number): string | null {
  try {
    const raw = localStorage.getItem(nodeDraftKey(id))
    return raw && raw.trim() ? raw : null
  } catch {
    return null
  }
}

function writeNodeDraft(id: number, content: string) {
  try {
    if (content.trim()) localStorage.setItem(nodeDraftKey(id), content)
    else localStorage.removeItem(nodeDraftKey(id))
  } catch {
    /* 存储不可用，仅本次会话生效 */
  }
}

export default function NodeNoteEditor({ nodeId, onSaved }: Props) {
  const [content, setContent] = useState('')
  const [savedContent, setSavedContent] = useState('')
  const [updatedAt, setUpdatedAt] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const contentRef = useRef(content)
  contentRef.current = content
  const dirty = content !== savedContent

  // debounced 草稿：输入停 500ms 即落 localStorage（收起/切节点/崩溃可恢复）
  useEffect(() => {
    if (!dirty) return
    const t = setTimeout(() => writeNodeDraft(nodeId, content), 500)
    return () => clearTimeout(t)
  }, [content, dirty, nodeId])

  useEffect(() => {
    let cancelled = false
    getNodeNote(nodeId)
      .then((r) => {
        if (cancelled) return
        // 未保存草稿恢复确认（存在且与服务端已存内容不一致）
        const draft = readNodeDraft(nodeId)
        if (draft !== null && draft !== r.content) {
          if (window.confirm('检测到该知识点笔记的未保存草稿，恢复吗？\n「确定」恢复草稿，「取消」丢弃草稿使用已保存版本。')) {
            setContent(draft)
            setSavedContent(r.content)
            setUpdatedAt(r.updated_at)
            setLoading(false)
            return
          }
          writeNodeDraft(nodeId, '')
        }
        setContent(r.content)
        setSavedContent(r.content)
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
    const sent = content
    try {
      const r = await putNodeNote(nodeId, sent)
      // 保存往返期间的继续输入不覆盖（同 Notes M3 口径）
      if (contentRef.current === sent) {
        setContent(r.content)
        setSavedContent(r.content)
      } else {
        setSavedContent(r.content) // 基线对齐已存内容；用户新输入保持 dirty
      }
      setUpdatedAt(r.updated_at)
      if (contentRef.current === sent) writeNodeDraft(nodeId, '')
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
      <p className="flex items-center gap-2 px-3 pb-2 text-xs text-slate-500 dark:text-slate-400" role="status">
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
        <span className="text-xs text-slate-500 dark:text-slate-400">
          {dirty ? '未保存（收起后可从草稿恢复）' : updatedAt ? `更新于 ${new Date(updatedAt).toLocaleString()}` : '尚未保存'}
        </span>
        <button
          onClick={save}
          disabled={busy}
          className="rounded-lg bg-blue-600 px-3 py-1 text-xs font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98] disabled:opacity-50 disabled:hover:shadow-sm disabled:active:scale-100"
        >
          {busy ? '保存中…' : '保存笔记'}
        </button>
      </div>
    </div>
  )
}

// iter 346（F8 #10）：首页「从知识库文档生成」——复用 kbGenerate
// （KB 文档 -> session+material -> /generate 生成管线，见 kb.py generate_from_documents）。
// 用户要求：生成方案时能直接引用知识库内已有的文本笔记；二进制（PDF 等）须先解析为文本。
// 仅列出 status=parsed 的文档（未解析的 PDF 无文本可注入，引导先解析）。
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ApiError, kbGenerate, listKbDocuments } from '../lib/api'
import { toast } from './Toast'
import Card from './Card'

export default function HomeKbGenerate() {
  const nav = useNavigate()
  const [sel, setSel] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const kbQ = useQuery({ queryKey: ['kb-docs', 'home'], queryFn: () => listKbDocuments({ limit: 50 }), staleTime: 60_000 })
  const docs = (kbQ.data?.documents ?? []).filter((d) => d.status === 'parsed')
  // iter 371：取满 50 条说明可能被截断——提示用户到知识库页看全量，避免「更早文档不可达却无感知」
  const maybeTruncated = (kbQ.data?.documents ?? []).length >= 50

  function toggle(id: number) {
    setSel((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  }

  async function generate() {
    if (sel.size === 0 || busy) return
    setBusy(true)
    try {
      const r = await kbGenerate([...sel])
      toast.success(`已用 ${r.material_count} 篇知识库文档创建方案，正在生成…`)
      nav(r.open_path)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '生成失败')
    } finally {
      setBusy(false)
    }
  }

  if (docs.length === 0) return null // 无已解析文档不显示该区

  return (
    <Card className="animate-fade-in-up space-y-2 !p-4" >
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-200">📚 从知识库文档生成</h3>
        <button
          type="button"
          onClick={generate}
          disabled={sel.size === 0 || busy}
          className="rounded-lg bg-blue-600 px-3 py-1 text-xs text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? '生成中…' : `用所选生成${sel.size > 0 ? `（${sel.size}）` : ''}`}
        </button>
      </div>
      <p className="text-xs text-slate-500 dark:text-slate-400">
        选择已解析为文本的知识库文档作为生成资料（PDF 等二进制须先在知识库解析为文本）
        {maybeTruncated && ' · 仅列出最近 50 篇，更多请在知识库页查看'}
      </p>
      <ul className="max-h-48 space-y-1 overflow-y-auto">
        {docs.map((d) => (
          <li key={d.id}>
            <label className="flex items-center gap-2 rounded border px-2 py-1 text-sm hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700">
              <input type="checkbox" checked={sel.has(d.id)} onChange={() => toggle(d.id)} />
              <span className="truncate">{d.title}</span>
              {d.kind !== 'text' && d.kind !== 'url' && (
                <span className="ml-auto shrink-0 text-xs text-slate-400">{d.ext || d.kind}</span>
              )}
            </label>
          </li>
        ))}
      </ul>
    </Card>
  )
}

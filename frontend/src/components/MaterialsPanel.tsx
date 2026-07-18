// 会话源资料清单（iter 104）：方案详情页展示生成所用资料，可单个删除（传错资料移除后重生成）。
// 数据：GET /sessions/{id}/materials；删除：DELETE /sessions/{id}/materials/{mid}（生成中后端 409）。
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, deleteSessionMaterial, getSessionMaterials, type SessionMaterial } from '../lib/api'
import { SkeletonLines } from './Skeleton'
import { toast } from './Toast'

const KIND_ICON: Record<string, string> = { file: '📄', url: '🔗', text: '📝' }
const KIND_LABEL: Record<string, string> = { file: '文件', url: '网页', text: '文本' }

function displayName(m: SessionMaterial): string {
  const filename = typeof m.meta?.filename === 'string' ? (m.meta.filename as string) : ''
  const name = filename || m.source || `${KIND_LABEL[m.kind] ?? m.kind}资料`
  return name.length > 60 ? `${name.slice(0, 60)}…` : name
}

export default function MaterialsPanel({ sessionId }: { sessionId: number }) {
  const qc = useQueryClient()
  const [busyId, setBusyId] = useState<number | null>(null)
  const { data, isLoading, error } = useQuery({
    queryKey: ['session-materials', sessionId],
    queryFn: () => getSessionMaterials(sessionId),
    staleTime: 30_000,
  })

  async function onDelete(m: SessionMaterial) {
    if (busyId !== null) return
    if (!window.confirm(`确定删除资料「${displayName(m)}」？\n删除后可重新生成方案使其不再参考该资料。`)) return
    setBusyId(m.material_id)
    try {
      await deleteSessionMaterial(sessionId, m.material_id)
      toast.success('资料已删除')
      await qc.invalidateQueries({ queryKey: ['session-materials', sessionId] })
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '删除失败，请重试')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <section className="rounded-xl border bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800">
      <h3 className="mb-2 font-semibold">源资料{data ? `（${data.total}）` : ''}</h3>
      {isLoading && <SkeletonLines lines={2} />}
      {error && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          资料清单加载失败：{error instanceof ApiError ? error.message : '网络错误'}
        </p>
      )}
      {data && data.total === 0 && (
        <p className="text-sm text-slate-500 dark:text-slate-400">无源资料（方案由需求描述直接生成）</p>
      )}
      {data && data.total > 0 && (
        <ul className="space-y-1.5">
          {data.items.map((m, i) => (
            <li
              key={m.material_id}
              className="flex animate-fade-in-up items-center gap-2 rounded-lg border px-3 py-1.5 text-sm transition-colors hover:border-slate-300 dark:border-slate-700 dark:hover:border-slate-600"
              style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}
            >
              <span aria-hidden="true">{KIND_ICON[m.kind] ?? '📎'}</span>
              <span className="min-w-0 flex-1 truncate" title={m.source}>
                {displayName(m)}
              </span>
              <span className="shrink-0 text-xs text-slate-400 tabular-nums">
                {m.parsed_chars > 0 ? `约 ${m.parsed_chars} 字` : '未解析'}
              </span>
              <button
                onClick={() => onDelete(m)}
                disabled={busyId !== null}
                aria-label={`删除资料 ${displayName(m)}`}
                className="shrink-0 rounded-lg border border-red-200 px-2 py-0.5 text-xs text-red-600 transition-all hover:bg-red-50 active:scale-95 disabled:opacity-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
              >
                {busyId === m.material_id ? '删除中…' : '删除'}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

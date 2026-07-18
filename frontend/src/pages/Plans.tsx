// 方案列表页（plan/06 §三 AppShell 侧栏「方案列表」）：当前用户全部方案，
// 标题/版本/创建时间，点击进入详情；简单分页（上一页/下一页）。
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import EmptyState from '../components/EmptyState'
import { SkeletonCards } from '../components/Skeleton'
import { ApiError, listPlans, type PlanSummary } from '../lib/api'

const PAGE_SIZE = 20

export default function Plans() {
  const [items, setItems] = useState<PlanSummary[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    listPlans(undefined, page)
      .then((r) => {
        setItems(r.items)
        setTotal(r.total)
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : '加载失败'))
      .finally(() => setLoading(false))
  }, [page])

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">方案列表（{total}）</h2>
      {error && (
        <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
          {error}
        </div>
      )}
      {loading ? (
        <SkeletonCards count={4} />
      ) : items.length === 0 ? (
        <EmptyState
          icon="🗂️"
          title="还没有学习方案"
          description="从首页描述需求或上传资料，生成你的第一个方案"
          action={
            <Link to="/" className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">
              新建方案 →
            </Link>
          }
        />
      ) : (
        <ul className="space-y-2">
          {items.map((p) => (
            <li key={p.id}>
              <Link
                to={`/plans/${p.id}`}
                className="flex items-center justify-between rounded-lg border bg-white px-4 py-3 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:hover:bg-slate-700"
              >
                <span className="min-w-0 flex-1 truncate font-medium">
                  {p.topic || `方案 #${p.id}`}
                  {p.version > 1 && (
                    <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500 dark:bg-slate-700 dark:text-slate-400">
                      v{p.version}
                    </span>
                  )}
                </span>
                <span className="ml-3 shrink-0 text-xs text-slate-400">
                  {p.created_at.slice(0, 16).replace('T', ' ')}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {pages > 1 && (
        <div className="flex items-center gap-3 text-sm">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="rounded border px-3 py-1 disabled:opacity-40 dark:border-slate-600"
          >
            上一页
          </button>
          <span className="text-slate-500 dark:text-slate-400">
            {page} / {pages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(pages, p + 1))}
            disabled={page >= pages}
            className="rounded border px-3 py-1 disabled:opacity-40 dark:border-slate-600"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  )
}

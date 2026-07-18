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
  const [q, setQ] = useState('') // 主题搜索输入（iter 128）
  const [debouncedQ, setDebouncedQ] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // 搜索防抖（300ms）；搜索词变化回到第 1 页
  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedQ(q)
      setPage(1)
    }, 300)
    return () => clearTimeout(t)
  }, [q])

  useEffect(() => {
    let cancelled = false // err.md D6：快速翻页时旧响应不得覆盖新数据
    setLoading(true)
    listPlans(undefined, page, debouncedQ)
      .then((r) => {
        if (cancelled) return
        setItems(r.items)
        setTotal(r.total)
      })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : '加载失败') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [page, debouncedQ])

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-2xl font-bold">方案列表（{total}）</h2>
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索方案主题…"
          aria-label="搜索方案主题"
          className="w-56 rounded-lg border px-3 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500/60 dark:border-slate-600 dark:bg-slate-800"
        />
      </div>
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
          title={debouncedQ ? '没有匹配的方案' : '还没有学习方案'}
          description={debouncedQ ? '换个关键词试试' : '从首页描述需求或上传资料，生成你的第一个方案'}
          action={
            debouncedQ ? (
              <button
                onClick={() => setQ('')}
                className="rounded-lg border px-4 py-2 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
              >
                清除搜索
              </button>
            ) : (
              <Link to="/" className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">
                新建方案 →
              </Link>
            )
          }
        />
      ) : (
        <ul className="space-y-2">
          {items.map((p, idx) => (
            <li key={p.id} className="animate-fade-in-up" style={{ animationDelay: `${Math.min(idx, 10) * 40}ms` }}>
              <Link
                to={`/plans/${p.id}`}
                className="group flex items-center justify-between rounded-lg border bg-white px-4 py-3 transition-all duration-200 hover:-translate-y-0.5 hover:border-blue-200 hover:shadow-md dark:border-slate-700 dark:bg-slate-800 dark:hover:border-blue-900 dark:hover:bg-slate-700"
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
                <span aria-hidden="true" className="ml-2 shrink-0 text-slate-300 transition-all duration-200 group-hover:translate-x-0.5 group-hover:text-blue-500 dark:text-slate-600 dark:group-hover:text-blue-400">
                  →
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
            className="rounded border px-3 py-1 transition-all hover:bg-slate-100 active:scale-95 disabled:opacity-40 disabled:hover:bg-transparent disabled:active:scale-100 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            上一页
          </button>
          <span className="text-slate-500 dark:text-slate-400">
            {page} / {pages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(pages, p + 1))}
            disabled={page >= pages}
            className="rounded border px-3 py-1 transition-all hover:bg-slate-100 active:scale-95 disabled:opacity-40 disabled:hover:bg-transparent disabled:active:scale-100 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  )
}

// 个人知识库 Wiki 页（iter 100）：跨方案聚合知识节点，可检索/按状态筛选，
// 按方案分组浏览；点击节点跳方案详情。数据：GET /api/knowledge。
import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import EmptyState from '../components/EmptyState'
import NodeNoteEditor from '../components/NodeNoteEditor'
import { SkeletonCards } from '../components/Skeleton'
import { getKnowledge, type KnowledgeItem, type ProgressStatus } from '../lib/api'

const STATUS_LABEL: Record<ProgressStatus, string> = {
  not_started: '未开始',
  learning: '学习中',
  done: '已掌握',
}
const STATUS_STYLE: Record<ProgressStatus, string> = {
  not_started: 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300',
  learning: 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300',
  done: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300',
}
const DIFFICULTY_LABEL = ['', '入门', '基础', '中等', '进阶', '挑战']

export default function Wiki() {
  const qc = useQueryClient()
  const [q, setQ] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')
  const [status, setStatus] = useState<ProgressStatus | null>(null)
  const [openNoteId, setOpenNoteId] = useState<number | null>(null) // iter 105：展开中的笔记编辑器

  // 搜索防抖（300ms）：避免每击键一次请求
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300)
    return () => clearTimeout(t)
  }, [q])

  const { data, isLoading, error } = useQuery({
    queryKey: ['knowledge', debouncedQ, status],
    queryFn: () => getKnowledge({ q: debouncedQ, status: status ?? undefined }),
    staleTime: 15_000,
  })

  // 按方案分组（保持后端返回顺序：新方案在前）
  const groups = useMemo(() => {
    const map = new Map<number, { topic: string; items: KnowledgeItem[] }>()
    for (const item of data?.items ?? []) {
      const g = map.get(item.plan_id) ?? { topic: item.plan_topic || `方案 #${item.plan_id}`, items: [] }
      g.items.push(item)
      map.set(item.plan_id, g)
    }
    return [...map.entries()]
  }, [data])

  const stats = data?.stats

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-2xl font-bold">知识库</h2>
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索知识点…"
          aria-label="搜索知识点"
          className="w-56 rounded-lg border px-3 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500/60 dark:border-slate-600 dark:bg-slate-800"
        />
      </div>

      {/* 状态筛选 chips（带计数） */}
      {stats && (
        <div className="flex flex-wrap gap-2" role="group" aria-label="按学习状态筛选">
          {(Object.keys(STATUS_LABEL) as ProgressStatus[]).map((s) => (
            <button
              key={s}
              onClick={() => setStatus((prev) => (prev === s ? null : s))}
              aria-pressed={status === s}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-all active:scale-95 ${
                status === s
                  ? 'bg-blue-600 text-white shadow-sm'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-300 dark:hover:bg-slate-600'
              }`}
            >
              {STATUS_LABEL[s]} {stats[s] ?? 0}
            </button>
          ))}
        </div>
      )}

      {isLoading && <SkeletonCards count={3} />}
      {error && (
        <p role="alert" className="text-red-600 dark:text-red-400">
          加载失败：{error instanceof Error ? error.message : '未知错误'}
        </p>
      )}

      {!isLoading && !error && groups.length === 0 && (
        <EmptyState
          icon="🧠"
          title={debouncedQ || status ? '没有匹配的知识点' : '知识库还是空的'}
          description={
            debouncedQ || status
              ? '换个关键词或清除筛选试试'
              : '生成学习方案后，方案的知识图谱节点会自动汇聚到这里'
          }
          action={
            debouncedQ || status ? (
              <button
                onClick={() => {
                  setQ('')
                  setStatus(null)
                }}
                className="rounded-lg border px-3 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:hover:bg-slate-700"
              >
                清除筛选
              </button>
            ) : (
              <Link
                to="/"
                className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-500 hover:shadow-md active:scale-[.98]"
              >
                去新建方案
              </Link>
            )
          }
        />
      )}

      {groups.map(([planId, g], gi) => (
        <section
          key={planId}
          className="animate-fade-in-up rounded-xl border bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800"
          style={{ animationDelay: `${Math.min(gi, 8) * 60}ms` }}
        >
          <header className="flex items-center justify-between border-b px-4 py-2.5 dark:border-slate-700">
            <h3 className="truncate font-semibold">{g.topic}</h3>
            <Link
              to={`/plans/${planId}`}
              className="shrink-0 text-sm text-blue-600 transition-colors hover:text-blue-500 hover:underline dark:text-blue-400"
            >
              查看方案 →
            </Link>
          </header>
          <ul className="grid gap-2 p-3 sm:grid-cols-2">
            {g.items.map((n) => (
              <li key={n.node_id}>
                <div className="rounded-lg border transition-all hover:border-blue-300 hover:shadow-md dark:border-slate-700 dark:hover:border-blue-700">
                  <div className="flex items-start">
                    <Link to={`/plans/${planId}`} className="block min-w-0 flex-1 px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="min-w-0 truncate text-sm font-medium">
                          {n.must_learn && <span className="mr-1 text-amber-500" title="必学">★</span>}
                          {n.name}
                        </span>
                        <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${STATUS_STYLE[n.status]}`}>
                          {STATUS_LABEL[n.status]}
                        </span>
                      </div>
                      <div className="mt-1.5 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                        <span>{DIFFICULTY_LABEL[n.difficulty] ?? `难度${n.difficulty}`}</span>
                        <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700">
                          <span
                            className="block h-full rounded-full bg-emerald-500 transition-all"
                            style={{ width: `${Math.round(n.mastery * 100)}%` }}
                            aria-label={`掌握度 ${Math.round(n.mastery * 100)}%`}
                          />
                        </span>
                        <span className="tabular-nums">{Math.round(n.mastery * 100)}%</span>
                      </div>
                    </Link>
                    {/* 笔记开关（iter 105）：有笔记时 📝 常驻着色 */}
                    <button
                      onClick={() => setOpenNoteId((prev) => (prev === n.node_id ? null : n.node_id))}
                      aria-expanded={openNoteId === n.node_id}
                      aria-label={`${n.name} 笔记`}
                      title={n.has_note ? '查看/编辑笔记' : '添加笔记'}
                      className={`shrink-0 rounded-lg px-2 py-2 text-sm transition-all hover:bg-slate-100 active:scale-90 dark:hover:bg-slate-700 ${
                        n.has_note ? 'text-amber-500' : 'text-slate-300 dark:text-slate-600'
                      }`}
                    >
                      📝
                    </button>
                  </div>
                  {openNoteId === n.node_id && (
                    <NodeNoteEditor
                      nodeId={n.node_id}
                      onSaved={() => qc.invalidateQueries({ queryKey: ['knowledge'] })}
                    />
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}

// 方案列表页（plan/06 §三 AppShell 侧栏「方案列表」）：当前用户全部方案，
// 标题/版本/创建时间，点击进入详情；简单分页（上一页/下一页）。
// iter 189 F4：上方新增「进行中的会话」占位区——running/待澄清/失败/中断/草稿会话
// 立即可见可点（此前生成中切页后任务消失、失败/中断会话无入口）；分区渲染避免状态
// 迁移引起全列表跳动；有活跃会话时 5s 轮询（静默期零轮询，引擎 SSE 已推动失效）。
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import EmptyState from '../components/EmptyState'
import { SkeletonCards } from '../components/Skeleton'
import { PHASE_META, TOOL_LABELS } from '../components/TaskCenter'
import { toast } from '../components/Toast'
import { ApiError, deleteSession, listPlans, listSessions, type PlanSummary } from '../lib/api'
import { engine } from '../lib/taskEngine'
import { planTaskId, useTaskStore, type SessionStatusItem } from '../store/tasks'

const PAGE_SIZE = 20

/** 占位行状态徽标（服务端状态 -> 展示语义；error.code=cancelled 归「已取消」）。 */
function statusMeta(s: SessionStatusItem): { label: string; badge: string } {
  if (s.status === 'running') return PHASE_META.running
  if (s.status === 'paused_clarify') return PHASE_META.clarify
  if (s.status === 'interrupted') return PHASE_META.interrupted
  if (s.status === 'error') return s.error?.code === 'cancelled' ? PHASE_META.cancelled : PHASE_META.error
  return { label: '草稿', badge: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400' } // idle
}

function relTime(isoish: string | null): string {
  if (!isoish) return ''
  const t = Date.parse(isoish.replace(' ', 'T'))
  if (Number.isNaN(t)) return isoish.slice(0, 16)
  const diff = Date.now() - t
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  return `${Math.floor(diff / 86_400_000)} 天前`
}

/** 是否需要在占位区显示（done 由正常方案行覆盖；error 有旧方案时方案行已够）。 */
function isPlaceholder(s: SessionStatusItem): boolean {
  if (s.status === 'running' || s.status === 'paused_clarify' || s.status === 'interrupted') return true
  if (s.status === 'error' || s.status === 'idle') return !s.latest_plan
  return false
}

const hasActiveSession = (d: { items: SessionStatusItem[] } | undefined) =>
  !!d?.items.some((i) => i.status === 'running' || i.status === 'paused_clarify')

export default function Plans() {
  const [items, setItems] = useState<PlanSummary[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [q, setQ] = useState('') // 主题搜索输入（iter 128）
  const [debouncedQ, setDebouncedQ] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [deletingId, setDeletingId] = useState<number | null>(null) // 删除防重入（iter 130）
  const [reloadKey, setReloadKey] = useState(0) // 删除后重取本页
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const removeTask = useTaskStore((s) => s.remove)

  // iter 189 F4：会话状态查询（占位区数据源）。轮询仅在有活跃会话时开启（5s）——
  // 引擎 SSE 相位迁移已 invalidate ['sessions']，轮询是多标签/断流兜底。
  const { data: sessionsData } = useQuery({
    queryKey: ['sessions'],
    queryFn: () => listSessions({ limit: 50 }),
    refetchInterval: (query) => (hasActiveSession(query.state.data) ? 5000 : false),
  })
  // 占位区不受方案搜索词影响（活跃任务任何时候都不应被搜索过滤隐藏）
  const placeholders = (sessionsData?.items ?? []).filter(isPlaceholder)

  // 删除方案（iter 130）：级联删除其会话（全部版本/进度/自学材料/文件）
  async function onDelete(p: PlanSummary) {
    if (deletingId !== null) return
    if (!window.confirm(`确定删除方案「${p.topic || `#${p.id}`}」？\n将同时删除其全部版本、学习进度、自学材料与源资料，且不可恢复。`)) return
    setDeletingId(p.id)
    setError('')
    try {
      await deleteSession(p.session_id)
      removeTask(planTaskId(p.session_id))
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      // 删的是本页最后一条且非首页 -> 回一页，否则原地刷新
      if (items.length === 1 && page > 1) setPage((v) => v - 1)
      else setReloadKey((k) => k + 1)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '删除失败，请重试')
    } finally {
      setDeletingId(null)
    }
  }

  // 删除占位会话（iter 189 F4）：running 时明确提示将取消生成
  async function onDeleteSession(s: SessionStatusItem) {
    if (deletingId !== null) return
    const name = s.title || '未命名会话'
    const msg =
      s.status === 'running'
        ? `会话「${name}」正在生成中，删除将取消生成并移除其全部数据，且不可恢复。确定删除？`
        : `确定删除会话「${name}」？其源资料与生成记录将一并删除，且不可恢复。`
    if (!window.confirm(msg)) return
    setDeletingId(s.session_id)
    setError('')
    try {
      await deleteSession(s.session_id)
      removeTask(planTaskId(s.session_id))
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
      setReloadKey((k) => k + 1)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '删除失败，请重试')
    } finally {
      setDeletingId(null)
    }
  }

  // 取消占位会话的生成（后端取消编排器任务，行变「已取消」）
  async function onCancelSession(s: SessionStatusItem) {
    if (!window.confirm(`确定取消「${s.title || '学习方案'}」的生成吗？已产生的费用不会退回。`)) return
    try {
      await engine.cancel(s.session_id)
      void queryClient.invalidateQueries({ queryKey: ['sessions'] })
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '取消失败，请重试')
    }
  }

  // 占位行点击跳转矩阵（iter 189 F4）
  function openSession(s: SessionStatusItem) {
    if (s.status === 'paused_clarify') navigate(`/clarify/${s.session_id}`)
    else navigate(`/generate/${s.session_id}`) // running/error/interrupted/idle 均进进度页对应视图
  }

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
        setError('') // 重试成功后清除错误（否则错误 banner 残留）
      })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : '加载失败') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [page, debouncedQ, reloadKey])

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
      {error && (loading || items.length > 0) && (
        // 列表有内容（或加载中）时错误以 banner 提示；列表为空时由下方错误卡承担（避免双份错误文案）
        <div role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {error}
        </div>
      )}
      {/* iter 189 F4：进行中的会话占位区（running/待澄清/失败/中断/草稿；分区渲染防跳动） */}
      {placeholders.length > 0 && (
        <section aria-label="进行中的会话" className="space-y-2">
          <h3 className="text-sm font-medium text-slate-500 dark:text-slate-400">进行中的会话（{placeholders.length}）</h3>
          <ul className="space-y-2">
            {placeholders.map((s) => {
              const meta = statusMeta(s)
              const live = s.status === 'running' ? s.live : null
              const total = live?.total_steps || 8
              const cur = live?.step_index ?? 0
              const pct = Math.min(100, Math.round((Math.max(0, cur - 1) / total) * 100))
              return (
                <li key={s.session_id}>
                  <div className="rounded-lg border border-dashed bg-white/60 px-4 py-3 dark:border-slate-600 dark:bg-slate-800/60">
                    <div className="flex items-center gap-2">
                      <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs ${meta.badge}`}>{meta.label}</span>
                      <button
                        type="button"
                        onClick={() => openSession(s)}
                        className="min-w-0 flex-1 truncate text-left font-medium text-slate-700 transition-colors hover:text-blue-600 dark:text-slate-200 dark:hover:text-blue-400"
                      >
                        {s.title || '未命名会话'}
                      </button>
                      <span className="shrink-0 text-xs text-slate-400">{relTime(s.updated_at ?? s.created_at)}</span>
                      {s.status === 'running' && (
                        <button
                          type="button"
                          onClick={() => onCancelSession(s)}
                          className="shrink-0 text-xs text-slate-400 underline underline-offset-2 hover:text-red-600 dark:hover:text-red-400"
                        >
                          取消
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => onDeleteSession(s)}
                        disabled={deletingId !== null}
                        aria-label={`删除会话 ${s.title || s.session_id}`}
                        title="删除该会话（含源资料与生成记录）"
                        className="shrink-0 rounded-lg border border-transparent px-2 py-1 text-xs text-slate-300 transition-all hover:border-red-200 hover:bg-red-50 hover:text-red-600 active:scale-95 disabled:opacity-50 dark:text-slate-600 dark:hover:border-red-900 dark:hover:bg-red-950 dark:hover:text-red-400"
                      >
                        {deletingId === s.session_id ? '…' : '🗑'}
                      </button>
                    </div>
                    {s.status === 'running' && (
                      <div className="mt-2">
                        <div
                          role="progressbar"
                          aria-valuenow={pct}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-label={`生成进度 ${pct}%`}
                          className="h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700"
                        >
                          <div className="h-full rounded-full bg-blue-500 transition-all duration-500" style={{ width: `${pct}%` }} />
                        </div>
                        <div className="mt-1 text-xs text-slate-400">
                          {live
                            ? `第 ${cur}/${total} 步${live.current_tool ? ` · ${TOOL_LABELS[live.current_tool] ?? live.current_tool}` : ''}`
                            : '生成进行中…'}
                        </div>
                      </div>
                    )}
                    {s.status === 'error' && s.error?.message && (
                      <div className="mt-1 truncate text-xs text-red-500 dark:text-red-400">{s.error.message}</div>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        </section>
      )}
      {loading ? (
        <SkeletonCards count={4} />
      ) : error && items.length === 0 ? (
        // 加载失败时不落「还没有学习方案」空态（老用户会误以为方案全丢）——给错误卡 + 重试
        <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-6 text-center dark:border-red-900 dark:bg-red-950">
          <p className="text-sm text-red-700 dark:text-red-300">方案列表加载失败：{error}</p>
          <button
            onClick={() => setReloadKey((k) => k + 1)}
            className="mt-3 rounded-lg border border-red-300 px-4 py-1.5 text-sm text-red-700 transition-all hover:bg-red-100 active:scale-95 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-900"
          >
            重试
          </button>
        </div>
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
              <div className="group flex items-center rounded-lg border bg-white transition-all duration-200 hover:-translate-y-0.5 hover:border-blue-200 hover:shadow-md dark:border-slate-700 dark:bg-slate-800 dark:hover:border-blue-900 dark:hover:bg-slate-700">
                <Link to={`/plans/${p.id}`} className="flex min-w-0 flex-1 items-center justify-between px-4 py-3">
                  <span className="min-w-0 flex-1 truncate font-medium">
                    {p.topic || `方案 #${p.id}`}
                    {p.version > 1 && (
                      <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500 dark:bg-slate-700 dark:text-slate-400">
                        v{p.version}
                      </span>
                    )}
                  </span>
                  <span className="ml-3 shrink-0 text-xs tabular-nums text-slate-400">
                    {p.created_at.slice(0, 16).replace('T', ' ')}
                  </span>
                  <span aria-hidden="true" className="ml-2 shrink-0 text-slate-300 transition-all duration-200 group-hover:translate-x-0.5 group-hover:text-blue-500 dark:text-slate-600 dark:group-hover:text-blue-400">
                    →
                  </span>
                </Link>
                {/* 删除（iter 130）：级联删会话（全部版本/进度/材料），confirm 确认 */}
                <button
                  onClick={() => onDelete(p)}
                  disabled={deletingId !== null}
                  aria-label={`删除方案 ${p.topic || p.id}`}
                  title="删除该方案（含其全部版本/进度/材料）"
                  className="mr-2 shrink-0 rounded-lg border border-transparent px-2 py-1 text-xs text-slate-300 transition-all hover:border-red-200 hover:bg-red-50 hover:text-red-600 active:scale-95 disabled:opacity-50 dark:text-slate-600 dark:hover:border-red-900 dark:hover:bg-red-950 dark:hover:text-red-400"
                >
                  {deletingId === p.id ? '…' : '🗑'}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {pages > 1 && (
        <div className="flex items-center gap-3 text-sm">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="rounded-lg border px-3 py-1 transition-all hover:bg-slate-100 active:scale-95 disabled:opacity-40 disabled:hover:bg-transparent disabled:active:scale-100 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            上一页
          </button>
          <span className="tabular-nums text-slate-500 dark:text-slate-400">
            {page} / {pages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(pages, p + 1))}
            disabled={page >= pages}
            className="rounded-lg border px-3 py-1 transition-all hover:bg-slate-100 active:scale-95 disabled:opacity-40 disabled:hover:bg-transparent disabled:active:scale-100 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  )
}

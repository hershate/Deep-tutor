import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, getReviewQueue, updateProgress, type ProgressStatus, type ReviewQueueItem } from '../lib/api'
import { toast } from '../components/Toast'
import EmptyState from '../components/EmptyState'
import { Skeleton } from '../components/Skeleton'

// iter 281：跨方案统一复习工作流页 /review。
// 此前复习入口分散四处（顶栏徽标→/wiki、Wiki 待复习 chips、Wiki 错题卡、Learn 单方案复习），
// 无「打开即逐张翻卡复习」的流式体验。本页聚合全部到期节点（GET /progress/review/queue），
// 翻卡自评→回写掌握度（POST /progress，SM-2-lite 据自评重排下次复习日）→自动下一张。
// 纯编排，零新增后端写入面（仅复用既有端点）；卡片背面展示本人节点笔记（无笔记降级自评提示）。

type Rating = 'forgot' | 'fuzzy' | 'remembered'

// 自评→进度回写映射：quiz_score 主导 compute_mastery（0.7*score+0.3*old），
// 故 记住了→高分(远期)/模糊→中(近期)/忘了→低(明日)——符合间隔重复语义。
const RATING_META: Record<Rating, { key: string; label: string; status: ProgressStatus; score: number; cls: string }> = {
  forgot: { key: '1', label: '忘了', status: 'not_started', score: 0.0, cls: 'border-red-300 text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950' },
  fuzzy: { key: '2', label: '模糊', status: 'learning', score: 0.5, cls: 'border-amber-300 text-amber-700 hover:bg-amber-50 dark:border-amber-900 dark:text-amber-300 dark:hover:bg-amber-950' },
  remembered: { key: '3', label: '记住了', status: 'done', score: 1.0, cls: 'border-emerald-300 text-emerald-700 hover:bg-emerald-50 dark:border-emerald-900 dark:text-emerald-300 dark:hover:bg-emerald-950' },
}
const RATINGS: Rating[] = ['forgot', 'fuzzy', 'remembered']

const DIFFICULTY_LABEL: Record<number, string> = { 1: '入门', 2: '基础', 3: '中等', 4: '进阶', 5: '挑战' }

function pct(m: number) {
  return `${Math.round(m * 100)}%`
}

export default function Review() {
  const qc = useQueryClient()
  // staleTime=Infinity：复习会话内快照稳定——评分布点不触发重取扰动队列顺序；
  // 「再复习一次」显式 refetch 拉取最新到期集
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['review-queue'],
    queryFn: getReviewQueue,
    staleTime: Infinity,
  })
  const items = useMemo(() => data?.items ?? [], [data])

  const [index, setIndex] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [stats, setStats] = useState({ forgot: 0, fuzzy: 0, remembered: 0 })

  const done = items.length > 0 && index >= items.length
  const current: ReviewQueueItem | undefined = done ? undefined : items[index]

  const advance = useCallback(() => {
    setFlipped(false)
    setIndex((i) => i + 1)
  }, [])

  const rate = useCallback(
    async (r: Rating) => {
      const it = items[index]
      if (!it || submitting) return
      if (!flipped) {
        setFlipped(true) // 未翻面先翻面（诚实回忆：先自检再看背面）
        return
      }
      setSubmitting(true)
      const meta = RATING_META[r]
      try {
        await updateProgress(it.plan_id, it.node_id, meta.status, meta.score)
        setStats((s) => ({ ...s, [r]: s[r] + 1 }))
        // 徽标计数同步（评分布点改变到期集）
        qc.invalidateQueries({ queryKey: ['review-due'] })
        advance()
      } catch (e) {
        toast.error(`回写失败：${e instanceof ApiError ? e.message : '网络错误'}`)
      } finally {
        setSubmitting(false)
      }
    },
    [items, index, flipped, submitting, advance, qc],
  )

  // 键盘：Space/Enter 翻面、1/2/3 自评、→ 跳过。仅在卡片态生效（完成/空/加载态不劫持）。
  useEffect(() => {
    if (done || items.length === 0) return
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
      if (document.querySelector('[role="dialog"]')) return
      if (e.key === ' ' || e.key === 'Enter') {
        e.preventDefault()
        setFlipped((f) => !f)
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        advance()
      } else if (e.key === '1' || e.key === '2' || e.key === '3') {
        e.preventDefault()
        const r = RATINGS[Number(e.key) - 1]
        void rate(r)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [done, items.length, advance, rate])

  function restart() {
    setIndex(0)
    setFlipped(false)
    setStats({ forgot: 0, fuzzy: 0, remembered: 0 })
    void refetch()
  }

  // ---- 状态分支 ----
  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-2 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    )
  }
  if (isError) {
    return (
      <EmptyState
        icon="⚠️"
        title="复习队列加载失败"
        description={error instanceof ApiError ? error.message : '请稍后重试'}
        action={
          <button
            type="button"
            onClick={() => refetch()}
            className="rounded-lg border px-3 py-1.5 text-sm transition-colors hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            重试
          </button>
        }
      />
    )
  }
  if (items.length === 0) {
    return (
      <EmptyState
        icon="🎉"
        title="今天没有需要复习的知识点"
        description="间隔重复到期的知识点会出现在这里。去学习新内容或查看全部方案吧。"
        action={
          <Link to="/plans" className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-blue-700">
            查看方案
          </Link>
        }
      />
    )
  }
  if (done) {
    const total = stats.forgot + stats.fuzzy + stats.remembered
    return (
      <div className="mx-auto max-w-lg animate-fade-in-up space-y-4 text-center">
        <div className="text-5xl">🎯</div>
        <h1 className="text-xl font-bold text-slate-800 dark:text-slate-100">本次复习完成</h1>
        <p className="text-sm text-slate-600 dark:text-slate-300">
          共复习 <span className="tabular-nums font-medium">{total}</span> 个知识点
        </p>
        <div className="grid grid-cols-3 gap-2">
          {RATINGS.map((r) => (
            <div key={r} className="rounded-lg border bg-white p-3 dark:border-slate-700 dark:bg-slate-800">
              <div className="text-2xl font-bold tabular-nums text-slate-800 dark:text-slate-100">{stats[r]}</div>
              <div className="text-xs text-slate-500 dark:text-slate-400">{RATING_META[r].label}</div>
            </div>
          ))}
        </div>
        <div className="flex justify-center gap-2">
          <button
            type="button"
            onClick={restart}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
          >
            再复习一次
          </button>
          <Link
            to="/plans"
            className="rounded-lg border px-4 py-2 text-sm transition-colors hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            返回方案
          </Link>
        </div>
      </div>
    )
  }

  // ---- 卡片态 ----
  const it = current!
  const progressPct = Math.round((index / items.length) * 100)

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold text-slate-800 dark:text-slate-100">
          复习 <span className="ml-1 text-sm font-normal text-slate-400">· 间隔重复到期</span>
        </h1>
        <Link to="/plans" className="text-sm text-slate-400 transition-colors hover:text-slate-600 dark:hover:text-slate-200">
          退出
        </Link>
      </div>

      {/* 进度 */}
      <div>
        <div className="mb-1 flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
          <span className="tabular-nums">
            {index + 1} / {items.length}
          </span>
          <span className="tabular-nums">{progressPct}%</span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
          <div className="h-full rounded-full bg-blue-600 transition-all duration-300" style={{ width: `${progressPct}%` }} />
        </div>
      </div>

      {/* 卡片 */}
      <button
        type="button"
        onClick={() => setFlipped((f) => !f)}
        aria-label={flipped ? '点击收起卡片背面' : '点击翻看卡片背面'}
        className="block w-full animate-fade-in-up rounded-[var(--dt-card-radius,0.75rem)] border bg-white p-6 text-left shadow-sm transition-all hover:shadow-md dark:border-slate-700 dark:bg-slate-800"
      >
        {!flipped ? (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600 dark:bg-slate-700 dark:text-slate-300">
                {it.plan_topic || `方案 #${it.plan_id}`}
              </span>
              {it.must_learn && <span className="text-amber-500" title="必学">★ 必学</span>}
              {it.difficulty != null && (
                <span className="text-slate-400">{DIFFICULTY_LABEL[it.difficulty] ?? `难度 ${it.difficulty}`}</span>
              )}
            </div>
            <h2 className="text-2xl font-bold text-slate-800 dark:text-slate-100">{it.name}</h2>
            <p className="text-sm text-slate-400">回忆这个知识点的要点，然后翻面自评</p>
            <p className="text-xs text-slate-400">空格 / Enter 翻面</p>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span className="font-medium text-slate-700 dark:text-slate-200">{it.name}</span>
              <span className="text-slate-400">当前掌握度 {pct(it.mastery)}</span>
            </div>
            {it.note ? (
              <div className="whitespace-pre-wrap rounded-lg border-l-2 border-blue-300 bg-blue-50/50 p-3 text-sm text-slate-700 dark:border-blue-700 dark:bg-blue-950/40 dark:text-slate-200">
                {it.note}
              </div>
            ) : (
              <div className="rounded-lg border border-dashed p-3 text-sm text-slate-500 dark:border-slate-600 dark:text-slate-400">
                这个知识点还没有笔记。你记得它的要点吗？
                <Link
                  to={`/learn/${it.plan_id}`}
                  onClick={(e) => e.stopPropagation()}
                  className="ml-1 text-blue-600 underline underline-offset-2 dark:text-blue-400"
                >
                  去学习中心补充
                </Link>
              </div>
            )}
            <div className="flex flex-wrap items-center justify-between gap-2 pt-1 text-xs text-slate-400">
              <span>按下对应的数字键自评（回写掌握度并重排复习日）</span>
              <Link to={`/learn/${it.plan_id}`} onClick={(e) => e.stopPropagation()} className="text-blue-600 underline underline-offset-2 dark:text-blue-400">
                查看方案学习中心
              </Link>
            </div>
          </div>
        )}
      </button>

      {/* 自评按钮（翻面后可用） */}
      <div className="grid grid-cols-3 gap-2">
        {RATINGS.map((r) => {
          const meta = RATING_META[r]
          return (
            <button
              key={r}
              type="button"
              disabled={submitting}
              onClick={() => rate(r)}
              className={`rounded-lg border bg-white py-2.5 text-sm font-medium transition-all hover:shadow-sm disabled:opacity-50 dark:bg-slate-800 ${meta.cls}`}
            >
              <span className="mr-1 text-xs text-slate-400">{meta.key}</span>
              {meta.label}
            </button>
          )
        })}
      </div>
      <div className="flex justify-center">
        <button
          type="button"
          onClick={advance}
          className="text-xs text-slate-400 underline underline-offset-2 transition-colors hover:text-slate-600 dark:hover:text-slate-200"
        >
          跳过这个（→）
        </button>
      </div>
    </div>
  )
}

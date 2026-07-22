import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  getReviewQueue,
  getWrongQuizQueue,
  submitQuiz,
  updateProgress,
  type ProgressStatus,
  type ReviewQueueItem,
  type WrongQuizItem,
} from '../lib/api'
import { toast } from '../components/Toast'
import EmptyState from '../components/EmptyState'
import { Skeleton } from '../components/Skeleton'

// iter 281：跨方案统一复习工作流页 /review；iter 285：并入错题重练卡片。
// 此前复习入口分散四处（顶栏徽标/Wiki 待复习/Wiki 错题/Learn 单方案复习），无「打开即逐张复习」流式体验。
// 本页聚合两类待复习内容：① 到期知识点（recall，翻卡自评→回写掌握度）；
// ② 错题（quiz，重答→服务器判分→重答正确即移出错题集）。两者构成完整「今日复习」。
// 纯编排，复用既有端点（review/queue + wrong-queue + progress + quizzes/submit），零新增写入面。

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

type ReviewCard =
  | ({ kind: 'recall' } & ReviewQueueItem)
  | ({ kind: 'quiz' } & WrongQuizItem)

// iter 290：复习模式筛选（全部/仅知识点/仅错题）
type ReviewMode = 'all' | 'recall' | 'quiz'
const MODE_KEY = 'dt-review-mode'
const REVIEW_MODES: { value: ReviewMode; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'recall', label: '知识点' },
  { value: 'quiz', label: '错题' },
]
function readReviewMode(): ReviewMode {
  try {
    const v = localStorage.getItem(MODE_KEY)
    return v === 'recall' || v === 'quiz' ? v : 'all'
  } catch {
    return 'all'
  }
}

function pct(m: number) {
  return `${Math.round(m * 100)}%`
}

export default function Review() {
  const qc = useQueryClient()
  // 一次请求并行取两类待复习内容；staleTime=Infinity 会话内快照稳定（评分布点不扰动队列顺序）
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['review-queue-all'],
    queryFn: async () => {
      const [recall, quizzes] = await Promise.all([getReviewQueue(), getWrongQuizQueue()])
      return { recall: recall.items, quizzes: quizzes.items }
    },
    staleTime: Infinity,
  })
  // iter 290：复习模式（全部/仅知识点/仅错题），localStorage 记忆（iter 280 同款静默降级）
  const [mode, setMode] = useState<ReviewMode>(readReviewMode)
  // iter 291：本轮薄弱项（忘了/仍错）集合 + 重练覆盖队列（非 null 时 queue 取此值——收窄再复习）
  const [missed, setMissed] = useState<ReviewCard[]>([])
  const [extraQueue, setExtraQueue] = useState<ReviewCard[] | null>(null)
  // recall 在前、错题在后（确定性顺序；先扫记忆再重练错题的自然节奏）
  const fullQueue: ReviewCard[] = useMemo(() => {
    const r = (data?.recall ?? []).map((d) => ({ kind: 'recall' as const, ...d }))
    const q = (data?.quizzes ?? []).map((d) => ({ kind: 'quiz' as const, ...d }))
    return [...r, ...q]
  }, [data])
  const recallCount = data?.recall.length ?? 0
  const quizCount = data?.quizzes.length ?? 0
  // 重练覆盖优先；否则按模式过滤（全部 = recall+quiz；recall/quiz 仅对应类）
  const queue: ReviewCard[] = useMemo(() => {
    if (extraQueue) return extraQueue
    return mode === 'all' ? fullQueue : fullQueue.filter((c) => c.kind === mode)
  }, [fullQueue, mode, extraQueue])

  const [index, setIndex] = useState(0)
  // recall 卡态
  const [flipped, setFlipped] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  // quiz 卡态
  const [quizSelected, setQuizSelected] = useState<string | null>(null)
  const [quizJudged, setQuizJudged] = useState(false)
  const [quizJudging, setQuizJudging] = useState(false)
  const [quizCorrect, setQuizCorrect] = useState<boolean | null>(null)
  // 统计
  const [stats, setStats] = useState({ remembered: 0, fuzzy: 0, forgot: 0, quizCorrect: 0, quizWrong: 0 })

  const done = queue.length > 0 && index >= queue.length
  const current: ReviewCard | undefined = done ? undefined : queue[index]

  const advance = useCallback(() => {
    setFlipped(false)
    setQuizSelected(null)
    setQuizJudged(false)
    setQuizCorrect(null)
    setIndex((i) => i + 1)
  }, [])

  const rate = useCallback(
    async (r: Rating) => {
      const card = queue[index]
      if (!card || card.kind !== 'recall' || submitting) return
      if (!flipped) {
        setFlipped(true) // 未翻面先翻面（诚实回忆：先自检再看背面）
        return
      }
      setSubmitting(true)
      const meta = RATING_META[r]
      try {
        await updateProgress(card.plan_id, card.node_id, meta.status, meta.score)
        setStats((s) => ({ ...s, [r]: s[r] + 1 }))
        if (r === 'forgot') setMissed((m) => [...m, card]) // iter 291：忘了计入薄弱
        qc.invalidateQueries({ queryKey: ['review-due'] }) // 徽标计数同步
        advance()
      } catch (e) {
        toast.error(`回写失败：${e instanceof ApiError ? e.message : '网络错误'}`)
      } finally {
        setSubmitting(false)
      }
    },
    [queue, index, flipped, submitting, advance, qc],
  )

  // 错题判分（镜像 QuizModal：submit 记录 attempt；重答正确 -> 最新 attempt correct -> 移出错题集）
  const submitQuizCard = useCallback(async () => {
    const card = queue[index]
    if (!card || card.kind !== 'quiz' || quizJudged || quizJudging || !quizSelected) return
    setQuizJudging(true)
    try {
      const res = await submitQuiz(card.id, { selected: quizSelected })
      setQuizJudged(true)
      setQuizCorrect(res.correct === true)
      setStats((s) => ({
        ...s,
        quizCorrect: s.quizCorrect + (res.correct === true ? 1 : 0),
        quizWrong: s.quizWrong + (res.correct === true ? 0 : 1),
      }))
      if (res.correct !== true) setMissed((m) => [...m, card]) // iter 291：仍错计入薄弱
    } catch (e) {
      toast.error(`判分失败：${e instanceof ApiError ? e.message : '网络错误'}`)
    } finally {
      setQuizJudging(false)
    }
  }, [queue, index, quizJudged, quizJudging, quizSelected])

  // 键盘：recall——Space/Enter 翻面、1/2/3 自评、→ 跳过；
  // quiz——1-9 选选项、Enter 判定，判完 Enter/→/空格 下一题。按当前卡 kind 派发（无跨类冲突）。
  useEffect(() => {
    if (done || queue.length === 0) return
    const card = queue[index]
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
      if (document.querySelector('[role="dialog"]')) return
      if (card.kind === 'recall') {
        if (e.key === ' ' || e.key === 'Enter') {
          e.preventDefault()
          setFlipped((f) => !f)
        } else if (e.key === 'ArrowRight') {
          e.preventDefault()
          advance()
        } else if (e.key === '1' || e.key === '2' || e.key === '3') {
          e.preventDefault()
          void rate(RATINGS[Number(e.key) - 1])
        }
      } else {
        if (!quizJudged) {
          if (/^[1-9]$/.test(e.key)) {
            const opt = card.options[Number(e.key) - 1]
            if (opt !== undefined) {
              e.preventDefault()
              setQuizSelected(opt)
            }
          } else if (e.key === 'Enter' && quizSelected && !quizJudging) {
            e.preventDefault()
            void submitQuizCard()
          }
        } else if (e.key === 'Enter' || e.key === 'ArrowRight' || e.key === ' ') {
          e.preventDefault()
          advance()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [done, queue, index, flipped, submitting, quizJudged, quizJudging, quizSelected])

  function restart() {
    setIndex(0)
    setFlipped(false)
    setQuizSelected(null)
    setQuizJudged(false)
    setQuizCorrect(null)
    setStats({ remembered: 0, fuzzy: 0, forgot: 0, quizCorrect: 0, quizWrong: 0 })
    setMissed([])
    setExtraQueue(null)
    void refetch()
  }
  // iter 290：切模式重置到新筛选队列开头（清卡态；统计清零避免跨模式混计）
  function changeMode(m: ReviewMode) {
    if (m === mode) return
    setMode(m)
    setIndex(0)
    setFlipped(false)
    setQuizSelected(null)
    setQuizJudged(false)
    setQuizCorrect(null)
    setStats({ remembered: 0, fuzzy: 0, forgot: 0, quizCorrect: 0, quizWrong: 0 })
    setMissed([])
    setExtraQueue(null)
    try {
      localStorage.setItem(MODE_KEY, m)
    } catch {
      /* 写失败仅本次会话生效 */
    }
  }
  // iter 291：重练薄弱——以上轮忘了/仍错项为覆盖队列再过一遍（薄弱集在新轮重新累计，支持多轮收窄）
  function requeueWeak() {
    if (missed.length === 0) return
    setExtraQueue(missed)
    setMissed([])
    setIndex(0)
    setFlipped(false)
    setQuizSelected(null)
    setQuizJudged(false)
    setQuizCorrect(null)
    setStats({ remembered: 0, fuzzy: 0, forgot: 0, quizCorrect: 0, quizWrong: 0 })
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
  if (queue.length === 0) {
    const total = recallCount + quizCount
    const emptyForMode = total > 0 // 有内容但当前模式筛空（如「仅错题」但无错题）
    return (
      <EmptyState
        icon={emptyForMode ? '🗂️' : '🎉'}
        title={emptyForMode ? (mode === 'quiz' ? '暂无错题' : '暂无到期知识点') : '今天没有需要复习的内容'}
        description={
          emptyForMode
            ? mode === 'quiz'
              ? '错题都已答对或尚未作答。切换到「全部」或「知识点」继续。'
              : '到期知识点已复习或已掌握。切换到「全部」或「错题」继续。'
            : '到期知识点与错题都已掌握。去学习新内容或做一组测验吧。'
        }
        action={
          emptyForMode ? (
            <button
              type="button"
              onClick={() => changeMode('all')}
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-blue-700"
            >
              查看全部
            </button>
          ) : (
            <Link to="/plans" className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-blue-700">
              查看方案
            </Link>
          )
        }
      />
    )
  }
  if (done) {
    const recalled = stats.remembered + stats.fuzzy + stats.forgot
    const quizzed = stats.quizCorrect + stats.quizWrong
    return (
      <div className="mx-auto max-w-lg animate-fade-in-up space-y-4 text-center">
        <div className="text-5xl">🎯</div>
        <h1 className="text-xl font-bold text-slate-800 dark:text-slate-100">本次复习完成</h1>
        <p className="text-sm text-slate-600 dark:text-slate-300">
          共复习 <span className="tabular-nums font-medium">{recalled + quizzed}</span> 项
        </p>
        {recalled > 0 && (
          <div>
            <p className="mb-1 text-xs text-slate-400">知识点自评</p>
            <div className="grid grid-cols-3 gap-2">
              {RATINGS.map((r) => (
                <div key={r} className="rounded-lg border bg-white p-3 dark:border-slate-700 dark:bg-slate-800">
                  <div className="text-2xl font-bold tabular-nums text-slate-800 dark:text-slate-100">{stats[r]}</div>
                  <div className="text-xs text-slate-500 dark:text-slate-400">{RATING_META[r].label}</div>
                </div>
              ))}
            </div>
          </div>
        )}
        {quizzed > 0 && (
          <div>
            <p className="mb-1 text-xs text-slate-400">错题重练</p>
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 dark:border-emerald-900 dark:bg-emerald-950">
                <div className="text-2xl font-bold tabular-nums text-emerald-700 dark:text-emerald-300">{stats.quizCorrect}</div>
                <div className="text-xs text-emerald-600 dark:text-emerald-400">答对</div>
              </div>
              <div className="rounded-lg border border-red-200 bg-red-50 p-3 dark:border-red-900 dark:bg-red-950">
                <div className="text-2xl font-bold tabular-nums text-red-700 dark:text-red-300">{stats.quizWrong}</div>
                <div className="text-xs text-red-600 dark:text-red-400">仍错</div>
              </div>
            </div>
          </div>
        )}
        <div className="flex flex-wrap justify-center gap-2">
          {/* iter 291：重练薄弱——仅重过本次忘了/仍错的项（多轮收窄直至全过） */}
          {missed.length > 0 && (
            <button
              type="button"
              onClick={requeueWeak}
              title="仅重练本次忘了 / 仍错的项"
              className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-amber-600"
            >
              重练薄弱（{missed.length}）
            </button>
          )}
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
  const card = current!
  const progressPct = Math.round((index / queue.length) * 100)

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold text-slate-800 dark:text-slate-100">
          复习
          <span className="ml-2 text-xs font-normal tabular-nums text-slate-400">
            {extraQueue ? `重练薄弱 · ${queue.length} 项` : `${recallCount} 知识点 · ${quizCount} 错题`}
          </span>
        </h1>
        <Link to="/plans" className="text-sm text-slate-400 transition-colors hover:text-slate-600 dark:hover:text-slate-200">
          退出
        </Link>
      </div>
      {/* iter 290：模式筛选（全部/仅知识点/仅错题），各 tab 显示对应计数 */}
      <div className="flex flex-wrap items-center gap-1" role="group" aria-label="复习模式筛选">
        {REVIEW_MODES.map((m) => {
          const count = m.value === 'recall' ? recallCount : m.value === 'quiz' ? quizCount : recallCount + quizCount
          return (
            <button
              key={m.value}
              type="button"
              onClick={() => changeMode(m.value)}
              aria-pressed={mode === m.value}
              className={`rounded-lg border px-2.5 py-1 text-xs tabular-nums transition-all active:scale-95 dark:border-slate-600 ${
                mode === m.value
                  ? 'border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300'
                  : 'text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-700'
              }`}
            >
              {m.label}
              {m.value !== 'all' ? ` ${count}` : ''}
            </button>
          )
        })}
      </div>

      {/* 进度 */}
      <div>
        <div className="mb-1 flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
          <span className="tabular-nums">
            {index + 1} / {queue.length}
          </span>
          <span className="tabular-nums">{progressPct}%</span>
        </div>
        <div className="h-2 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
          <div className="h-full rounded-full bg-blue-600 transition-all duration-300" style={{ width: `${progressPct}%` }} />
        </div>
      </div>

      {card.kind === 'recall' ? (
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
                  {card.plan_topic || `方案 #${card.plan_id}`}
                </span>
                {card.must_learn && <span className="text-amber-500" title="必学">★ 必学</span>}
                {card.difficulty != null && (
                  <span className="text-slate-400">{DIFFICULTY_LABEL[card.difficulty] ?? `难度 ${card.difficulty}`}</span>
                )}
              </div>
              <h2 className="text-2xl font-bold text-slate-800 dark:text-slate-100">{card.name}</h2>
              <p className="text-sm text-slate-400">回忆这个知识点的要点，然后翻面自评</p>
              <p className="text-xs text-slate-400">空格 / Enter 翻面</p>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                <span className="font-medium text-slate-700 dark:text-slate-200">{card.name}</span>
                <span className="text-slate-400">当前掌握度 {pct(card.mastery)}</span>
              </div>
              {card.note ? (
                <div className="whitespace-pre-wrap rounded-lg border-l-2 border-blue-300 bg-blue-50/50 p-3 text-sm text-slate-700 dark:border-blue-700 dark:bg-blue-950/40 dark:text-slate-200">
                  {card.note}
                </div>
              ) : (
                <div className="rounded-lg border border-dashed p-3 text-sm text-slate-500 dark:border-slate-600 dark:text-slate-400">
                  这个知识点还没有笔记。你记得它的要点吗？
                  <Link
                    to={`/learn/${card.plan_id}`}
                    onClick={(e) => e.stopPropagation()}
                    className="ml-1 text-blue-600 underline underline-offset-2 dark:text-blue-400"
                  >
                    去学习中心补充
                  </Link>
                </div>
              )}
              <div className="pt-1 text-xs text-slate-400">按下对应的数字键自评（回写掌握度并重排复习日）</div>
            </div>
          )}
        </button>
      ) : (
        // 错题卡片（choice）：选选项 -> 判定 -> 揭示答案/解析 -> 下一题
        <div className="w-full animate-fade-in-up rounded-[var(--dt-card-radius,0.75rem)] border bg-white p-6 text-left shadow-sm dark:border-slate-700 dark:bg-slate-800">
          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-700 dark:bg-amber-950 dark:text-amber-300">错题重练</span>
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600 dark:bg-slate-700 dark:text-slate-300">
              {card.plan_topic || `方案 #${card.plan_id}`}
            </span>
          </div>
          <p className="mb-3 font-medium text-slate-800 dark:text-slate-100">{card.question}</p>
          <div className="space-y-1.5">
            {card.options.map((opt, j) => {
              const isSel = quizSelected === opt
              const showRight = quizJudged && opt === card.answer
              const showWrong = quizJudged && isSel && opt !== card.answer
              return (
                <button
                  key={j}
                  type="button"
                  disabled={quizJudged}
                  onClick={() => setQuizSelected(opt)}
                  className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
                    showRight
                      ? 'border-emerald-300 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950'
                      : showWrong
                        ? 'border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950'
                        : isSel
                          ? 'border-blue-300 bg-blue-50 dark:border-blue-800 dark:bg-blue-950'
                          : 'hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700'
                  } ${quizJudged ? 'cursor-default' : ''}`}
                >
                  {!quizJudged && j < 9 && (
                    <span className="w-4 shrink-0 text-center text-xs tabular-nums text-slate-400 dark:text-slate-500" aria-hidden="true">
                      {j + 1}
                    </span>
                  )}
                  <span className="min-w-0 flex-1">{opt}</span>
                </button>
              )
            })}
          </div>
          <div className="mt-3">
            {!quizJudged ? (
              <button
                type="button"
                onClick={submitQuizCard}
                disabled={!quizSelected || quizJudging}
                className="rounded-lg bg-blue-600 px-4 py-1.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 active:scale-95 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {quizJudging ? '判定中…' : '判定（Enter）'}
              </button>
            ) : (
              <div className="space-y-2">
                <p
                  role="status"
                  className={`text-sm font-medium ${quizCorrect ? 'text-emerald-700 dark:text-emerald-300' : 'text-red-700 dark:text-red-300'}`}
                >
                  {quizCorrect ? '✓ 回答正确' : `✗ 回答错误，正确答案：${card.answer}`}
                </p>
                {card.explanation && <p className="text-sm text-slate-500 dark:text-slate-400">解析：{card.explanation}</p>}
                <button
                  type="button"
                  onClick={advance}
                  className="rounded-lg border px-4 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
                >
                  下一题（Enter）
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* recall 自评按钮（翻面后可用） */}
      {card.kind === 'recall' && (
        <>
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
        </>
      )}
    </div>
  )
}

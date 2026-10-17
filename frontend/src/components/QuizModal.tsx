// 测验弹窗（P2，plan/02 §8：choice 服务器判分（/quizzes/{id}/submit，iter 42）；fill/short 展示参考答案自评）。
// 打开即生成（幂等：已持久化直接返回）；全部选择题判定后按知识点聚合正确率回写进度（POST /progress，iter 40）。
// wrong 模式（iter 114 错题重练）：GET /plans/{id}/quiz/wrong 取最近作答判错的题，零 LLM 成本。
// iter 193 F8：出题反馈改善——loading 加已用秒数+轮换文案+「关闭不浪费结果」说明；
// 生成中关闭（Esc/遮罩/按钮）toast 提示重开可见（服务端幂等持久化，重开即取回结果）。
import { useEffect, useRef, useState } from 'react'
import { toast } from './Toast'
import { ApiError, generateQuiz, getWrongQuizzes, submitQuiz, updateProgress, type QuizItemData } from '../lib/api'
import { useModalEsc } from '../lib/modalEsc'
import { events } from '../lib/plugins'

interface Props {
  planId: number
  onClose: () => void
  wrong?: boolean // 错题重练模式
}

interface AnswerState {
  selected?: string
  judged?: boolean
  judging?: boolean // 判分请求在途（双击防重入，防重复 attempt 落库）
  correct?: boolean
  showAnswer?: boolean
}

const TYPE_LABEL: Record<string, string> = { choice: '单选', fill: '填空', short: '简答' }

export default function QuizModal({ planId, onClose, wrong = false }: Props) {
  const [loading, setLoading] = useState(true)
  const [title, setTitle] = useState('')
  const [quizzes, setQuizzes] = useState<QuizItemData[]>([])
  const [warning, setWarning] = useState('')
  const [error, setError] = useState('')
  const [answers, setAnswers] = useState<Record<number, AnswerState>>({})
  const [writeback, setWriteback] = useState('')
  const acRef = useRef<AbortController | null>(null)
  const writebackRef = useRef(false)
  // iter 355：开放题（fill/short）无自动判分，旧逻辑仅选择题回写 -> 纯开放题测验节点拿不到掌握度。
  // 补「完成自评」入口：用户确认看过/自评后，按知识点标记 done（compute_mastery 手动 done -> 1.0）。
  const [openWriteback, setOpenWriteback] = useState('')
  const openWbRef = useRef(false)
  const openWbBusyRef = useRef(false)

  // iter 193 F8：出题已用秒数（loading 期间每秒走动）
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!loading) return
    setElapsed(0)
    const t = setInterval(() => setElapsed((s) => s + 1), 1000)
    return () => clearInterval(t)
  }, [loading])

  // 生成中（非错题模式）：关闭不浪费结果——服务端幂等持久化，重开即取回
  const generating = loading && !wrong && !error
  function requestClose() {
    if (generating) toast.info('出题在后台继续：若已出完，重新打开测验即可看到结果')
    onClose()
  }

  useEffect(() => {
    const ac = new AbortController()
    acRef.current = ac
    // iter 346（F10 #15）：重开若有缓存先乐观显示——跳过 loading 闪现与出题计时重走，
    // 避免「关闭再开像重新创建」观感（generateQuiz 幂等，POST 静默刷新并更新缓存）。
    const cacheKey = `quiz_cache_${planId}`
    if (!wrong) {
      try {
        const cached = localStorage.getItem(cacheKey)
        if (cached) {
          const c = JSON.parse(cached) as { title: string; quizzes: QuizItemData[] }
          setTitle(c.title)
          setQuizzes(c.quizzes)
          setLoading(false)
        }
      } catch {
        /* 缓存损坏忽略，走正常加载 */
      }
    }
    const fetcher = wrong ? getWrongQuizzes(planId, ac.signal) : generateQuiz(planId, ac.signal)
    fetcher
      .then((r) => {
        if (ac.signal.aborted) return
        setTitle(r.title)
        setQuizzes(r.quizzes)
        setWarning(r.warning ?? '')
        if (!wrong) {
          try {
            localStorage.setItem(cacheKey, JSON.stringify({ title: r.title, quizzes: r.quizzes }))
          } catch {
            /* 存储不可用忽略 */
          }
        }
      })
      .catch((e) => {
        if (ac.signal.aborted) return
        setError(e instanceof ApiError ? e.message : '生成失败')
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false)
      })
    // iter 350：弹窗已开期间收到 quiz_ready（后台预生成完成）-> 即时刷新（命中后端缓存秒回）
    const offReady = events.on('quiz_ready', (p) => {
      const data = p as Record<string, unknown> | undefined
      if (wrong || Number(data?.plan_id) !== planId || ac.signal.aborted) return
      generateQuiz(planId, ac.signal)
        .then((r) => {
          if (ac.signal.aborted) return
          setTitle(r.title)
          setQuizzes(r.quizzes)
          setWarning(r.warning ?? '')
          setLoading(false)
          try {
            localStorage.setItem(cacheKey, JSON.stringify({ title: r.title, quizzes: r.quizzes }))
          } catch {
            /* 存储不可用忽略 */
          }
        })
        .catch(() => {
          /* 后台已就绪但拉取失败：忽略，原 fetcher/端点等待兜底 */
        })
    })
    return () => {
      offReady()
      ac.abort()
    }
  }, [planId, wrong])

  // iter 367（批次六 M10）：quiz 集合更换（quiz_ready 即时刷新/缓存回填/重开取回）时
  // 清空作答——answers 按题目下标键控，旧作答会错位映射到新题（新题显示已判定 +
  // 沿用旧对错）；回写去重标记一并复位（新集合需重新回写掌握度）
  useEffect(() => {
    setAnswers({})
    setWriteback('')
    setOpenWriteback('')
    writebackRef.current = false
    openWbRef.current = false
  }, [quizzes])

  // Esc 关闭（生成中经 requestClose 提示重开可见；iter 368：经全局弹层栈，
  // 仅当本弹层为栈顶时响应——叠加弹层一次 Esc 只关最上层）
  useModalEsc(true, requestClose)

  const choiceIdx = quizzes.map((q, i) => ({ q, i })).filter(({ q }) => q.type === 'choice')
  // iter 355：开放题（无自动判分），「完成自评」入口按其 node_id 回写 done
  const openItems = quizzes.map((q, i) => ({ q, i })).filter(({ q }) => q.type !== 'choice')
  // iter 276：键盘作答——「当前题」= 首个未判定选择题；数字键 1-9 选选项、Enter 判定，
  // 判完自动推进下一题（1→Enter→2→Enter 流畅答完）。文本输入态（fill/short 无输入框，
  // 防御未来）与输入法组合态不劫持；选项前序号角标 + 当前题高亮圈标明显式作用域。
  const currentChoiceIdx = choiceIdx.find(({ i }) => !answers[i]?.judged)?.i
  useEffect(() => {
    if (currentChoiceIdx === undefined) return
    const cur = currentChoiceIdx // 收窄供闭包使用（TS 对跨函数捕获不保留 undefined 收窄）
    function onKey(e: KeyboardEvent) {
      if (e.isComposing) return
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'TEXTAREA' || (t.tagName === 'INPUT' && (t as HTMLInputElement).type === 'text'))) return
      const q = quizzes[cur]
      if (!q || q.type !== 'choice') return
      if (/^[1-9]$/.test(e.key)) {
        const opt = q.options[Number(e.key) - 1]
        if (opt !== undefined) {
          e.preventDefault()
          patch(cur, { selected: opt })
        }
      } else if (e.key === 'Enter') {
        const a = answers[cur]
        if (a?.selected && !a.judged && !a.judging) {
          e.preventDefault()
          void judge(cur, q)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentChoiceIdx, answers, quizzes])

  // 测验回写（iter 40）：全部选择题判定后，按知识点聚合正确率 -> POST /progress。
  // 状态映射：score >= 0.8 -> done（与 record_progress 主观 done 下限 0.8 一致），否则 learning。
  // 无 node_id 的题跳过（无法映射 knowledge_nodes）；单节点失败不阻塞其余（best-effort）。
  useEffect(() => {
    if (writebackRef.current) return
    const choiceItems = quizzes.map((q, i) => ({ q, i })).filter(({ q }) => q.type === 'choice')
    if (choiceItems.length === 0) return
    if (!choiceItems.every(({ i }) => answers[i]?.judged)) return
    writebackRef.current = true

    const byNode = new Map<string, { total: number; correct: number }>()
    for (const { q, i } of choiceItems) {
      if (!q.node_id) continue
      const agg = byNode.get(q.node_id) ?? { total: 0, correct: 0 }
      agg.total += 1
      if (answers[i]?.correct) agg.correct += 1
      byNode.set(q.node_id, agg)
    }
    if (byNode.size === 0) {
      setWriteback('题目未关联知识点，未回写进度')
      return
    }
    let cancelled = false
    void (async () => {
      let ok = 0
      for (const [nodeId, agg] of byNode) {
        const score = agg.correct / agg.total
        try {
          const r = await updateProgress(planId, nodeId, score >= 0.8 ? 'done' : 'learning', score)
          if (r.updated) ok += 1
        } catch {
          // best-effort：单节点失败不阻塞其余
        }
      }
      if (!cancelled) {
        setWriteback(`已回写进度：${ok}/${byNode.size} 个知识点（掌握度按答题正确率更新）`)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [answers, quizzes, planId])

  // iter 355：开放题「完成自评」——纯开放题（无选择题）测验旧逻辑完全不回写进度，
  // 节点掌握度停滞 -> 方案卡 80% 无法完成。用户确认自评后按 node_id 标记 done
  // （score 缺省 -> compute_mastery 手动 done -> 1.0），方案可达 100%。
  async function finishOpenReview() {
    if (openWbRef.current || openWbBusyRef.current) return
    const nodes = Array.from(new Set(openItems.map(({ q }) => q.node_id).filter(Boolean))) as string[]
    if (nodes.length === 0) {
      setOpenWriteback('题目未关联知识点，未回写进度')
      openWbRef.current = true
      return
    }
    openWbBusyRef.current = true
    let ok = 0
    for (const nodeId of nodes) {
      try {
        const r = await updateProgress(planId, nodeId, 'done')
        if (r.updated) ok += 1
      } catch {
        // best-effort：单节点失败不阻塞其余
      }
    }
    openWbRef.current = true
    openWbBusyRef.current = false
    setOpenWriteback(ok > 0 ? `已标记 ${ok}/${nodes.length} 个知识点为已掌握` : '回写失败，请重试')
  }

  function patch(i: number, p: Partial<AnswerState>) {
    setAnswers((prev) => ({ ...prev, [i]: { ...prev[i], ...p } }))
  }

  // 判分：有 DB id 走服务器判分（/quizzes/{id}/submit，记录 attempt）；
  // 失败/无 id 回退本地比对（不阻塞答题，但 attempt 不落库）。
  async function judge(i: number, q: QuizItemData) {
    const a = answers[i]
    if (!a?.selected || a.judged || a.judging) return
    patch(i, { judging: true })
    if (q.id !== undefined) {
      try {
        const r = await submitQuiz(q.id, { selected: a.selected })
        patch(i, { judged: true, judging: false, correct: r.correct === true })
        return
      } catch {
        // 网络/服务器异常 -> 本地判分兜底
      }
    }
    patch(i, { judged: true, judging: false, correct: a.selected === q.answer })
  }

  const judged = choiceIdx.filter(({ i }) => answers[i]?.judged)
  const correctCount = judged.filter(({ i }) => answers[i]?.correct).length

  return (
    <div
      className="fixed inset-0 z-50 flex animate-fade-in items-center justify-center bg-black/40 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) requestClose()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="测验"
        className="flex max-h-[85vh] w-full max-w-3xl animate-scale-in flex-col rounded-xl bg-white dark:bg-slate-800 shadow-xl"
      >
        <div className="flex items-center justify-between gap-2 border-b px-4 py-3">
          <h3 className="font-semibold">{title || '测验'}</h3>
          <div className="flex items-center gap-3">
            {currentChoiceIdx !== undefined && (
              <span className="hidden text-xs text-slate-400 sm:inline dark:text-slate-500" aria-hidden="true">
                键盘：1-9 选项 · Enter 判定
              </span>
            )}
            {judged.length > 0 && (
              <span className="text-sm text-slate-600 dark:text-slate-300" aria-live="polite">
                选择题得分：{correctCount} / {choiceIdx.length}
              </span>
            )}
            {writeback && (
              <span className="text-xs text-emerald-700 dark:text-emerald-400" aria-live="polite">
                {writeback}
              </span>
            )}
            {/* iter 355：开放题「完成自评」——无选择题时这是唯一的进度回写入口 */}
            {openItems.length > 0 && !openWbRef.current && (
              <button
                onClick={() => void finishOpenReview()}
                className="rounded-lg bg-emerald-600 px-2.5 py-1 text-xs text-white transition-all hover:bg-emerald-700 active:scale-95"
                title="确认已自评开放题，按知识点标记为已掌握"
              >
                完成自评
              </button>
            )}
            {openWriteback && (
              <span className="text-xs text-emerald-700 dark:text-emerald-400" aria-live="polite">
                {openWriteback}
              </span>
            )}
            <button
              onClick={requestClose}
              aria-label="关闭"
              className="rounded-lg border px-3 py-1 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
            >
              关闭
            </button>
          </div>
        </div>
        <div className="space-y-4 overflow-y-auto px-6 py-4">
          {loading && (
            <div role="status">
              <p className="flex items-center gap-2 text-slate-500 dark:text-slate-400">
                <span aria-hidden="true" className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600 dark:border-slate-600 dark:border-t-blue-400" />
                {wrong ? '加载错题…' : `出题中…（已用 ${elapsed} 秒）`}
              </p>
              {!wrong && (
                <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-500">
                  {elapsed < 10 ? '正在分析知识点与掌握情况…' : elapsed < 30 ? '正在按知识点组织题目…' : '题目较多，仍在生成中，请稍候…'}
                  出题约需 30–90 秒。关闭弹窗不会浪费结果——若后台已出完，重新打开即可看到。
                </p>
              )}
            </div>
          )}
          {error && (
            <div role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950 p-3 text-sm text-red-700 dark:text-red-300">
              {error}
            </div>
          )}
          {!loading && !error && quizzes.length === 0 && wrong && (
            <div className="animate-fade-in rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-center text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300">
              🎉 暂无错题——全部答对或尚未作答，去完整测验挑战一下吧
            </div>
          )}
          {!loading && !error && (
            <>
              {warning && (
                <div className="animate-fade-in rounded-lg border border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950 p-3 text-sm text-amber-700 dark:text-amber-300">{warning}</div>
              )}
              {quizzes.map((q, i) => {
                const a = answers[i] ?? {}
                return (
                  <div
                    key={i}
                    className={`animate-fade-in-up rounded-lg border p-4 dark:border-slate-700 ${
                      i === currentChoiceIdx ? 'ring-2 ring-blue-400/60 dark:ring-blue-500/50' : ''
                    }`}
                    style={{ animationDelay: `${Math.min(i, 8) * 60}ms` }}
                  >
                    <div className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                      <span>第 {i + 1} 题</span>
                      <span className="rounded bg-slate-100 dark:bg-slate-700 px-1.5 py-0.5">{TYPE_LABEL[q.type] ?? q.type}</span>
                      {q.node_name && <span>知识点：{q.node_name}</span>}
                    </div>
                    <p className="mt-1 font-medium">{q.question}</p>

                    {q.type === 'choice' && (
                      <div className="mt-2 space-y-1">
                        {q.options.map((opt, j) => {
                          const isSel = a.selected === opt
                          const showRight = a.judged && opt === q.answer
                          const showWrong = a.judged && isSel && opt !== q.answer
                          return (
                            <label
                              key={j}
                              className={`flex cursor-pointer items-center gap-2 rounded border px-3 py-2 text-sm transition-colors duration-150 ${
                                showRight
                                  ? 'border-green-300 bg-green-50 dark:bg-green-950'
                                  : showWrong
                                    ? 'border-red-300 bg-red-50 dark:bg-red-950'
                                    : isSel
                                      ? 'border-blue-300 bg-blue-50 dark:bg-blue-950'
                                      : 'hover:bg-slate-50 dark:hover:bg-slate-700'
                              }`}
                            >
                              <input
                                type="radio"
                                name={`q-${i}`}
                                value={opt}
                                checked={isSel}
                                disabled={a.judged}
                                onChange={() => patch(i, { selected: opt })}
                              />
                              {!a.judged && j < 9 && (
                                <span className="w-4 shrink-0 text-center text-xs text-slate-400 tabular-nums dark:text-slate-500" aria-hidden="true">
                                  {j + 1}
                                </span>
                              )}
                              {opt}
                            </label>
                          )
                        })}
                        <div className="pt-1">
                          {!a.judged ? (
                            <button
                              onClick={() => judge(i, q)}
                              disabled={!a.selected || a.judging}
                              className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98] disabled:opacity-50 disabled:hover:shadow-sm disabled:active:scale-100"
                            >
                              {a.judging ? '判定中…' : '判定'}
                            </button>
                          ) : (
                            <p className={`text-sm ${a.correct ? 'text-green-700 dark:text-green-300' : 'text-red-700 dark:text-red-300'}`} role="status">
                              {a.correct ? '✓ 回答正确' : `✗ 回答错误，正确答案：${q.answer}`}
                            </p>
                          )}
                        </div>
                      </div>
                    )}

                    {q.type !== 'choice' && (
                      <div className="mt-2">
                        {q.answer ? (
                          <>
                            <button
                              onClick={() => patch(i, { showAnswer: !a.showAnswer })}
                              className="rounded-lg border px-3 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
                              aria-expanded={!!a.showAnswer}
                            >
                              {a.showAnswer ? '隐藏参考答案' : '显示参考答案'}
                            </button>
                            {a.showAnswer && (
                              <p className="mt-2 animate-fade-in rounded bg-slate-50 dark:bg-slate-800 p-2 text-sm text-slate-700 dark:text-slate-200">{q.answer}</p>
                            )}
                          </>
                        ) : (
                          <p className="text-sm text-slate-400">（开放自评题，无参考答案）</p>
                        )}
                      </div>
                    )}

                    {a.judged && q.explanation && (
                      <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">解析：{q.explanation}</p>
                    )}
                  </div>
                )
              })}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// 测验弹窗（P2，plan/02 §8：choice 服务器判分（/quizzes/{id}/submit，iter 42）；fill/short 展示参考答案自评）。
// 打开即生成（幂等：已持久化直接返回）；全部选择题判定后按知识点聚合正确率回写进度（POST /progress，iter 40）。
import { useEffect, useRef, useState } from 'react'
import { ApiError, generateQuiz, submitQuiz, updateProgress, type QuizItemData } from '../lib/api'

interface Props {
  planId: number
  onClose: () => void
}

interface AnswerState {
  selected?: string
  judged?: boolean
  correct?: boolean
  showAnswer?: boolean
}

const TYPE_LABEL: Record<string, string> = { choice: '单选', fill: '填空', short: '简答' }

export default function QuizModal({ planId, onClose }: Props) {
  const [loading, setLoading] = useState(true)
  const [title, setTitle] = useState('')
  const [quizzes, setQuizzes] = useState<QuizItemData[]>([])
  const [warning, setWarning] = useState('')
  const [error, setError] = useState('')
  const [answers, setAnswers] = useState<Record<number, AnswerState>>({})
  const [writeback, setWriteback] = useState('')
  const acRef = useRef<AbortController | null>(null)
  const writebackRef = useRef(false)

  useEffect(() => {
    const ac = new AbortController()
    acRef.current = ac
    generateQuiz(planId, ac.signal)
      .then((r) => {
        if (ac.signal.aborted) return
        setTitle(r.title)
        setQuizzes(r.quizzes)
        setWarning(r.warning ?? '')
      })
      .catch((e) => {
        if (ac.signal.aborted) return
        setError(e instanceof ApiError ? e.message : '生成失败')
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false)
      })
    return () => ac.abort()
  }, [planId])

  // Esc 关闭
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

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

  function patch(i: number, p: Partial<AnswerState>) {
    setAnswers((prev) => ({ ...prev, [i]: { ...prev[i], ...p } }))
  }

  // 判分：有 DB id 走服务器判分（/quizzes/{id}/submit，记录 attempt）；
  // 失败/无 id 回退本地比对（不阻塞答题，但 attempt 不落库）。
  async function judge(i: number, q: QuizItemData) {
    const a = answers[i]
    if (!a?.selected || a.judged) return
    if (q.id !== undefined) {
      try {
        const r = await submitQuiz(q.id, { selected: a.selected })
        patch(i, { judged: true, correct: r.correct === true })
        return
      } catch {
        // 网络/服务器异常 -> 本地判分兜底
      }
    }
    patch(i, { judged: true, correct: a.selected === q.answer })
  }

  const choiceIdx = quizzes.map((q, i) => ({ q, i })).filter(({ q }) => q.type === 'choice')
  const judged = choiceIdx.filter(({ i }) => answers[i]?.judged)
  const correctCount = judged.filter(({ i }) => answers[i]?.correct).length

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="测验"
        className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl bg-white dark:bg-slate-800 shadow-xl"
      >
        <div className="flex items-center justify-between gap-2 border-b px-4 py-3">
          <h3 className="font-semibold">{title || '测验'}</h3>
          <div className="flex items-center gap-3">
            {judged.length > 0 && (
              <span className="text-sm text-slate-600 dark:text-slate-300" aria-live="polite">
                选择题得分：{correctCount} / {choiceIdx.length}
              </span>
            )}
            {writeback && (
              <span className="text-xs text-emerald-700" aria-live="polite">
                {writeback}
              </span>
            )}
            <button
              onClick={onClose}
              aria-label="关闭"
              className="rounded-lg border px-3 py-1 text-sm hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              关闭
            </button>
          </div>
        </div>
        <div className="space-y-4 overflow-y-auto px-6 py-4">
          {loading && <p className="text-slate-500 dark:text-slate-400">出题中…（按知识点组织题目，约需数十秒）</p>}
          {error && (
            <div role="alert" className="rounded-lg bg-red-50 dark:bg-red-950 p-3 text-sm text-red-700 dark:text-red-300">
              {error}
            </div>
          )}
          {!loading && !error && (
            <>
              {warning && (
                <div className="rounded-lg bg-amber-50 dark:bg-amber-950 p-3 text-sm text-amber-700">{warning}</div>
              )}
              {quizzes.map((q, i) => {
                const a = answers[i] ?? {}
                return (
                  <div key={i} className="rounded-lg border p-4">
                    <div className="flex items-center gap-2 text-xs text-slate-400">
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
                              className={`flex items-center gap-2 rounded border px-3 py-2 text-sm ${
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
                              {opt}
                            </label>
                          )
                        })}
                        <div className="pt-1">
                          {!a.judged ? (
                            <button
                              onClick={() => judge(i, q)}
                              disabled={!a.selected}
                              className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
                            >
                              判定
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
                              className="rounded-lg border px-3 py-1.5 text-sm hover:bg-slate-50 dark:hover:bg-slate-700"
                              aria-expanded={!!a.showAnswer}
                            >
                              {a.showAnswer ? '隐藏参考答案' : '显示参考答案'}
                            </button>
                            {a.showAnswer && (
                              <p className="mt-2 rounded bg-slate-50 dark:bg-slate-800 p-2 text-sm text-slate-700 dark:text-slate-200">{q.answer}</p>
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

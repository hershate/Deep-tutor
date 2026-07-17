// 测验弹窗（P2，plan/02 §8 简化版：choice 即时判分；fill/short 展示参考答案自评）。
// 打开即生成；进度回写在 iter 40（record_progress 端点落地后）接入。
import { useEffect, useRef, useState } from 'react'
import { ApiError, generateQuiz, type QuizItemData } from '../lib/api'

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
  const acRef = useRef<AbortController | null>(null)

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

  function patch(i: number, p: Partial<AnswerState>) {
    setAnswers((prev) => ({ ...prev, [i]: { ...prev[i], ...p } }))
  }

  function judge(i: number, q: QuizItemData) {
    const a = answers[i]
    if (!a?.selected || a.judged) return
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
        className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl bg-white shadow-xl"
      >
        <div className="flex items-center justify-between gap-2 border-b px-4 py-3">
          <h3 className="font-semibold">{title || '测验'}</h3>
          <div className="flex items-center gap-3">
            {judged.length > 0 && (
              <span className="text-sm text-slate-600" aria-live="polite">
                选择题得分：{correctCount} / {choiceIdx.length}
              </span>
            )}
            <button
              onClick={onClose}
              aria-label="关闭"
              className="rounded-lg border px-3 py-1 text-sm hover:bg-slate-50"
            >
              关闭
            </button>
          </div>
        </div>
        <div className="space-y-4 overflow-y-auto px-6 py-4">
          {loading && <p className="text-slate-500">出题中…（按知识点组织题目，约需数十秒）</p>}
          {error && (
            <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
              {error}
            </div>
          )}
          {!loading && !error && (
            <>
              {warning && (
                <div className="rounded-lg bg-amber-50 p-3 text-sm text-amber-700">{warning}</div>
              )}
              {quizzes.map((q, i) => {
                const a = answers[i] ?? {}
                return (
                  <div key={i} className="rounded-lg border p-4">
                    <div className="flex items-center gap-2 text-xs text-slate-400">
                      <span>第 {i + 1} 题</span>
                      <span className="rounded bg-slate-100 px-1.5 py-0.5">{TYPE_LABEL[q.type] ?? q.type}</span>
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
                                  ? 'border-green-300 bg-green-50'
                                  : showWrong
                                    ? 'border-red-300 bg-red-50'
                                    : isSel
                                      ? 'border-blue-300 bg-blue-50'
                                      : 'hover:bg-slate-50'
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
                            <p className={`text-sm ${a.correct ? 'text-green-700' : 'text-red-700'}`} role="status">
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
                              className="rounded-lg border px-3 py-1.5 text-sm hover:bg-slate-50"
                              aria-expanded={!!a.showAnswer}
                            >
                              {a.showAnswer ? '隐藏参考答案' : '显示参考答案'}
                            </button>
                            {a.showAnswer && (
                              <p className="mt-2 rounded bg-slate-50 p-2 text-sm text-slate-700">{q.answer}</p>
                            )}
                          </>
                        ) : (
                          <p className="text-sm text-slate-400">（开放自评题，无参考答案）</p>
                        )}
                      </div>
                    )}

                    {a.judged && q.explanation && (
                      <p className="mt-2 text-sm text-slate-500">解析：{q.explanation}</p>
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

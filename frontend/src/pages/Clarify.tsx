import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { clarifyStream } from '../lib/api'

export default function Clarify() {
  const { sessionId } = useParams()
  const loc = useLocation()
  const questions = (loc.state as { questions?: string[] } | null)?.questions ?? []
  const nav = useNavigate()
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const terminalRef = useRef(false)
  const acRef = useRef<AbortController | null>(null)

  // 卸载时取消进行中的 clarify 流（与 GenerateProgress/PlanDetail 一致）
  useEffect(() => () => acRef.current?.abort(), [])

  function submit() {
    if (!sessionId) return
    // 过滤空回答
    const filtered = Object.fromEntries(Object.entries(answers).filter(([, v]) => v.trim()))
    // err.md D14：全空提交等于放弃澄清却走完整生成——拦下提示
    if (Object.keys(filtered).length === 0) {
      setError('请至少回答一个问题（或返回首页重新开始）')
      return
    }
    setLoading(true)
    setError('')
    terminalRef.current = false
    const ac = new AbortController()
    acRef.current = ac
    clarifyStream(
      Number(sessionId),
      filtered,
      (event, data) => {
        if (event === 'done') {
          terminalRef.current = true
          nav(`/plans/${data.plan_id}`)
        } else if (event === 'error') {
          terminalRef.current = true
          setError(String(data.message ?? '续接失败'))
          setLoading(false)
        }
      },
      ac.signal,
    )
      .then(() => {
        if (ac.signal.aborted) return // 主动取消/卸载，不计错误
        // 流结束但未收到终态事件 -> 静默断连，提示重试
        if (!terminalRef.current) {
          setError('连接中断，请重试')
          setLoading(false)
        }
      })
      .catch((e: unknown) => {
        if (ac.signal.aborted) return
        setError(e instanceof Error ? e.message : '连接失败')
        setLoading(false)
      })
  }

  // err.md D14：直接访问 /clarify/:id（无 questions state）此前渲染空表单，
  // 用户只能提交空回答触发一轮无意义生成——显式给出口
  if (questions.length === 0)
    return (
      <div className="space-y-4">
        <h2 className="text-2xl font-bold">补充信息</h2>
        <p className="text-slate-600 dark:text-slate-300">
          没有待回答的澄清问题（可能已回答过或会话状态已变化）。
        </p>
        <button
          onClick={() => nav('/')}
          className="rounded-lg bg-blue-600 px-6 py-2.5 font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98]"
        >
          返回首页
        </button>
      </div>
    )

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">补充信息</h2>
      <p className="text-slate-600 dark:text-slate-300">为了生成更贴合的学习方案，请回答以下问题：</p>

      <div className="space-y-3">
        {questions.map((q, i) => {
          const inputId = `clarify-q-${i}`
          return (
            // iter 100 美化：问题卡片化 + 错峰入场
            <div
              key={i}
              className="animate-fade-in-up space-y-1 rounded-xl border bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800"
              style={{ animationDelay: `${Math.min(i, 6) * 70}ms` }}
            >
              <label htmlFor={inputId} className="text-sm font-medium">
                {q}
              </label>
              <input
                id={inputId}
                className="w-full rounded-lg border border-slate-300 p-2 transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-900"
                value={answers[q] ?? ''}
                onChange={(e) => setAnswers((a) => ({ ...a, [q]: e.target.value }))}
                placeholder="你的回答…"
                maxLength={2000} // err.md D14
              />
            </div>
          )
        })}
      </div>

      {error && (
        <div role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950 p-3 text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      <button
        onClick={submit}
        disabled={loading}
        className="rounded-lg bg-blue-600 px-6 py-2.5 font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98] disabled:opacity-50 disabled:hover:shadow-sm disabled:active:scale-100"
      >
        {loading ? '生成中…' : '提交并继续生成'}
      </button>
    </div>
  )
}

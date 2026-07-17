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
    setLoading(true)
    setError('')
    terminalRef.current = false
    // 过滤空回答
    const filtered = Object.fromEntries(Object.entries(answers).filter(([, v]) => v.trim()))
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

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">补充信息</h2>
      <p className="text-slate-600">为了生成更贴合的学习方案，请回答以下问题：</p>

      <div className="space-y-3">
        {questions.map((q, i) => {
          const inputId = `clarify-q-${i}`
          return (
            <div key={i} className="space-y-1">
              <label htmlFor={inputId} className="text-sm font-medium">
                {q}
              </label>
              <input
                id={inputId}
                className="w-full rounded-lg border border-slate-300 p-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
                value={answers[q] ?? ''}
                onChange={(e) => setAnswers((a) => ({ ...a, [q]: e.target.value }))}
                placeholder="你的回答…"
              />
            </div>
          )
        })}
      </div>

      {error && (
        <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <button
        onClick={submit}
        disabled={loading}
        className="rounded-lg bg-blue-600 px-6 py-2.5 font-medium text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? '生成中…' : '提交并继续生成'}
      </button>
    </div>
  )
}

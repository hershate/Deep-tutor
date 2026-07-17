import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { generateStream } from '../lib/api'

interface Step {
  name: string
  status: 'running' | 'done' | 'error' | ''
  meta?: Record<string, unknown>
}

export default function GenerateProgress() {
  const { sessionId } = useParams()
  const loc = useLocation()
  const rawText = (loc.state as { rawText?: string } | null)?.rawText ?? ''
  const nav = useNavigate()
  const qc = useQueryClient()
  const [steps, setSteps] = useState<Step[]>([])
  const [status, setStatus] = useState<'running' | 'done' | 'error'>('running')
  const [error, setError] = useState('')
  const [cost, setCost] = useState(0)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!sessionId) return
    const ac = new AbortController()
    abortRef.current = ac
    setSteps([])
    setStatus('running')

    generateStream(
      Number(sessionId),
      rawText,
      (event, data) => {
        if (event === 'tool_start') {
          setSteps((s) => [...s, { name: String(data.tool), status: 'running' }])
        } else if (event === 'tool_end') {
          setSteps((s) =>
            s.map((st) =>
              st.name === data.tool && st.status === 'running'
                ? { ...st, status: (data.status as Step['status']) || 'done', meta: data.metadata as Record<string, unknown> }
                : st,
            ),
          )
          const c = (data.metadata as { cost?: number })?.cost
          if (typeof c === 'number') setCost((x) => x + c)
        } else if (event === 'clarify_needed') {
          nav(`/clarify/${sessionId}`, { state: { questions: data.questions } })
        } else if (event === 'done') {
          setStatus('done')
          nav(`/plans/${data.plan_id}`)
        } else if (event === 'error') {
          setStatus('error')
          setError(String(data.message ?? '生成失败'))
        } else if (event === 'budget_exceeded') {
          setStatus('error')
          setError('预算超限，生成中断')
        }
      },
      ac.signal,
    )
      .then(() => {
        // 生成结束（非取消）后刷新用量（api_usage 已在 done-callback 落库）
        if (!ac.signal.aborted) qc.invalidateQueries({ queryKey: ['usage'] })
      })
      .catch((e: unknown) => {
        // generateStream 内部已指数退避重连≤3；此处仅捕获重连耗尽/4xx 等终态失败
        if (ac.signal.aborted) return // 主动取消/组件卸载，不计错误
        setStatus('error')
        setError(e instanceof Error ? e.message : '连接失败')
      })

    return () => ac.abort()
  }, [sessionId, rawText, nav])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">生成中…</h2>
        <span className="text-sm text-slate-500">费用 ${cost.toFixed(4)}</span>
      </div>
      <p className="text-sm text-slate-500">会话 #{sessionId}</p>

      <ul className="space-y-2">
        {steps.map((st, i) => (
          <li key={i} className="flex items-center gap-3 rounded-lg border bg-white p-3">
            <span className="w-5 text-center">
              <span aria-hidden="true">
                {st.status === 'running' ? '⏳' : st.status === 'error' ? '❌' : '✅'}
              </span>
              <span className="sr-only">
                {st.status === 'running' ? '进行中' : st.status === 'error' ? '失败' : '完成'}
              </span>
            </span>
            <span className="font-medium">{st.name}</span>
            {st.meta && (
              <span className="text-xs text-slate-400">
                {(st.meta as { tokens_in?: number }).tokens_in ?? 0} in / {(st.meta as { tokens_out?: number }).tokens_out ?? 0} out
              </span>
            )}
          </li>
        ))}
      </ul>

      {status === 'error' && (
        <div role="alert" className="rounded-lg bg-red-50 p-4 text-red-700">
          <p className="font-medium">生成失败</p>
          <p className="text-sm">{error}</p>
          <button onClick={() => nav('/')} className="mt-2 text-sm underline">
            返回首页
          </button>
        </div>
      )}
    </div>
  )
}

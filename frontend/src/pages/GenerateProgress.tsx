import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import PlanOutline, { type OutlineNode, type OutlineStage } from '../components/PlanOutline'
import Timeline, { type ScheduleDay } from '../components/Timeline'
import { toast } from '../components/Toast'
import { cancelGeneration, generateStream } from '../lib/api'
import { isHttpUrl } from '../lib/url'

interface Step {
  name: string
  status: 'running' | 'done' | 'error' | ''
  meta?: Record<string, unknown>
}

// plan_chunk 部分方案（plan/03 §一）：与 PlanDetail 的 PlanData 同构（内容可能尚不完整）
interface PartialPlan {
  intent?: { topic?: string }
  stages?: OutlineStage[]
  knowledge_map?: { nodes?: OutlineNode[] }
  schedule?: ScheduleDay[]
  resources?: Array<{ title?: string; url?: string; verified?: string }>
}

export default function GenerateProgress() {
  const { sessionId } = useParams()
  const loc = useLocation()
  const rawText = (loc.state as { rawText?: string } | null)?.rawText ?? ''
  const nav = useNavigate()
  const qc = useQueryClient()
  const [steps, setSteps] = useState<Step[]>([])
  const [status, setStatus] = useState<'running' | 'done' | 'error' | 'cancelled'>('running')
  const [error, setError] = useState('')
  const [cost, setCost] = useState(0)
  const [partial, setPartial] = useState<PartialPlan | null>(null) // 最新 plan_chunk（查看部分）
  const [showPartial, setShowPartial] = useState(false)
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
        } else if (event === 'plan_chunk') {
          // 每工具产出后广播的部分方案（plan/03 §一）：仅保留最新一份供「查看部分」
          setPartial(data.partial_plan as PartialPlan)
        } else if (event === 'clarify_needed') {
          nav(`/clarify/${sessionId}`, { state: { questions: data.questions } })
        } else if (event === 'done') {
          setStatus('done')
          nav(`/plans/${data.plan_id}`)
        } else if (event === 'error') {
          if (data.code === 'cancelled') {
            setStatus('cancelled')
            setError('')
          } else {
            setStatus('error')
            setError(String(data.message ?? '生成失败'))
          }
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
        <div className="flex items-center gap-3">
          <h2 className="text-2xl font-bold">生成中…</h2>
          {status === 'running' && (
            <button
              onClick={() =>
                // err.md D13：取消请求失败此前静默吞掉，用户以为已取消实际仍在跑
                cancelGeneration(Number(sessionId)).catch(() =>
                  toast.error('取消请求失败，请重试（生成仍在进行）'),
                )
              }
              className="rounded-lg border px-3 py-1 text-sm text-slate-600 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              取消
            </button>
          )}
          {partial && (
            <button
              onClick={() => setShowPartial(true)}
              className="rounded-lg border px-3 py-1 text-sm text-blue-600 hover:bg-blue-50 dark:border-slate-600 dark:text-blue-400 dark:hover:bg-slate-700"
            >
              查看部分 →
            </button>
          )}
        </div>
        <span className="text-sm text-slate-500 dark:text-slate-400">费用 ${cost.toFixed(4)}</span>
      </div>
      <p className="text-sm text-slate-500 dark:text-slate-400">会话 #{sessionId}</p>

      <ul className="space-y-2">
        {steps.map((st, i) => (
          <li key={i} className="flex items-center gap-3 rounded-lg border bg-white dark:border-slate-700 dark:bg-slate-800 p-3">
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
        <div role="alert" className="rounded-lg bg-red-50 dark:bg-red-950 p-4 text-red-700 dark:text-red-300">
          <p className="font-medium">生成失败</p>
          <p className="text-sm">{error}</p>
          <button onClick={() => nav('/')} className="mt-2 text-sm underline">
            返回首页
          </button>
        </div>
      )}

      {status === 'cancelled' && (
        <div className="rounded-lg bg-slate-100 dark:bg-slate-700 p-4 text-slate-700 dark:text-slate-200">
          <p className="font-medium">已取消</p>
          <button onClick={() => nav('/')} className="mt-2 text-sm underline">
            返回首页
          </button>
        </div>
      )}

      {showPartial && partial && (
        <div
          className="fixed inset-0 z-50 flex animate-fade-in items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="部分方案预览"
          onClick={() => setShowPartial(false)}
        >
          <div
            className="max-h-[85vh] w-full max-w-3xl animate-scale-in overflow-y-auto rounded-xl bg-white p-5 shadow-xl dark:bg-slate-800"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-lg font-semibold">
                部分方案{partial.intent?.topic ? `：${partial.intent.topic}` : ''}
                {status === 'running' && <span className="ml-2 text-xs font-normal text-slate-400">（生成中，实时更新）</span>}
              </h3>
              <button
                onClick={() => setShowPartial(false)}
                aria-label="关闭预览"
                className="rounded px-2 py-1 text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-700"
              >
                ✕
              </button>
            </div>
            <div className="space-y-5">
              <PlanOutline stages={partial.stages} knowledgeMap={partial.knowledge_map} />
              {partial.schedule && partial.schedule.length > 0 && (
                <section aria-label="已产出日程">
                  <h4 className="mb-2 font-semibold">日程（已产出）</h4>
                  <Timeline schedule={partial.schedule} stageNames={(partial.stages ?? []).map((s) => s.name)} />
                </section>
              )}
              {partial.resources && partial.resources.length > 0 && (
                <section aria-label="已产出资源">
                  <h4 className="mb-2 font-semibold">资源（已产出 {partial.resources.length} 条）</h4>
                  <ul className="space-y-1 text-sm">
                    {partial.resources.map((r, i) => (
                      <li key={i}>
                        {r.verified === 'ok' ? '✅' : r.verified === 'invalid' ? '❌' : '⚠️'} {r.title}{' '}
                        {isHttpUrl(r.url) ? (
                          <a href={r.url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline dark:text-blue-400">
                            {r.url}
                          </a>
                        ) : (
                          <span className="text-slate-500 dark:text-slate-400">{r.url}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

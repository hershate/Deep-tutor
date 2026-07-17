import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { ApiError, exportPlan, getPlan } from '../lib/api'

interface PlanData {
  intent?: { topic?: string; level?: string; goal?: string; daily_minutes?: number }
  stages?: Array<{
    index: number
    name: string
    goal: string
    difficulty: number
    estimated_weeks: number
    topics?: Array<{ name: string; why?: string; must_learn?: boolean }>
    milestone?: string
  }>
  resources?: Array<{ title?: string; url?: string; type?: string; verified?: string; note?: string }>
  validation_report?: { passed?: boolean; report?: Record<string, { status?: string; details?: string }>; fixes?: string[] }
}

const VERIFY_LABEL: Record<string, string> = { ok: '✅', uncertain: '⚠️', invalid: '❌' }
const VERIFY_TEXT: Record<string, string> = { ok: '已验证', uncertain: '不确定', invalid: '无效' }

export default function PlanDetail() {
  const { planId } = useParams()
  const [plan, setPlan] = useState<PlanData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [exportUrl, setExportUrl] = useState('')

  useEffect(() => {
    if (!planId) return
    getPlan(Number(planId))
      .then((r) => setPlan((r as { plan: PlanData }).plan))
      .catch((e) => setError(e instanceof ApiError ? e.message : '加载失败'))
      .finally(() => setLoading(false))
  }, [planId])

  async function doExport() {
    if (!planId) return
    try {
      const r = await exportPlan(Number(planId), 'md')
      setExportUrl(r.file_url)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '导出失败')
    }
  }

  if (loading) return <p className="text-slate-500">加载方案…</p>
  if (error)
    return (
      <div role="alert" className="rounded-lg bg-red-50 p-3 text-red-700">
        {error}
      </div>
    )
  if (!plan) return <p className="text-slate-500">无方案数据</p>

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">{plan.intent?.topic ?? '学习方案'}</h2>
        <button onClick={doExport} className="rounded-lg border px-4 py-2 text-sm hover:bg-slate-50">
          导出 Markdown
        </button>
      </div>
      {exportUrl && <div className="text-xs text-slate-500">已导出: {exportUrl}</div>}

      {plan.intent && (
        <section className="rounded-lg border bg-white p-4">
          <h3 className="mb-2 font-semibold">学习意图</h3>
          <ul className="text-sm text-slate-600">
            {plan.intent.level && <li>基础: {plan.intent.level}</li>}
            {plan.intent.goal && <li>目标: {plan.intent.goal}</li>}
            {plan.intent.daily_minutes && <li>每日: {plan.intent.daily_minutes} 分钟</li>}
          </ul>
        </section>
      )}

      {plan.stages && (
        <section className="space-y-3">
          <h3 className="font-semibold">学习阶段</h3>
          {plan.stages.map((s) => (
            <div key={s.index} className="rounded-lg border bg-white p-4">
              <div className="flex items-center justify-between">
                <h4 className="font-medium">{s.index + 1}. {s.name}</h4>
                <span className="text-xs text-slate-400">难度 {s.difficulty} / {s.estimated_weeks} 周</span>
              </div>
              <p className="mt-1 text-sm text-slate-600">目标: {s.goal}</p>
              {s.topics && s.topics.length > 0 && (
                <ul className="mt-2 text-sm">
                  {s.topics.map((t, i) => (
                    <li key={i}>{t.must_learn ? '★ ' : '· '}{t.name}</li>
                  ))}
                </ul>
              )}
              {s.milestone && <p className="mt-2 text-sm text-slate-500">里程碑: {s.milestone}</p>}
            </div>
          ))}
        </section>
      )}

      {plan.resources && plan.resources.length > 0 && (
        <section className="rounded-lg border bg-white p-4">
          <h3 className="mb-2 font-semibold">配套资源</h3>
          <ul className="space-y-1 text-sm">
            {plan.resources.map((r, i) => (
              <li key={i}>
                <span aria-hidden="true">{VERIFY_LABEL[r.verified ?? ''] ?? '·'}</span>{' '}
                <span className="sr-only">{VERIFY_TEXT[r.verified ?? ''] ?? '未知'}：</span>
                {r.title}{' '}
                <a href={r.url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
                  {r.url}
                </a>
              </li>
            ))}
          </ul>
        </section>
      )}

      {plan.validation_report && (
        <section className="rounded-lg border bg-white p-4">
          <h3 className="mb-2 font-semibold">校验报告: {plan.validation_report.passed ? '通过 ✅' : '未通过 ⚠️'}</h3>
          {plan.validation_report.report && (
            <ul className="text-sm">
              {Object.entries(plan.validation_report.report).map(([k, v]) => (
                <li key={k}>{k}: {v.status} {v.details}</li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  )
}

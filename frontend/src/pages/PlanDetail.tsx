import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ApiError, exportPlan, getPlan, regenerateStream } from '../lib/api'

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
  const nav = useNavigate()
  const [plan, setPlan] = useState<PlanData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [exportUrl, setExportUrl] = useState('')
  const [regenScope, setRegenScope] = useState('design_stages')
  const [regenFeedback, setRegenFeedback] = useState('')
  const [regenLoading, setRegenLoading] = useState(false)
  const [regenError, setRegenError] = useState('')
  const regenAcRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!planId) return
    getPlan(Number(planId))
      .then((r) => setPlan((r as { plan: PlanData }).plan))
      .catch((e) => setError(e instanceof ApiError ? e.message : '加载失败'))
      .finally(() => setLoading(false))
  }, [planId])

  // 卸载时取消进行中的重生成流
  useEffect(() => () => regenAcRef.current?.abort(), [])

  async function doExport() {
    if (!planId) return
    try {
      const r = await exportPlan(Number(planId), 'md')
      setExportUrl(r.file_url)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '导出失败')
    }
  }

  function doRegenerate() {
    if (!planId) return
    setRegenError('')
    setRegenLoading(true)
    const ac = new AbortController()
    regenAcRef.current = ac
    regenerateStream(Number(planId), regenScope, regenFeedback, (event, data) => {
      if (event === 'done') {
        nav(`/plans/${data.plan_id}`)
      } else if (event === 'error') {
        setRegenError(String(data.message ?? '重生成失败'))
        setRegenLoading(false)
      } else if (event === 'budget_exceeded') {
        setRegenError('预算超限，重生成中断')
        setRegenLoading(false)
      }
    }, ac.signal).catch((e: unknown) => {
      if (ac.signal.aborted) return
      setRegenError(e instanceof Error ? e.message : '连接失败')
      setRegenLoading(false)
    })
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

      <section className="rounded-lg border bg-white p-4 space-y-3" aria-label="重生成方案">
        <h3 className="font-semibold">重生成</h3>
        <p className="text-sm text-slate-500">从选定工具起级联重跑其下游，生成新版本方案（plan.version+1）。</p>
        <div className="flex items-center gap-2">
          <label htmlFor="regen-scope" className="text-sm text-slate-600">
            起点
          </label>
          <select
            id="regen-scope"
            value={regenScope}
            onChange={(e) => setRegenScope(e.target.value)}
            className="border rounded p-1.5 text-sm"
            disabled={regenLoading}
          >
            <option value="design_stages">阶段设计</option>
            <option value="search_resources">资源</option>
            <option value="schedule_plan">排期</option>
            <option value="validate_plan">校验</option>
          </select>
        </div>
        <div className="space-y-1">
          <label htmlFor="regen-feedback" className="block text-sm text-slate-600">
            反馈（可选）
          </label>
          <textarea
            id="regen-feedback"
            className="w-full rounded border border-slate-300 p-2 text-sm min-h-[60px] focus:outline-none focus:ring-2 focus:ring-blue-500"
            placeholder="如：简化阶段、增加资源、调整难度梯度"
            value={regenFeedback}
            onChange={(e) => setRegenFeedback(e.target.value)}
            disabled={regenLoading}
          />
        </div>
        <button
          onClick={doRegenerate}
          disabled={regenLoading}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {regenLoading ? '重生成中…' : '重生成'}
        </button>
        {regenError && (
          <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
            {regenError}
          </div>
        )}
      </section>
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import MaterialsModal from '../components/MaterialsModal'
import ProgressBoard from '../components/ProgressBoard'
import QuizModal from '../components/QuizModal'
import { ApiError, createShare, exportPlan, getPlan, getShare, regenerateStream, revokeShare, type ShareStatus } from '../lib/api'

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

/** 触发浏览器下载（P5 ics/Anki 导出：内容随 API 响应返回，本地生成 Blob 保存）。 */
function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

export default function PlanDetail() {
  const { planId } = useParams()
  const nav = useNavigate()
  const [plan, setPlan] = useState<PlanData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [exportUrl, setExportUrl] = useState('')
  const [exportFormat, setExportFormat] = useState<'md' | 'slides' | 'ics' | 'anki'>('md')
  const [showMaterials, setShowMaterials] = useState(false)
  const [showQuiz, setShowQuiz] = useState(false)
  const [share, setShare] = useState<ShareStatus | null>(null)
  const [shareMsg, setShareMsg] = useState('')
  const [progressKey, setProgressKey] = useState(0) // 测验回写后刷新进度看板
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
    getShare(Number(planId))
      .then(setShare)
      .catch(() => setShare(null))
  }, [planId])

  // 协作分享（P5）：创建（幂等）/复制/撤销只读链接
  async function doShare() {
    if (!planId) return
    try {
      const r = await createShare(Number(planId))
      const url = `${window.location.origin}${r.path}`
      setShare({ shared: true, token: r.token, path: r.path })
      try {
        await navigator.clipboard.writeText(url)
        setShareMsg('链接已复制')
      } catch {
        setShareMsg(url) // 剪贴板不可用（非安全上下文）则直接展示
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '创建分享失败')
    }
  }

  async function doRevokeShare() {
    if (!planId) return
    try {
      await revokeShare(Number(planId))
      setShare({ shared: false })
      setShareMsg('已撤销，原链接即刻失效')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '撤销失败')
    }
  }

  // 卸载时取消进行中的重生成流
  useEffect(() => () => regenAcRef.current?.abort(), [])

  async function doExport() {
    if (!planId) return
    try {
      const r = await exportPlan(Number(planId), exportFormat)
      if (exportFormat === 'slides' && r.html) {
        // 自包含 HTML：Blob URL 新窗口打开（plan/03 §93 键盘/滚轮/点击翻页）
        const url = URL.createObjectURL(new Blob([r.html], { type: 'text/html' }))
        window.open(url)
        setTimeout(() => URL.revokeObjectURL(url), 60_000)
        setExportUrl('')
      } else if (exportFormat === 'ics' && r.ics) {
        // P5 日历：Blob 下载 .ics（双击导入系统日历）
        downloadBlob(new Blob([r.ics], { type: 'text/calendar' }), r.filename || 'plan.ics')
        setExportUrl(`已下载 ${r.filename}（${r.event_count ?? 0} 个学习日）`)
      } else if (exportFormat === 'anki' && r.apkg_b64) {
        // P5 Anki：base64 -> Blob 下载 .apkg（双击导入 Anki）
        const bytes = Uint8Array.from(atob(r.apkg_b64), (c) => c.charCodeAt(0))
        downloadBlob(new Blob([bytes], { type: 'application/octet-stream' }), r.filename || 'plan.apkg')
        setExportUrl(`已下载 ${r.filename}（${r.card_count ?? 0} 张卡片）`)
      } else if (exportFormat === 'md' && r.content) {
        // UX（iter 73）：Markdown 也走浏览器下载（原仅显示服务器文件路径，浏览器无法访问）
        downloadBlob(new Blob([r.content], { type: 'text/markdown;charset=utf-8' }), r.filename || 'plan.md')
        setExportUrl(`已下载 ${r.filename || 'plan.md'}`)
      } else {
        setExportUrl(r.file_url)
      }
      if (r.warning) setError(`导出警告：${r.warning}`)
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
        <div className="flex items-center gap-2">
          <label htmlFor="export-format" className="sr-only">
            导出格式
          </label>
          <select
            id="export-format"
            value={exportFormat}
            onChange={(e) => setExportFormat(e.target.value as 'md' | 'slides' | 'ics' | 'anki')}
            className="rounded-lg border px-2 py-2 text-sm"
          >
            <option value="md">Markdown</option>
            <option value="slides">幻灯片</option>
            <option value="ics">日历 (.ics)</option>
            <option value="anki">Anki 卡片包</option>
          </select>
          <button onClick={doExport} className="rounded-lg border px-4 py-2 text-sm hover:bg-slate-50">
            导出
          </button>
          <button onClick={doShare} className="rounded-lg border px-4 py-2 text-sm hover:bg-slate-50">
            {share?.shared ? '复制分享链接' : '分享'}
          </button>
          {share?.shared && (
            <button onClick={doRevokeShare} className="rounded-lg border px-3 py-2 text-sm text-red-600 hover:bg-red-50">
              撤销分享
            </button>
          )}
          <button
            onClick={() => nav(`/learn/${planId}`)}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            学习中心
          </button>
          <button
            onClick={() => setShowMaterials(true)}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            自学材料
          </button>
          <button
            onClick={() => setShowQuiz(true)}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700"
          >
            测验
          </button>
        </div>
      </div>
      {exportUrl && <div className="text-xs text-slate-500">已导出: {exportUrl}</div>}
      {shareMsg && <div className="break-all text-xs text-emerald-600">{shareMsg}</div>}

      {showMaterials && planId && (
        <MaterialsModal
          planId={Number(planId)}
          onClose={() => setShowMaterials(false)}
          onOpenQuiz={() => {
            setShowMaterials(false) // 「去测验」：关材料弹窗开测验弹窗（plan/07 §六 检查理解可跳测验）
            setShowQuiz(true)
          }}
        />
      )}
      {showQuiz && planId && (
        <QuizModal
          planId={Number(planId)}
          onClose={() => {
            setShowQuiz(false)
            setProgressKey((k) => k + 1) // 测验可能已回写进度，关闭后刷新看板
          }}
        />
      )}

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

      {planId && (
        <section className="rounded-lg border bg-white p-4" aria-label="学习进度">
          <h3 className="mb-2 font-semibold">学习进度</h3>
          <ProgressBoard planId={Number(planId)} refreshToken={progressKey} />
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

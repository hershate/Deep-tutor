import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import MaterialsModal from '../components/MaterialsModal'
import MaterialsPanel from '../components/MaterialsPanel'
import EmptyState from '../components/EmptyState'
import PlanOutline from '../components/PlanOutline'
import ProgressBoard from '../components/ProgressBoard'
import QuizModal from '../components/QuizModal'
import { SkeletonCards } from '../components/Skeleton'
import Timeline, { type ScheduleDay } from '../components/Timeline'
import { toast } from '../components/Toast'
import { ApiError, createShare, exportPlan, getPlan, getShare, regenerateStream, revokeShare, type ShareStatus } from '../lib/api'
import { isHttpUrl } from '../lib/url'

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
  knowledge_map?: {
    nodes?: Array<{ id: string; name: string; difficulty?: number; must_learn?: boolean; parent_id?: string | null }>
    edges?: Array<{ from: string; to: string; relation?: string }>
  }
  schedule?: ScheduleDay[]
  resources?: Array<{ title?: string; url?: string; type?: string; verified?: string; note?: string }>
  validation_report?: { passed?: boolean; degraded?: boolean; report?: Record<string, { status?: string; details?: string }>; fixes?: string[] }
}

const VERIFY_LABEL: Record<string, string> = { ok: '✅', uncertain: '⚠️', invalid: '❌' }
const VERIFY_TEXT: Record<string, string> = { ok: '已验证', uncertain: '不确定', invalid: '无效' }

// Tabs（plan/06 §四.4）：学习路径 / 时间线 / 资源 / 校验报告
type TabKey = 'path' | 'timeline' | 'resources' | 'report'
const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'path', label: '学习路径' },
  { key: 'timeline', label: '时间线' },
  { key: 'resources', label: '资源' },
  { key: 'report', label: '校验报告' },
]

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
  // err.md D1：初始加载错误（整页错误块）与操作错误（内联 banner）分离——
  // 方案已加载后导出/分享失败不再把整页替换成无出口的裸错误块
  const [error, setError] = useState('')
  const [opError, setOpError] = useState('')
  const [exportUrl, setExportUrl] = useState('')
  const [exportBusy, setExportBusy] = useState(false) // err.md D8：导出防重复提交
  const [exportFormat, setExportFormat] = useState<'md' | 'slides' | 'ics' | 'anki' | 'pdf'>('md')
  const [showMaterials, setShowMaterials] = useState(false)
  const [showQuiz, setShowQuiz] = useState(false)
  const [showWrongQuiz, setShowWrongQuiz] = useState(false) // 错题重练（iter 114）
  const [share, setShare] = useState<ShareStatus | null>(null)
  const [shareMsg, setShareMsg] = useState('')
  const [progressKey, setProgressKey] = useState(0) // 测验回写后刷新进度看板
  const [tab, setTab] = useState<TabKey>('path')
  const [sessionId, setSessionId] = useState<number | null>(null) // 源资料面板（iter 104）
  const [wrongExportBusy, setWrongExportBusy] = useState(false) // 错题 Anki 导出（iter 134）
  const [resFilter, setResFilter] = useState('') // 资源 Tab ✅⚠️❌ 筛选（plan/06 §四.4）
  const [regenScope, setRegenScope] = useState('design_stages')
  const [regenFeedback, setRegenFeedback] = useState('')
  const [regenLoading, setRegenLoading] = useState(false)
  const [regenError, setRegenError] = useState('')
  const regenAcRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!planId) return
    // err.md D3：重生成 done 后 nav 到新版 planId（同路由组件不卸载），
    // 在此复位重生成区状态——否则 regenLoading 永远为 true，区域永久禁用
    setRegenLoading(false)
    setRegenError('')
    setOpError('')
    let cancelled = false // err.md D6：快速切换 planId 时旧响应不得覆盖新数据
    setLoading(true)
    getPlan(Number(planId))
      .then((r) => { if (!cancelled) { setPlan((r as { plan: PlanData }).plan); setSessionId((r as { session_id?: number }).session_id ?? null) } })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : '加载失败') })
      .finally(() => { if (!cancelled) setLoading(false) })
    getShare(Number(planId))
      .then((s) => { if (!cancelled) setShare(s) })
      .catch(() => { if (!cancelled) setShare(null) })
    return () => { cancelled = true }
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
        toast.success('分享链接已复制到剪贴板')
      } catch {
        setShareMsg(url) // 剪贴板不可用（非安全上下文）则直接展示
        toast.info('剪贴板不可用，请手动复制链接')
      }
    } catch (e) {
      setOpError(e instanceof ApiError ? e.message : '创建分享失败')
    }
  }

  async function doRevokeShare() {
    if (!planId) return
    try {
      await revokeShare(Number(planId))
      setShare({ shared: false })
      setShareMsg('已撤销，原链接即刻失效')
      toast.success('分享已撤销')
    } catch (e) {
      setOpError(e instanceof ApiError ? e.message : '撤销失败')
    }
  }

  // 卸载时取消进行中的重生成流
  useEffect(() => () => regenAcRef.current?.abort(), [])

  async function doExport() {
    if (!planId || exportBusy) return // err.md D8：防重复提交
    setExportBusy(true)
    setOpError('')
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
      } else if (exportFormat === 'pdf' && r.pdf_b64) {
        // PDF（iter 79）：base64 -> Blob 下载；weasyprint 不可用时后端降级 md（走下方 content 分支）
        const bytes = Uint8Array.from(atob(r.pdf_b64), (c) => c.charCodeAt(0))
        downloadBlob(new Blob([bytes], { type: 'application/pdf' }), r.filename || 'plan.pdf')
        setExportUrl(`已下载 ${r.filename}`)
      } else if (r.content) {
        // Markdown（含 pdf 降级 md 的情形：后端 R9 降级时返回 md 正文 + warning）
        downloadBlob(new Blob([r.content], { type: 'text/markdown;charset=utf-8' }), r.filename || 'plan.md')
        setExportUrl(`已下载 ${r.filename || 'plan.md'}`)
      } else {
        setExportUrl(r.file_url)
      }
      if (r.warning) setOpError(`导出警告：${r.warning}`)
    } catch (e) {
      setOpError(e instanceof ApiError ? e.message : '导出失败')
    } finally {
      setExportBusy(false)
    }
  }

  // 错题 Anki 导出（iter 134）：仅最近判错的题 -> 独立错题牌组 .apkg（不消耗 API 额度）
  async function doExportWrong() {
    if (!planId || wrongExportBusy) return
    setWrongExportBusy(true)
    setOpError('')
    try {
      const r = await exportPlan(Number(planId), 'anki', true)
      if (r.apkg_b64) {
        const bytes = Uint8Array.from(atob(r.apkg_b64), (c) => c.charCodeAt(0))
        downloadBlob(new Blob([bytes], { type: 'application/octet-stream' }), r.filename || 'wrong.apkg')
        setExportUrl(`已下载 ${r.filename}（${r.card_count ?? 0} 张错题卡）`)
        toast.success(`已导出 ${r.card_count ?? 0} 张错题卡`)
      }
    } catch (e) {
      // no_wrong_quizzes（全部答对/未作答）等后端信息直接透出
      setOpError(e instanceof ApiError ? e.message : '错题导出失败')
    } finally {
      setWrongExportBusy(false)
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

  if (loading) return <SkeletonCards count={4} />
  // 初始加载失败：整页错误 + 出口（err.md D1：不再是无返回/重试的裸错误块）
  if (error)
    return (
      <div role="alert" className="space-y-3 rounded-lg border border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950 p-4 text-red-700 dark:text-red-300">
        <p>{error}</p>
        <div className="flex gap-3 text-sm">
          <button onClick={() => nav('/plans')} className="underline">
            返回方案列表
          </button>
          <button onClick={() => window.location.reload()} className="underline">
            重试
          </button>
        </div>
      </div>
    )
  if (!plan) return <p className="text-slate-500 dark:text-slate-400">无方案数据</p>

  return (
    <div className="space-y-6">
      {opError && (
        <div role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950 p-3 text-sm text-red-700 dark:text-red-300">
          {opError}
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-2xl font-bold">{plan.intent?.topic ?? '学习方案'}</h2>
        {/* iter 93 美化：窄屏 flex-wrap 防溢出；次按钮按压缩放、主按钮悬浮阴影 */}
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="export-format" className="sr-only">
            导出格式
          </label>
          <select
            id="export-format"
            value={exportFormat}
            onChange={(e) => setExportFormat(e.target.value as 'md' | 'slides' | 'ics' | 'anki' | 'pdf')}
            className="rounded-lg border px-2 py-2 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-800"
          >
            <option value="md">Markdown</option>
            <option value="slides">幻灯片</option>
            <option value="ics">日历 (.ics)</option>
            <option value="anki">Anki 卡片包</option>
            <option value="pdf">PDF</option>
          </select>
          <button
            onClick={doExport}
            disabled={exportBusy}
            className="rounded-lg border px-4 py-2 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700 disabled:opacity-50 disabled:active:scale-100"
          >
            {exportBusy ? '导出中…' : '导出'}
          </button>
          <button onClick={doShare} className="rounded-lg border px-4 py-2 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700">
            {share?.shared ? '复制分享链接' : '分享'}
          </button>
          {share?.shared && (
            <button onClick={doRevokeShare} className="rounded-lg border border-red-200 px-3 py-2 text-sm text-red-600 transition-all hover:bg-red-50 active:scale-95 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950">
              撤销分享
            </button>
          )}
          <button
            onClick={() => nav(`/learn/${planId}`)}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-indigo-700 hover:shadow-md active:scale-[.98]"
          >
            学习中心
          </button>
          <button
            onClick={() => setShowMaterials(true)}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98]"
          >
            自学材料
          </button>
          <button
            onClick={() => setShowQuiz(true)}
            className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-emerald-700 hover:shadow-md active:scale-[.98]"
          >
            测验
          </button>
          <button
            onClick={() => setShowWrongQuiz(true)}
            title="只练最近答错的题（不消耗 API 额度）"
            className="rounded-lg border border-emerald-300 px-4 py-2 text-sm font-medium text-emerald-700 shadow-sm transition-all hover:bg-emerald-50 hover:shadow-md active:scale-[.98] dark:border-emerald-800 dark:text-emerald-300 dark:hover:bg-emerald-950"
          >
            错题重练
          </button>
          {/* 错题 Anki 导出（iter 134）：独立错题牌组 .apkg，双击导入 Anki */}
          <button
            onClick={doExportWrong}
            disabled={wrongExportBusy}
            title="把最近答错的题导出为 Anki 卡片包（.apkg），离线复习"
            className="rounded-lg border border-emerald-300 px-3 py-2 text-sm text-emerald-700 shadow-sm transition-all hover:bg-emerald-50 hover:shadow-md active:scale-[.98] disabled:opacity-50 disabled:active:scale-100 dark:border-emerald-800 dark:text-emerald-300 dark:hover:bg-emerald-950"
          >
            {wrongExportBusy ? '导出中…' : '导出错题'}
          </button>
        </div>
      </div>
      {exportUrl && <div className="text-xs text-slate-500 dark:text-slate-400">已导出: {exportUrl}</div>}
      {shareMsg && <div className="break-all text-xs text-emerald-600 dark:text-emerald-400">{shareMsg}</div>}

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
      {showWrongQuiz && planId && (
        <QuizModal
          wrong
          planId={Number(planId)}
          onClose={() => {
            setShowWrongQuiz(false)
            setProgressKey((k) => k + 1) // 重练同样回写进度
          }}
        />
      )}

      {plan.intent && (
        <section className="rounded-xl border bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800 p-4">
          <h3 className="mb-2 font-semibold">学习意图</h3>
          <ul className="text-sm text-slate-600 dark:text-slate-300">
            {plan.intent.level && <li>基础: {plan.intent.level}</li>}
            {plan.intent.goal && <li>目标: {plan.intent.goal}</li>}
            {plan.intent.daily_minutes && <li>每日: {plan.intent.daily_minutes} 分钟</li>}
          </ul>
        </section>
      )}

      {/* 源资料清单（iter 104）：可单个删除后重生成 */}
      {sessionId !== null && <MaterialsPanel sessionId={sessionId} />}

      <div role="tablist" aria-label="方案内容" className="flex gap-1 border-b dark:border-slate-700">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors duration-200 ${
              tab === t.key
                ? 'border-blue-600 text-blue-700 dark:text-blue-300'
                : 'border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-700 dark:text-slate-400 dark:hover:border-slate-600 dark:hover:text-slate-200'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'path' && (
        <div role="tabpanel" aria-label="学习路径" className="animate-fade-in">
          <PlanOutline stages={plan.stages} knowledgeMap={plan.knowledge_map} />
        </div>
      )}

      {tab === 'timeline' && (
        <div role="tabpanel" aria-label="时间线" className="animate-fade-in">
          <Timeline schedule={plan.schedule ?? []} stageNames={(plan.stages ?? []).map((s) => s.name)} />
        </div>
      )}

      {tab === 'resources' && (
        <div role="tabpanel" aria-label="资源" className="animate-fade-in space-y-3">
          {plan.resources && plan.resources.length > 0 ? (
            <>
              <div className="flex items-center gap-2 text-sm">
                <span className="text-slate-500 dark:text-slate-400">筛选：</span>
                {[
                  { v: '', label: '全部' },
                  { v: 'ok', label: '✅ 已验证' },
                  { v: 'uncertain', label: '⚠️ 不确定' },
                  { v: 'invalid', label: '❌ 无效' },
                ].map((f) => (
                  <button
                    key={f.v}
                    onClick={() => setResFilter(f.v)}
                    aria-pressed={resFilter === f.v}
                    className={`rounded-full border px-3 py-1 text-xs transition-all duration-150 active:scale-95 ${
                      resFilter === f.v ? 'border-blue-600 bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300' : 'text-slate-500 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-700'
                    }`}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
              <ul className="space-y-1 rounded-xl border bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800 p-4 text-sm">
                {plan.resources
                  .filter((r) => !resFilter || (r.verified ?? '') === resFilter)
                  .map((r, i) => (
                    <li key={i}>
                      <span aria-hidden="true">{VERIFY_LABEL[r.verified ?? ''] ?? '·'}</span>{' '}
                      <span className="sr-only">{VERIFY_TEXT[r.verified ?? ''] ?? '未知'}：</span>
                      {r.title}{' '}
                      {isHttpUrl(r.url) ? (
                        <a href={r.url} target="_blank" rel="noreferrer" className="break-all text-blue-600 underline-offset-2 transition-colors hover:text-blue-500 hover:underline dark:text-blue-400 dark:hover:text-blue-300">
                          {r.url}
                        </a>
                      ) : (
                        <span className="text-slate-500 dark:text-slate-400">{r.url}</span>
                      )}
                      {r.note && <span className="text-slate-400">（{r.note}）</span>}
                    </li>
                  ))}
              </ul>
            </>
          ) : (
            <EmptyState icon="🔗" title="暂无配套资源" description="方案未包含资源推荐" />
          )}
        </div>
      )}

      {tab === 'report' && (
        <div role="tabpanel" aria-label="校验报告" className="animate-fade-in">
          {plan.validation_report ? (
            <section className="rounded-xl border bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800 p-4">
              <h3 className="mb-2 font-semibold">
                校验报告: {plan.validation_report.passed ? '通过 ✅' : '未通过 ⚠️'}
              </h3>
              {plan.validation_report.degraded && (
                <p className="mb-2 rounded bg-amber-50 dark:bg-amber-950 p-2 text-sm text-amber-700 dark:text-amber-300">
                  自动校验未执行（校验工具降级），方案未经核验，请人工复核。
                </p>
              )}
              {plan.validation_report.report && (
                <ul className="text-sm">
                  {Object.entries(plan.validation_report.report).map(([k, v]) => (
                    <li key={k}>{k}: {v.status} {v.details}</li>
                  ))}
                </ul>
              )}
              {plan.validation_report.fixes && plan.validation_report.fixes.length > 0 && (
                <div className="mt-2 text-sm">
                  <span className="text-slate-500 dark:text-slate-400">修复建议：</span>
                  <ul className="list-disc pl-5">
                    {plan.validation_report.fixes.map((f, i) => (
                      <li key={i}>{f}</li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
          ) : (
            <p className="text-sm text-slate-500 dark:text-slate-400">暂无校验报告。</p>
          )}
        </div>
      )}

      {planId && (
        <section className="rounded-xl border bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800 p-4" aria-label="学习进度">
          <h3 className="mb-2 font-semibold">学习进度</h3>
          <ProgressBoard planId={Number(planId)} refreshToken={progressKey} />
        </section>
      )}

      <section className="space-y-3 rounded-xl border bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800 p-4" aria-label="重生成方案">
        <h3 className="font-semibold">重生成</h3>
        <p className="text-sm text-slate-500 dark:text-slate-400">从选定工具起级联重跑其下游，生成新版本方案（plan.version+1）。</p>
        <div className="flex items-center gap-2">
          <label htmlFor="regen-scope" className="text-sm text-slate-600 dark:text-slate-300">
            起点
          </label>
          <select
            id="regen-scope"
            value={regenScope}
            onChange={(e) => setRegenScope(e.target.value)}
            className="rounded-lg border p-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 dark:border-slate-600 dark:bg-slate-900"
            disabled={regenLoading}
          >
            <option value="design_stages">阶段设计</option>
            <option value="search_resources">资源</option>
            <option value="schedule_plan">排期</option>
            <option value="validate_plan">校验</option>
          </select>
        </div>
        <div className="space-y-1">
          <label htmlFor="regen-feedback" className="block text-sm text-slate-600 dark:text-slate-300">
            反馈（可选）
          </label>
          <textarea
            id="regen-feedback"
            className="w-full min-h-[60px] rounded-lg border border-slate-300 p-2 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-900"
            placeholder="如：简化阶段、增加资源、调整难度梯度"
            value={regenFeedback}
            onChange={(e) => setRegenFeedback(e.target.value)}
            disabled={regenLoading}
          />
        </div>
        <button
          onClick={doRegenerate}
          disabled={regenLoading}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98] disabled:opacity-50 disabled:hover:shadow-sm disabled:active:scale-100"
        >
          {regenLoading ? '重生成中…' : '重生成'}
        </button>
        {regenError && (
          <div role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950 p-3 text-sm text-red-700 dark:text-red-300">
            {regenError}
          </div>
        )}
      </section>
    </div>
  )
}

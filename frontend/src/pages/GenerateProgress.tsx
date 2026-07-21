// 生成进度页（iter 190 F5 重构）：纯订阅视图——渲染 store/tasks 台账，
// SSE 连接由 App 级任务引擎持有（lib/taskEngine.ts）；挂载时 engine.attach 判态，
// 卸载不再 abort（切页/刷新生成在后台继续，回来自动重放恢复）。
// 视图矩阵：loading / 404 / idle（引导）/ running（进度条+步骤+断线横幅）/
// done（1.5s 倒计时自动跳）/ error·interrupted（重试生成）/ cancelled。
import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import PlanOutline, { type OutlineNode, type OutlineStage } from '../components/PlanOutline'
import Timeline, { type ScheduleDay } from '../components/Timeline'
import { SkeletonCards } from '../components/Skeleton'
import { TOOL_LABELS, fmtElapsed } from '../components/TaskCenter'
import { toast } from '../components/Toast'
import { ApiError } from '../lib/api'
import { engine } from '../lib/taskEngine'
import { isHttpUrl } from '../lib/url'
import { planTaskId, useTaskStore } from '../store/tasks'

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
  const sid = Number(sessionId)
  const loc = useLocation()
  const nav = useNavigate()
  const taskId = planTaskId(sid)
  const task = useTaskStore((s) => s.tasks[taskId])
  const [attachChecked, setAttachChecked] = useState(false) // 首次判态完成前 loading
  const [notFound, setNotFound] = useState(false)
  const [detailStatus, setDetailStatus] = useState<string | null>(null) // attach 返回的服务端状态（idle 判定）
  const [showPartial, setShowPartial] = useState(false)

  // 挂载判态：running->引擎续接（重放+实时）；idle->按需自动发起（Home 带 rawText 旧路径兜底）；
  // 终态->静默落相位；404->404 视图。卸载不 abort（引擎持连接，生成后台继续）。
  useEffect(() => {
    if (!sid) return
    let cancelled = false
    setAttachChecked(false)
    setNotFound(false)
    setDetailStatus(null)
    engine
      .attach(sid)
      .then((d) => {
        if (cancelled || !d) return
        setDetailStatus(d.status)
        if (d.status === 'idle') {
          const raw = (loc.state as { rawText?: string } | null)?.rawText ?? ''
          const cur = useTaskStore.getState().tasks[planTaskId(sid)]
          if (raw && !cur && !engine.isLive(planTaskId(sid))) {
            void engine.startPlan(sid, raw).catch((e: unknown) => {
              toast.error(e instanceof Error ? e.message : '发起生成失败')
            })
          }
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return
        if (e instanceof ApiError && e.status === 404) setNotFound(true)
        else toast.error(e instanceof Error ? e.message : '加载会话状态失败')
      })
      .finally(() => {
        if (!cancelled) setAttachChecked(true)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid])

  const phase = task?.phase

  // 待澄清 -> 转澄清页（问题经 state 传递，深链由 Clarify 三级回退兜底）
  useEffect(() => {
    if (phase === 'clarify') {
      nav(`/clarify/${sid}`, { state: { questions: task?.questions ?? [] }, replace: true })
    }
  }, [phase, sid, nav, task?.questions])

  // done -> 1.5s 倒计时自动跳方案页（「立即查看」可跳过）
  useEffect(() => {
    if (phase !== 'done' || !task?.resultPlanId) return
    const t = setTimeout(() => nav(`/plans/${task.resultPlanId}`), 1500)
    return () => clearTimeout(t)
  }, [phase, task?.resultPlanId, nav])

  // 秒表：从 startedAt 连续（跨刷新不重置），running 每秒走
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (phase !== 'running') return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [phase])

  // 部分方案预览弹窗：Esc 关闭（与 QuizModal/MaterialsModal/KbViewerModal 行为一致）
  useEffect(() => {
    if (!showPartial) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setShowPartial(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [showPartial])

  const onCancel = () => {
    if (!window.confirm('确定取消生成吗？已产生的费用不会退回。')) return
    void engine.cancel(sid).catch((e: unknown) => {
      toast.error(e instanceof Error ? e.message : '取消请求失败，请重试（生成仍在进行）')
    })
  }
  const onRetry = () => {
    // start_rejected（启动即败，服务端 idle）：走 startPlan 重新发起而非 retry（retry 必 409）
    const p = task?.errorCode === 'start_rejected' && task.rawText ? engine.startPlan(sid, task.rawText) : engine.retry(sid)
    void p.catch((e: unknown) => {
      toast.error(e instanceof Error ? e.message : '重试失败，请稍后再试')
    })
  }
  const onReconnect = () => {
    void engine.attach(sid).catch((e: unknown) => {
      toast.error(e instanceof Error ? e.message : '重连失败')
    })
  }
  const onContinue = () => {
    const raw = task?.rawText ?? ''
    if (!raw) return
    void engine.startPlan(sid, raw).catch((e: unknown) => {
      toast.error(e instanceof Error ? e.message : '发起生成失败')
    })
  }

  // --- 视图分支 ---
  if (notFound) {
    return (
      <div className="space-y-4">
        <h2 className="text-2xl font-bold">会话不存在</h2>
        <div className="rounded-lg border bg-white p-6 text-center dark:border-slate-700 dark:bg-slate-800">
          <p className="text-slate-500 dark:text-slate-400">会话 #{sessionId} 不存在或已被删除（可能在其他标签页删除）。</p>
          <button onClick={() => nav('/')} className="mt-3 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">
            返回首页
          </button>
        </div>
      </div>
    )
  }

  if (!task && !attachChecked) {
    return (
      <div className="space-y-4" role="status" aria-label="加载会话状态">
        <h2 className="text-2xl font-bold">加载中…</h2>
        <SkeletonCards count={2} />
      </div>
    )
  }

  // idle 引导视图（从未生成；或服务端 idle 但台账残留 rawText——如配额失败后——可「继续生成」）
  if (detailStatus === 'idle' && phase !== 'running' && phase !== 'clarify') {
    return (
      <div className="space-y-4">
        <h2 className="text-2xl font-bold">会话 #{sessionId}</h2>
        <div className="rounded-lg border bg-white p-6 text-center dark:border-slate-700 dark:bg-slate-800">
          <p className="text-slate-500 dark:text-slate-400">该会话尚未开始生成。</p>
          <div className="mt-3 flex items-center justify-center gap-3">
            {task?.rawText && (
              <button onClick={onContinue} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">
                继续生成
              </button>
            )}
            <button
              onClick={() => nav('/')}
              className={`rounded-lg px-4 py-2 text-sm font-medium ${task?.rawText ? 'border text-slate-600 hover:bg-slate-50 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-700' : 'bg-blue-600 text-white hover:bg-blue-700'}`}
            >
              回首页描述需求
            </button>
          </div>
        </div>
      </div>
    )
  }

  if (!task) {
    // attach 完成但台账无条目且非 idle（边界：他端刚发起、reconcile 未到）——短暂加载态
    return (
      <div className="space-y-4" role="status">
        <h2 className="text-2xl font-bold">加载中…</h2>
        <SkeletonCards count={2} />
      </div>
    )
  }

  const partial = task.partialPlan as PartialPlan | undefined
  const totalSteps = task.steps.reduce((m, s) => Math.max(m, s.totalSteps || 0), 0) || 8
  const doneIdx = new Set(task.steps.filter((s) => s.status === 'done').map((s) => s.stepIndex))
  const doneCount = Math.min(doneIdx.size, totalSteps)
  const pct = Math.round((doneCount / totalSteps) * 100)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-3">
          <h2 className="flex items-center gap-2 text-2xl font-bold">
            {phase === 'running' && (
              <span className="relative flex h-2.5 w-2.5" aria-hidden="true">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-75" />
                <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-blue-600" />
              </span>
            )}
            {phase === 'running' ? '生成中…' : phase === 'done' ? '生成完成' : phase === 'cancelled' ? '已取消' : phase === 'clarify' ? '待澄清' : '生成未完成'}
          </h2>
          {phase === 'running' && (
            <button
              onClick={onCancel}
              className="rounded-lg border px-3 py-1 text-sm text-slate-600 transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-700"
            >
              取消
            </button>
          )}
          {partial && (phase === 'running' || phase === 'done') && (
            <button
              onClick={() => setShowPartial(true)}
              className="rounded-lg border px-3 py-1 text-sm text-blue-600 transition-all hover:bg-blue-50 active:scale-95 dark:border-slate-600 dark:text-blue-400 dark:hover:bg-slate-700"
            >
              查看部分 →
            </button>
          )}
        </div>
        <span className="text-sm tabular-nums text-slate-500 dark:text-slate-400">
          <span aria-label="已用时长">⏱ {fmtElapsed(now - task.startedAt)}</span>
          <span className="mx-1.5 text-slate-300 dark:text-slate-600">·</span>
          {task.tokens.toLocaleString()} tokens
          <span className="mx-1.5 text-slate-300 dark:text-slate-600">·</span>
          费用 ${task.cost.toFixed(4)}
        </span>
      </div>
      <p className="text-sm text-slate-500 dark:text-slate-400">
        {task.title || `会话 #${sessionId}`}
      </p>

      {/* 断线提示：重连中（琥珀）/ 重连耗尽（橙，可重试连接或重试生成；后端仍在跑） */}
      {phase === 'running' && task.conn === 'reconnecting' && (
        <div role="status" className="animate-fade-in rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
          连接中断，正在自动重连…生成在后台继续，无需操作。
        </div>
      )}
      {phase === 'running' && task.conn === 'offline' && (
        <div role="alert" className="animate-fade-in rounded-lg border border-orange-200 bg-orange-50 p-3 text-sm text-orange-800 dark:border-orange-900 dark:bg-orange-950 dark:text-orange-200">
          <p>连接已断开（自动重连失败）。生成很可能仍在后台继续——可重试连接恢复实时进度，或直接重试生成。</p>
          <div className="mt-2 flex gap-3">
            <button onClick={onReconnect} className="rounded-lg border px-3 py-1 text-sm transition-all hover:bg-orange-100 active:scale-95 dark:border-orange-800 dark:hover:bg-orange-900">
              重试连接
            </button>
            <button onClick={onRetry} className="rounded-lg border px-3 py-1 text-sm transition-all hover:bg-orange-100 active:scale-95 dark:border-orange-800 dark:hover:bg-orange-900">
              重试生成
            </button>
          </div>
        </div>
      )}

      {phase === 'running' && (
        <>
          {/* 进度条：n/8 步 + 百分比（反馈循环重跑按 stepIndex 去重，进度可回退） */}
          <div>
            <div className="mb-1 flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
              <span>
                已完成 {doneCount}/{totalSteps} 步
              </span>
              <span className="tabular-nums">{pct}%</span>
            </div>
            <div
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`生成进度 ${pct}%`}
              className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700"
            >
              <div className="h-full rounded-full bg-blue-500 transition-all duration-500" style={{ width: `${pct}%` }} />
            </div>
          </div>
          <p className="rounded-lg border border-blue-100 bg-blue-50/60 px-3 py-2 text-xs text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300">
            可安全离开本页：生成在后台继续，任务中心与方案列表可随时查看进度，完成后会通知你。
          </p>
        </>
      )}

      <ul className="space-y-2">
        {task.steps.map((st, i) => (
          <li
            key={`${st.tool}:${st.attempt}:${i}`}
            className={`flex animate-fade-in-up items-center gap-3 rounded-lg border p-3 transition-colors duration-300 ${
              st.status === 'running'
                ? 'border-blue-200 bg-blue-50/60 dark:border-blue-900 dark:bg-blue-950/40'
                : 'bg-white dark:border-slate-700 dark:bg-slate-800'
            }`}
          >
            <span className="flex w-5 items-center justify-center">
              {st.status === 'running' ? (
                <span aria-hidden="true" className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600 dark:border-slate-600 dark:border-t-blue-400" />
              ) : (
                <span aria-hidden="true">{st.status === 'error' ? '❌' : '✅'}</span>
              )}
              <span className="sr-only">{st.status === 'running' ? '进行中' : st.status === 'error' ? '失败' : '完成'}</span>
            </span>
            <span className="text-xs tabular-nums text-slate-400">{st.stepIndex || i + 1}.</span>
            <span className="font-medium">{TOOL_LABELS[st.tool] ?? st.tool}</span>
            {st.attempt > 1 && (
              <span
                title="校验未通过，反馈循环自动重跑该步骤及其下游"
                className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700 dark:bg-amber-950 dark:text-amber-300"
              >
                ↻ 第 {st.attempt} 次
              </span>
            )}
          </li>
        ))}
      </ul>

      {phase === 'done' && (
        <div role="status" className="animate-fade-in rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-200">
          <p className="font-medium">✅ 方案已生成，正在跳转…</p>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-emerald-100 dark:bg-emerald-900">
            <div className="h-full w-full origin-left animate-[countdown-shrink_1.5s_linear_forwards] rounded-full bg-emerald-500" />
          </div>
          <button
            onClick={() => task.resultPlanId && nav(`/plans/${task.resultPlanId}`)}
            className="mt-2 text-sm font-medium underline underline-offset-2 hover:text-emerald-900 dark:hover:text-emerald-100"
          >
            立即查看 →
          </button>
        </div>
      )}

      {(phase === 'error' || phase === 'interrupted') && (
        <div role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 p-4 text-red-700 dark:border-red-900 dark:bg-slate-800 dark:text-red-300">
          <p className="font-medium">{phase === 'interrupted' ? '生成中断' : '生成失败'}</p>
          <p className="text-sm">{task.errorMessage || (phase === 'interrupted' ? '生成被中断，可重试' : '生成失败，可重试')}</p>
          <div className="mt-2 flex gap-3">
            <button onClick={onRetry} className="rounded-lg bg-red-600 px-3 py-1 text-sm font-medium text-white transition-all hover:bg-red-700 active:scale-95">
              重试生成
            </button>
            <button onClick={() => nav('/')} className="text-sm underline underline-offset-2">
              返回首页
            </button>
          </div>
        </div>
      )}

      {phase === 'cancelled' && (
        <div className="animate-fade-in rounded-lg bg-slate-100 p-4 text-slate-700 dark:bg-slate-700 dark:text-slate-200">
          <p className="font-medium">已取消</p>
          <div className="mt-2 flex gap-3">
            <button onClick={onRetry} className="rounded-lg border bg-white px-3 py-1 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:bg-slate-800 dark:hover:bg-slate-700">
              重试生成
            </button>
            <button onClick={() => nav('/')} className="text-sm underline underline-offset-2">
              返回首页
            </button>
          </div>
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
                {phase === 'running' && <span className="ml-2 text-xs font-normal text-slate-400">（生成中，实时更新）</span>}
              </h3>
              <button
                onClick={() => setShowPartial(false)}
                aria-label="关闭预览"
                className="rounded px-2 py-1 text-slate-400 transition-colors hover:bg-slate-100 dark:hover:bg-slate-700"
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
                          <a href={r.url} target="_blank" rel="noreferrer" className="break-all text-blue-600 underline-offset-2 transition-colors hover:text-blue-500 hover:underline dark:text-blue-400 dark:hover:text-blue-300">
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

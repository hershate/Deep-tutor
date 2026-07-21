// 学习中心页（P2，plan/03 §二 /learn/:planId）：进度看板 + 复习提醒 + 打卡。
// 复习提醒：GET /progress 节点 next_review <= 今日（间隔重复 SM-2-lite 到期节点）。
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import CheckinCalendar from '../components/CheckinCalendar'
import FocusTimer from '../components/FocusTimer'
import ProgressBoard from '../components/ProgressBoard'
import { toast } from '../components/Toast'
import {
  addLearningLog,
  ApiError,
  deleteLearningLog,
  getPlan,
  getProgress,
  listLearningLogs,
  type LearningLogItem,
  type ProgressNode,
} from '../lib/api'

export default function Learn() {
  const { planId } = useParams()
  const id = Number(planId)
  const [topic, setTopic] = useState('')
  const [dueNodes, setDueNodes] = useState<ProgressNode[]>([])
  const [logs, setLogs] = useState<LearningLogItem[]>([])
  const [totalMinutes, setTotalMinutes] = useState(0)
  const [minutes, setMinutes] = useState('30')
  const [note, setNote] = useState('')
  const [progressKey, setProgressKey] = useState(0)
  const [loading, setLoading] = useState(true)
  // err.md D4：初始加载错误（整页）与操作错误（内联 banner）分离——
  // 打卡失败不再把整页学习中心替换成裸错误块
  const [error, setError] = useState('')
  const [opError, setOpError] = useState('')
  const [checkinBusy, setCheckinBusy] = useState(false)
  const [checkinMsg, setCheckinMsg] = useState('')
  const [deletingLogId, setDeletingLogId] = useState<number | null>(null) // iter 150：删除日志防重入
  const [dailyGoal, setDailyGoal] = useState<number | null>(null) // iter 163：intent.daily_minutes 今日目标

  // 今日已学分钟（iter 163）：本地日口径（sv locale 出 YYYY-MM-DD，与后端服务器本地日一致）
  const todayStr = new Date().toLocaleDateString('sv')
  const todayMinutes = logs.filter((l) => l.date === todayStr).reduce((s, l) => s + l.minutes, 0)

  // 本周已学分钟（iter 179）：近 7 天（含今天）本地日窗口；周目标 = 日目标 × 7
  const weekStartStr = new Date(Date.now() - 6 * 86_400_000).toLocaleDateString('sv')
  const weekMinutes = logs.filter((l) => l.date >= weekStartStr).reduce((s, l) => s + l.minutes, 0)
  const weeklyGoal = dailyGoal ? dailyGoal * 7 : null

  // 删除打错的卡（iter 150）：confirm 后 DELETE，成功重取列表
  async function onDeleteLog(l: LearningLogItem) {
    if (deletingLogId !== null) return
    if (!window.confirm(`删除 ${l.date} 的 ${l.minutes} 分钟打卡记录？`)) return
    setDeletingLogId(l.id)
    setOpError('')
    try {
      await deleteLearningLog(l.id)
      loadLogs()
    } catch (e) {
      setOpError(e instanceof ApiError ? e.message : '删除失败，请重试')
    } finally {
      setDeletingLogId(null)
    }
  }

  const loadLogs = useCallback(() => {
    listLearningLogs(id)
      .then((r) => {
        setLogs(r.logs)
        setTotalMinutes(r.total_minutes)
      })
      .catch(() => {}) // 日志区失败不阻塞整页
  }, [id])

  useEffect(() => {
    if (!planId) return
    let cancelled = false // err.md D6：快速切换 planId 时旧响应不得覆盖新数据
    setLoading(true)
    Promise.all([getPlan(id), getProgress(id)])
      .then(([p, prog]) => {
        if (cancelled) return
        const intent = (p as { plan?: { intent?: { topic?: string; daily_minutes?: number } } }).plan?.intent
        setTopic(intent?.topic ?? '')
        setDailyGoal(typeof intent?.daily_minutes === 'number' && intent.daily_minutes > 0 ? intent.daily_minutes : null)
        const today = new Date().toLocaleDateString('sv') // 本地日口径（与 todayStr 及后端 date.today() 一致，UTC 日会在时区交界时段错位）
        setDueNodes(prog.nodes.filter((n) => n.next_review !== null && n.next_review <= today))
      })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : '加载失败') })
      .finally(() => { if (!cancelled) setLoading(false) })
    loadLogs()
    return () => { cancelled = true }
  }, [planId, id, loadLogs])

  // 看板状态切换后刷新复习提醒
  useEffect(() => {
    if (progressKey === 0) return
    getProgress(id)
      .then((prog) => {
        const today = new Date().toLocaleDateString('sv') // 本地日口径，同上
        setDueNodes(prog.nodes.filter((n) => n.next_review !== null && n.next_review <= today))
      })
      .catch(() => {})
  }, [progressKey, id])

  // 打卡成功提示 3 秒自动消失（定时器持 ref，卸载/重复打卡时清理，防卸载后 setState 与旧定时器误清新提示）
  const checkinTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (checkinTimer.current) clearTimeout(checkinTimer.current)
  }, [])

  async function submitLog(m: number, noteText: string): Promise<boolean> {
    if (checkinBusy) return false // 防重入（手动/专注完成双通道共用）
    setCheckinBusy(true)
    setOpError('')
    try {
      await addLearningLog(id, m, noteText)
      setNote('')
      setCheckinMsg('已打卡')
      loadLogs()
      if (checkinTimer.current) clearTimeout(checkinTimer.current)
      checkinTimer.current = setTimeout(() => setCheckinMsg(''), 3000)
      return true
    } catch (e) {
      setOpError(e instanceof ApiError ? e.message : '打卡失败')
      return false
    } finally {
      setCheckinBusy(false)
    }
  }

  async function checkin() {
    const m = Number(minutes)
    // err.md D14：0 分钟打卡无意义且污染统计，下限 1
    if (!Number.isFinite(m) || m < 1 || m > 1440) {
      setOpError('分钟数须在 1-1440 之间')
      return
    }
    await submitLog(m, note.trim())
  }

  // iter 251：专注计时完成——自动按该时长打卡（备注取打卡备注框，空记「番茄钟专注」）；
  // 打卡在途/失败时分钟回填输入框，用户可手动重试，不丢记录
  async function onFocusComplete(mins: number) {
    const ok = await submitLog(mins, note.trim() || '番茄钟专注')
    if (ok) {
      toast.success(`专注完成，已自动打卡 ${mins} 分钟`)
    } else {
      setMinutes(String(mins))
      toast.info(`专注 ${mins} 分钟已填入打卡框，请点击打卡提交`)
    }
  }

  if (!planId) return null
  if (loading)
    return (
      <p className="flex items-center gap-2 text-slate-500 dark:text-slate-400" role="status">
        <span aria-hidden="true" className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600 dark:border-slate-600 dark:border-t-blue-400" />
        加载学习中心…
      </p>
    )
  // 初始加载失败：整页错误 + 出口（err.md D4）
  if (error)
    return (
      <div role="alert" className="space-y-3 rounded-lg border border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950 p-4 text-red-700 dark:text-red-300">
        <p>{error}</p>
        <Link to={`/plans/${id}`} className="text-sm underline underline-offset-2 transition-colors hover:text-red-900 dark:hover:text-red-100">
          返回方案详情
        </Link>
      </div>
    )

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">学习中心{topic ? `：${topic}` : ''}</h2>
        <Link to={`/plans/${id}`} className="text-sm text-blue-600 underline-offset-2 transition-colors hover:text-blue-500 hover:underline dark:text-blue-400 dark:hover:text-blue-300">
          返回方案详情
        </Link>
      </div>

      {opError && (
        <div role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950 p-3 text-sm text-red-700 dark:text-red-300">
          {opError}
        </div>
      )}

      <section className="animate-fade-in-up rounded-xl border bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800 p-4" aria-label="复习提醒">
        <h3 className="mb-2 font-semibold">今日待复习（{dueNodes.length}）</h3>
        {dueNodes.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">暂无到期复习节点（学习或测验后按间隔重复自动推算）。</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {dueNodes.map((n, i) => (
              <li key={n.node_id} className="flex animate-fade-in-up items-center gap-2" style={{ animationDelay: `${Math.min(i, 8) * 50}ms` }}>
                <span className="font-medium">{n.name}</span>
                <span className="text-xs tabular-nums text-slate-400">掌握度 {Math.round(n.mastery * 100)}%</span>
                <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs text-amber-600 dark:bg-amber-950 dark:text-amber-400">复习 {n.next_review}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="animate-fade-in-up rounded-xl border bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800 p-4" style={{ animationDelay: '80ms' }} aria-label="进度看板">
        <h3 className="mb-2 font-semibold">进度看板</h3>
        <ProgressBoard planId={id} refreshToken={progressKey} onChanged={() => setProgressKey((k) => k + 1)} />
      </section>

      <section className="animate-fade-in-up rounded-xl border bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800 p-4" style={{ animationDelay: '160ms' }} aria-label="学习打卡">
        <h3 className="mb-2 font-semibold">打卡（累计 <span className="tabular-nums">{totalMinutes}</span> 分钟）</h3>
        {/* iter 251：专注计时器——计时结束自动打卡，打卡在途/失败回填分钟 */}
        <FocusTimer busy={checkinBusy} onComplete={onFocusComplete} />
        {/* 今日目标进度（iter 163）：方案 intent.daily_minutes 为日目标 */}
        {dailyGoal && (
          <div className="mb-3" aria-label={`今日目标 ${dailyGoal} 分钟，已完成 ${todayMinutes} 分钟`}>
            <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
              <span>今日目标 <span className="tabular-nums">{dailyGoal}</span> 分钟</span>
              <span className={`tabular-nums ${todayMinutes >= dailyGoal ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}>
                已完成 <span className="tabular-nums">{todayMinutes}</span> 分钟
                {todayMinutes >= dailyGoal && ' ✓'}
              </span>
            </div>
            <div
              className="mt-1 h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700"
              role="progressbar"
              aria-valuenow={Math.min(100, Math.round((todayMinutes / dailyGoal) * 100))}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="今日目标完成度"
            >
              <div
                className={`h-full rounded-full transition-all duration-500 ease-out ${todayMinutes >= dailyGoal ? 'bg-emerald-500' : 'bg-blue-500'}`}
                style={{ width: `${Math.min(100, Math.round((todayMinutes / dailyGoal) * 100))}%` }}
              />
            </div>
            {/* 本周目标进度（iter 179）：近 7 天窗口，周目标 = 日目标 × 7 */}
            {weeklyGoal && (
              <>
                <div className="mt-2 flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
                  <span>本周目标 <span className="tabular-nums">{weeklyGoal}</span> 分钟</span>
                  <span className={`tabular-nums ${weekMinutes >= weeklyGoal ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}>
                    已完成 <span className="tabular-nums">{weekMinutes}</span> 分钟
                    {weekMinutes >= weeklyGoal && ' ✓'}
                  </span>
                </div>
                <div
                  className="mt-1 h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700"
                  role="progressbar"
                  aria-valuenow={Math.min(100, Math.round((weekMinutes / weeklyGoal) * 100))}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label="本周目标完成度"
                >
                  <div
                    className={`h-full rounded-full transition-all duration-500 ease-out ${weekMinutes >= weeklyGoal ? 'bg-emerald-500' : 'bg-indigo-400'}`}
                    style={{ width: `${Math.min(100, Math.round((weekMinutes / weeklyGoal) * 100))}%` }}
                  />
                </div>
              </>
            )}
          </div>
        )}
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label htmlFor="checkin-minutes" className="block text-xs text-slate-500 dark:text-slate-400">
              今日学习分钟数
            </label>
            <input
              id="checkin-minutes"
              type="number"
              min={1}
              max={1440}
              value={minutes}
              onChange={(e) => setMinutes(e.target.value)}
              className="w-28 rounded-lg border px-2 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-800"
            />
            {/* 快捷分钟（iter 168）：一键填入常用时长 */}
            <div className="mt-1 flex gap-1" role="group" aria-label="快捷分钟">
              {[15, 30, 60, 120].map((m) => (
                <button
                  key={m}
                  type="button"
                  aria-pressed={minutes === String(m)}
                  onClick={() => setMinutes(String(m))}
                  className={`rounded px-1.5 py-0.5 text-xs tabular-nums transition-all active:scale-95 ${
                    minutes === String(m)
                      ? 'bg-blue-100 font-medium text-blue-700 dark:bg-blue-900 dark:text-blue-300'
                      : 'bg-slate-100 text-slate-500 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-400 dark:hover:bg-slate-600'
                  }`}
                >
                  {m}
                </button>
              ))}
            </div>
          </div>
          <div className="min-w-48 flex-1">
            <label htmlFor="checkin-note" className="block text-xs text-slate-500 dark:text-slate-400">
              备注（可选）
            </label>
            <input
              id="checkin-note"
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="今天学了什么"
              maxLength={200}
              className="w-full rounded-lg border px-2 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-800"
            />
          </div>
          <button
            onClick={checkin}
            disabled={checkinBusy}
            className="rounded-lg bg-blue-600 px-4 py-1.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98] disabled:opacity-50 disabled:hover:shadow-sm disabled:active:scale-100"
          >
            {checkinBusy ? '打卡中…' : '打卡'}
          </button>
          {checkinMsg && (
            <span className="animate-fade-in text-sm text-green-700 dark:text-green-300" aria-live="polite">
              {checkinMsg} ✓
            </span>
          )}
        </div>
        {logs.length > 0 && (
          <>
            <div className="mt-4 border-t pt-3 dark:border-slate-700">
              <CheckinCalendar logs={logs} />
            </div>
            <ul className="mt-3 space-y-1 text-sm text-slate-600 dark:text-slate-300">
              {logs.slice(0, 10).map((l) => (
                <li key={l.id} className="flex items-center gap-1">
                  <span className="text-slate-400">{l.date}</span> · {l.minutes} 分钟
                  {l.note ? ` · ${l.note}` : ''}
                  {/* 删除打错的卡（iter 150） */}
                  <button
                    onClick={() => onDeleteLog(l)}
                    disabled={deletingLogId !== null}
                    aria-label={`删除 ${l.date} 打卡`}
                    title="删除该条打卡记录"
                    className="rounded px-1 text-xs text-slate-300 transition-all hover:bg-red-50 hover:text-red-600 active:scale-95 disabled:opacity-50 dark:text-slate-600 dark:hover:bg-red-950 dark:hover:text-red-400"
                  >
                    {deletingLogId === l.id ? '…' : '✕'}
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </div>
  )
}

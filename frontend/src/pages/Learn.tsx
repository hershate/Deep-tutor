// 学习中心页（P2，plan/03 §二 /learn/:planId）：进度看板 + 复习提醒 + 打卡。
// 复习提醒：GET /progress 节点 next_review <= 今日（间隔重复 SM-2-lite 到期节点）。
import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import CheckinCalendar from '../components/CheckinCalendar'
import ProgressBoard from '../components/ProgressBoard'
import {
  addLearningLog,
  ApiError,
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
        setTopic(((p as { plan?: { intent?: { topic?: string } } }).plan?.intent?.topic) ?? '')
        const today = new Date().toISOString().slice(0, 10)
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
        const today = new Date().toISOString().slice(0, 10)
        setDueNodes(prog.nodes.filter((n) => n.next_review !== null && n.next_review <= today))
      })
      .catch(() => {})
  }, [progressKey, id])

  async function checkin() {
    const m = Number(minutes)
    // err.md D14：0 分钟打卡无意义且污染统计，下限 1
    if (!Number.isFinite(m) || m < 1 || m > 1440) {
      setOpError('分钟数须在 1-1440 之间')
      return
    }
    setCheckinBusy(true)
    setOpError('')
    try {
      await addLearningLog(id, m, note.trim())
      setNote('')
      setCheckinMsg('已打卡')
      loadLogs()
      setTimeout(() => setCheckinMsg(''), 3000)
    } catch (e) {
      setOpError(e instanceof ApiError ? e.message : '打卡失败')
    } finally {
      setCheckinBusy(false)
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
                <span className="text-xs text-slate-400">掌握度 {Math.round(n.mastery * 100)}%</span>
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
        <h3 className="mb-2 font-semibold">打卡（累计 {totalMinutes} 分钟）</h3>
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
              className="w-28 rounded border px-2 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-800"
            />
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
              className="w-full rounded border px-2 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-800"
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
                <li key={l.id}>
                  <span className="text-slate-400">{l.date}</span> · {l.minutes} 分钟
                  {l.note ? ` · ${l.note}` : ''}
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </div>
  )
}

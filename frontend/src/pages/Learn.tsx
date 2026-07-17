// 学习中心页（P2，plan/03 §二 /learn/:planId）：进度看板 + 复习提醒 + 打卡。
// 复习提醒：GET /progress 节点 next_review <= 今日（间隔重复 SM-2-lite 到期节点）。
import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
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
  const [error, setError] = useState('')
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
    Promise.all([getPlan(id), getProgress(id)])
      .then(([p, prog]) => {
        setTopic(((p as { plan?: { intent?: { topic?: string } } }).plan?.intent?.topic) ?? '')
        const today = new Date().toISOString().slice(0, 10)
        setDueNodes(prog.nodes.filter((n) => n.next_review !== null && n.next_review <= today))
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : '加载失败'))
      .finally(() => setLoading(false))
    loadLogs()
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
    if (!Number.isFinite(m) || m < 0 || m > 1440) {
      setError('分钟数须在 0-1440 之间')
      return
    }
    setCheckinBusy(true)
    setError('')
    try {
      await addLearningLog(id, m, note.trim())
      setNote('')
      setCheckinMsg('已打卡')
      loadLogs()
      setTimeout(() => setCheckinMsg(''), 3000)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '打卡失败')
    } finally {
      setCheckinBusy(false)
    }
  }

  if (!planId) return null
  if (loading) return <p className="text-slate-500">加载学习中心…</p>
  if (error && !dueNodes.length)
    return (
      <div role="alert" className="rounded-lg bg-red-50 p-3 text-red-700">
        {error}
      </div>
    )

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">学习中心{topic ? `：${topic}` : ''}</h2>
        <Link to={`/plans/${id}`} className="text-sm text-blue-600 hover:underline">
          返回方案详情
        </Link>
      </div>

      {error && (
        <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <section className="rounded-lg border bg-white p-4" aria-label="复习提醒">
        <h3 className="mb-2 font-semibold">今日待复习（{dueNodes.length}）</h3>
        {dueNodes.length === 0 ? (
          <p className="text-sm text-slate-500">暂无到期复习节点（学习或测验后按间隔重复自动推算）。</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {dueNodes.map((n) => (
              <li key={n.node_id} className="flex items-center gap-2">
                <span className="font-medium">{n.name}</span>
                <span className="text-xs text-slate-400">掌握度 {Math.round(n.mastery * 100)}%</span>
                <span className="text-xs text-amber-600">复习 {n.next_review}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-lg border bg-white p-4" aria-label="进度看板">
        <h3 className="mb-2 font-semibold">进度看板</h3>
        <ProgressBoard planId={id} refreshToken={progressKey} onChanged={() => setProgressKey((k) => k + 1)} />
      </section>

      <section className="rounded-lg border bg-white p-4" aria-label="学习打卡">
        <h3 className="mb-2 font-semibold">打卡（累计 {totalMinutes} 分钟）</h3>
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label htmlFor="checkin-minutes" className="block text-xs text-slate-500">
              今日学习分钟数
            </label>
            <input
              id="checkin-minutes"
              type="number"
              min={0}
              max={1440}
              value={minutes}
              onChange={(e) => setMinutes(e.target.value)}
              className="w-28 rounded border px-2 py-1.5 text-sm"
            />
          </div>
          <div className="min-w-48 flex-1">
            <label htmlFor="checkin-note" className="block text-xs text-slate-500">
              备注（可选）
            </label>
            <input
              id="checkin-note"
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="今天学了什么"
              className="w-full rounded border px-2 py-1.5 text-sm"
            />
          </div>
          <button
            onClick={checkin}
            disabled={checkinBusy}
            className="rounded-lg bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {checkinBusy ? '打卡中…' : '打卡'}
          </button>
          {checkinMsg && (
            <span className="text-sm text-green-700" aria-live="polite">
              {checkinMsg}
            </span>
          )}
        </div>
        {logs.length > 0 && (
          <ul className="mt-3 space-y-1 text-sm text-slate-600">
            {logs.slice(0, 10).map((l) => (
              <li key={l.id}>
                <span className="text-slate-400">{l.date}</span> · {l.minutes} 分钟
                {l.note ? ` · ${l.note}` : ''}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

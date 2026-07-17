// 进度看板（P2，plan/06 §五 ProgressBoard 简化版）：嵌入 PlanDetail 的知识点进度列表。
// 数据源 GET /api/progress/{planId}（knowledge_nodes 合并 progress 行，iter 39 端点）。
// 状态切换 -> POST /api/progress（覆盖写，掌握度由后端 record_progress 计算）。
// refreshToken 变化时重拉（测验回写后父组件递增触发刷新）。
import { useEffect, useState } from 'react'
import { ApiError, getProgress, updateProgress, type ProgressNode, type ProgressStatus } from '../lib/api'

interface Props {
  planId: number
  refreshToken?: number
}

const STATUS_LABEL: Record<ProgressStatus, string> = {
  not_started: '未开始',
  learning: '学习中',
  done: '已掌握',
}
const STATUS_ORDER: ProgressStatus[] = ['not_started', 'learning', 'done']
const STATUS_STYLE: Record<ProgressStatus, string> = {
  not_started: 'bg-slate-100 text-slate-600',
  learning: 'bg-blue-100 text-blue-700',
  done: 'bg-green-100 text-green-700',
}

export default function ProgressBoard({ planId, refreshToken = 0 }: Props) {
  const [nodes, setNodes] = useState<ProgressNode[]>([])
  const [masteryAvg, setMasteryAvg] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busyNode, setBusyNode] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getProgress(planId)
      .then((r) => {
        if (cancelled) return
        setNodes(r.nodes)
        setMasteryAvg(r.mastery_avg)
        setError('')
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? e.message : '进度加载失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [planId, refreshToken])

  async function setStatus(node: ProgressNode, status: ProgressStatus) {
    if (status === node.status || busyNode) return
    setBusyNode(node.node_id)
    try {
      const r = await updateProgress(planId, node.node_id, status)
      if (r.updated) {
        const next = nodes.map((n) =>
          n.node_id === node.node_id
            ? { ...n, status, mastery: r.mastery ?? n.mastery, next_review: r.next_review ?? n.next_review }
            : n,
        )
        setNodes(next)
        // mastery_avg 随单节点更新重算（与后端同口径：全节点平均）
        setMasteryAvg(
          next.length ? Math.round((next.reduce((s, n) => s + n.mastery, 0) / next.length) * 1000) / 1000 : 0,
        )
      } else {
        setError(r.warning ?? '进度更新被忽略')
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '进度更新失败')
    } finally {
      setBusyNode('')
    }
  }

  if (loading) return <p className="text-sm text-slate-500">进度加载中…</p>
  if (error)
    return (
      <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
        {error}
      </div>
    )
  if (nodes.length === 0) return null // 无知识图谱节点（如降级方案）-> 不显示看板

  const pct = Math.round(masteryAvg * 100)
  return (
    <div>
      <div className="mb-3 flex items-center gap-3">
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
          <div
            className="h-full rounded-full bg-emerald-500 transition-all"
            style={{ width: `${pct}%` }}
            role="progressbar"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="平均掌握度"
          />
        </div>
        <span className="text-sm text-slate-600">平均掌握度 {pct}%</span>
      </div>
      <ul className="space-y-2">
        {nodes.map((n) => (
          <li key={n.node_id} className="flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2">
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{n.name}</span>
            <span className="text-xs text-slate-400" aria-label={`掌握度 ${Math.round(n.mastery * 100)}%`}>
              掌握度 {Math.round(n.mastery * 100)}%
            </span>
            {n.next_review && (
              <span className="text-xs text-slate-400" title="按间隔重复（SM-2-lite）推算">
                复习 {n.next_review}
              </span>
            )}
            <span className="flex gap-1" role="group" aria-label={`${n.name} 状态`}>
              {STATUS_ORDER.map((s) => (
                <button
                  key={s}
                  onClick={() => setStatus(n, s)}
                  disabled={busyNode === n.node_id}
                  aria-pressed={n.status === s}
                  className={`rounded px-2 py-0.5 text-xs ${
                    n.status === s ? STATUS_STYLE[s] + ' font-medium' : 'text-slate-400 hover:bg-slate-50'
                  } disabled:opacity-50`}
                >
                  {STATUS_LABEL[s]}
                </button>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

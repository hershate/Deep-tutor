// 进度看板（P2，plan/06 §五 ProgressBoard 简化版）：嵌入 PlanDetail/Learn 的知识点进度列表。
// 数据源 GET /api/progress/{planId}（knowledge_nodes 合并 progress 行，iter 39 端点）。
// 状态切换 -> POST /api/progress（覆盖写，掌握度由后端 record_progress 计算）。
// iter 346（F10 #6）：知识点点击「验证」-> 取该 node 一道选择题 -> 答对方标「已掌握」，
// 答错回退「学习中」（mastery learning + retrieval practice §八 C：用一道直指核心的题验证掌握）。
// refreshToken 变化时重拉（测验回写后父组件递增触发刷新）。
// 大纲虚拟化（plan/09 §七）：节点数 > VIRTUAL_THRESHOLD 时用 react-window 只渲染可视行。
import { useEffect, useState } from 'react'
import { FixedSizeList, type ListChildComponentProps } from 'react-window'
import { ApiError, generateQuiz, getProgress, updateProgress, type ProgressNode, type ProgressStatus, type QuizItemData } from '../lib/api'
import { SkeletonLines } from './Skeleton'

interface Props {
  planId: number
  refreshToken?: number
  onChanged?: () => void // 状态更新成功后回调（父组件联动刷新）
}

const STATUS_LABEL: Record<ProgressStatus, string> = {
  not_started: '未开始',
  learning: '学习中',
  done: '已掌握',
}
const STATUS_ORDER: ProgressStatus[] = ['not_started', 'learning', 'done']
const STATUS_STYLE: Record<ProgressStatus, string> = {
  not_started: 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300',
  learning: 'bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300',
  done: 'bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300',
}

const VIRTUAL_THRESHOLD = 30
const ROW_HEIGHT = 48
const MAX_LIST_HEIGHT = ROW_HEIGHT * 10

interface RowData {
  nodes: ProgressNode[]
  busyNode: string
  onSetStatus: (node: ProgressNode, status: ProgressStatus) => void
  onVerify: (node: ProgressNode) => void
}

/** 单个知识点行（普通 <li> 与虚拟行共用）。compact=虚拟行（定高，元信息窄屏隐藏）。 */
function NodeItem({ node: n, busyNode, onSetStatus, onVerify, compact }: {
  node: ProgressNode
  busyNode: string
  onSetStatus: (node: ProgressNode, status: ProgressStatus) => void
  onVerify: (node: ProgressNode) => void
  compact?: boolean
}) {
  return (
    <>
      <span className="min-w-0 flex-1 truncate text-sm font-medium">{n.name}</span>
      <span
        className={`text-xs text-slate-500 dark:text-slate-400 ${compact ? 'hidden sm:inline' : ''}`}
        aria-label={`掌握度 ${Math.round(n.mastery * 100)}%`}
      >
        掌握度 <span className="tabular-nums">{Math.round(n.mastery * 100)}%</span>
      </span>
      {n.next_review && (
        <span
          className={`text-xs text-slate-500 dark:text-slate-400 ${compact ? 'hidden md:inline' : ''}`}
          title="按间隔重复（SM-2-lite）推算"
        >
          复习 {n.next_review}
        </span>
      )}
      {/* iter 346（F10 #6）：验证掌握——出一道该知识点选择题，答对方算掌握 */}
      <button
        type="button"
        onClick={() => onVerify(n)}
        disabled={busyNode === n.node_id}
        title="出一道选择题验证掌握（答对标记已掌握）"
        className="shrink-0 rounded px-1.5 py-0.5 text-xs text-emerald-700 transition-colors hover:bg-emerald-50 disabled:opacity-50 dark:text-emerald-300 dark:hover:bg-emerald-950"
      >
        🎯验证
      </button>
      <span className="flex shrink-0 gap-1" role="group" aria-label={`${n.name} 状态`}>
        {STATUS_ORDER.map((s) => (
          <button
            key={s}
            onClick={() => onSetStatus(n, s)}
            disabled={busyNode === n.node_id}
            aria-pressed={n.status === s}
            className={`rounded px-2 py-0.5 text-xs transition-all duration-150 active:scale-95 ${
              n.status === s ? STATUS_STYLE[s] + ' font-medium' : 'text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-700'
            } disabled:opacity-50 disabled:active:scale-100`}
          >
            {STATUS_LABEL[s]}
          </button>
        ))}
      </span>
    </>
  )
}

/** react-window 行渲染器：固定行高、单行布局（名称截断），元信息窄屏隐藏防换行。 */
function VirtualRow({ index, style, data }: ListChildComponentProps<RowData>) {
  const n = data.nodes[index]
  return (
    <div style={style}>
      <div className="mb-2 flex h-10 items-center gap-2 rounded-lg border px-3 dark:border-slate-700">
        <NodeItem node={n} busyNode={data.busyNode} onSetStatus={data.onSetStatus} onVerify={data.onVerify} compact />
      </div>
    </div>
  )
}

/** iter 346（F10 #6）：知识点掌握验证卡——取该 node 一道选择题，答对标 done / 答错回退 learning。 */
function VerifyCard({ planId, node, onDone, onClose }: {
  planId: number
  node: ProgressNode
  onDone: (status: ProgressStatus) => void
  onClose: () => void
}) {
  const [quiz, setQuiz] = useState<QuizItemData | null>(null)
  const [sel, setSel] = useState('')
  const [result, setResult] = useState<{ correct: boolean; explanation: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    setLoading(true)
    setErr('')
    setQuiz(null)
    setSel('')
    setResult(null)
    const ac = new AbortController()
    // generateQuiz 幂等（已生成直接返回）；取该 node 的 choice 题验证掌握
    generateQuiz(planId, ac.signal)
      .then((r) => {
        if (ac.signal.aborted) return
        const q =
          r.quizzes.find((x) => x.node_id === node.node_id && x.type === 'choice' && x.options.length > 0) ?? null
        setQuiz(q)
      })
      .catch((e) => {
        if (!ac.signal.aborted) setErr(e instanceof ApiError ? e.message : '取题失败')
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false)
      })
    return () => ac.abort()
  }, [planId, node.node_id])

  function submit() {
    if (!quiz || !sel || result) return
    const correct = sel === quiz.answer
    setResult({ correct, explanation: quiz.explanation })
    onDone(correct ? 'done' : 'learning') // 答对才标已掌握；答错回退学习中
  }

  return (
    <div className="mb-3 animate-fade-in rounded-lg border border-emerald-200 bg-emerald-50/60 p-3 text-sm dark:border-emerald-800 dark:bg-emerald-950/40">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-medium text-emerald-800 dark:text-emerald-200">🎯 验证「{node.name}」</span>
        <button
          type="button"
          onClick={onClose}
          className="rounded px-2 py-0.5 text-xs hover:bg-emerald-100 dark:hover:bg-emerald-900"
        >
          收起
        </button>
      </div>
      {loading ? (
        <p className="text-slate-500 dark:text-slate-400">正在取验证题…</p>
      ) : err ? (
        <p className="text-red-600 dark:text-red-400">{err}</p>
      ) : !quiz ? (
        <p className="text-slate-500 dark:text-slate-400">
          该知识点暂无选择题（每知识点一题，需先生成测验）。可手动标记状态。
        </p>
      ) : result ? (
        <div>
          <p className={result.correct ? 'text-green-700 dark:text-green-300' : 'text-red-600 dark:text-red-400'}>
            {result.correct ? '✓ 回答正确，已标记为「已掌握」' : '✗ 未答对，已回退为「学习中」'}
          </p>
          {result.explanation && (
            <p className="mt-1 text-slate-600 dark:text-slate-300">解析：{result.explanation}</p>
          )}
        </div>
      ) : (
        <div>
          <p className="mb-2 text-slate-700 dark:text-slate-200">{quiz.question}</p>
          <div className="space-y-1">
            {quiz.options.map((opt) => (
              <label
                key={opt}
                className="flex items-center gap-2 rounded border px-2 py-1 hover:bg-emerald-50 dark:border-slate-600 dark:hover:bg-emerald-900"
              >
                <input
                  type="radio"
                  name={`verify-${node.node_id}`}
                  checked={sel === opt}
                  onChange={() => setSel(opt)}
                />
                <span>{opt}</span>
              </label>
            ))}
          </div>
          <button
            type="button"
            onClick={submit}
            disabled={!sel}
            className="mt-2 rounded bg-emerald-600 px-3 py-1 text-xs text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            提交验证
          </button>
        </div>
      )}
    </div>
  )
}

export default function ProgressBoard({ planId, refreshToken = 0, onChanged }: Props) {
  const [nodes, setNodes] = useState<ProgressNode[]>([])
  const [masteryAvg, setMasteryAvg] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [opError, setOpError] = useState('')
  const [busyNode, setBusyNode] = useState('')
  const [retryTick, setRetryTick] = useState(0) // iter 395（R38）：加载失败重试（refreshToken 联动外的前端自主恢复）
  const [verifyNode, setVerifyNode] = useState<ProgressNode | null>(null) // iter 346（F10 #6）

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
  }, [planId, refreshToken, retryTick])

  async function setStatus(node: ProgressNode, status: ProgressStatus) {
    if (status === node.status || busyNode) return
    setBusyNode(node.node_id)
    setOpError('')
    try {
      const r = await updateProgress(planId, node.node_id, status)
      if (r.updated) {
        const next = nodes.map((n) =>
          n.node_id === node.node_id
            ? { ...n, status, mastery: r.mastery ?? n.mastery, next_review: r.next_review ?? n.next_review }
            : n,
        )
        setNodes(next)
        setMasteryAvg(
          next.length ? Math.round((next.reduce((s, n) => s + n.mastery, 0) / next.length) * 1000) / 1000 : 0,
        )
        onChanged?.()
      } else {
        setOpError(r.warning ?? '进度更新被忽略')
      }
    } catch (e) {
      setOpError(e instanceof ApiError ? e.message : '进度更新失败')
    } finally {
      setBusyNode('')
    }
  }

  if (loading) return <SkeletonLines lines={3} />
  if (error)
    return (
      <div role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950 p-3 text-sm text-red-700 dark:text-red-300">
        {error}
        <button
          onClick={() => setRetryTick((n) => n + 1)}
          className="ml-2 font-medium underline underline-offset-2 hover:text-red-800 dark:hover:text-red-200"
        >
          重试
        </button>
      </div>
    )
  if (nodes.length === 0) return null

  const pct = Math.round(masteryAvg * 100)
  return (
    <div>
      <div className="mb-3 flex items-center gap-3">
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700">
          <div
            className="h-full rounded-full bg-emerald-500 transition-all duration-500 ease-out"
            style={{ width: `${pct}%` }}
            role="progressbar"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="平均掌握度"
          />
        </div>
        <span className="text-sm tabular-nums text-slate-600 dark:text-slate-300">平均掌握度 {pct}%</span>
      </div>
      {opError && (
        <div role="alert" className="mb-3 animate-fade-in rounded-lg border border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950 p-2 text-sm text-red-700 dark:text-red-300">
          {opError}
        </div>
      )}
      {verifyNode && (
        <VerifyCard
          planId={planId}
          node={verifyNode}
          onDone={async (status) => {
            await setStatus(verifyNode, status)
            // 状态写库确认后才收卡（失败时错误条可见、卡片保留供重试）；答错保留卡片显示解析
            if (status === 'done') setVerifyNode(null)
          }}
          onClose={() => setVerifyNode(null)}
        />
      )}
      {nodes.length > VIRTUAL_THRESHOLD ? (
        <FixedSizeList
          height={Math.min(nodes.length * ROW_HEIGHT, MAX_LIST_HEIGHT)}
          itemCount={nodes.length}
          itemSize={ROW_HEIGHT}
          itemData={{ nodes, busyNode, onSetStatus: setStatus, onVerify: setVerifyNode }}
          width="100%"
          className="rounded-lg"
        >
          {VirtualRow}
        </FixedSizeList>
      ) : (
        <ul className="space-y-2">
          {nodes.map((n) => (
            <li key={n.node_id} className="flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2 transition-colors hover:border-slate-300 dark:border-slate-700 dark:hover:border-slate-600">
              <NodeItem node={n} busyNode={busyNode} onSetStatus={setStatus} onVerify={setVerifyNode} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

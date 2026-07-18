// 自学材料弹窗（P2，iter 44：内嵌 StudyMaterialReader 逐单元阅读；iter 47：生成改 SSE 逐单元渐进显示）。
// 打开：GET 单元列表 -> 空则 POST 生成（SSE 逐单元进度；幂等命中返 JSON）-> 再取列表；
// 切换深度：POST {refresh:true} 强制重生成后重取列表；关闭/卸载中止进行中请求。
import { useCallback, useEffect, useRef, useState } from 'react'
import StudyMaterialReader from './StudyMaterialReader'
import { ApiError, generateMaterialsStream, listStudyUnits, type StudyUnitMeta } from '../lib/api'

interface Props {
  planId: number
  onClose: () => void
  onOpenQuiz: () => void
}

/** 逐单元生成进度（SSE unit_start/unit_done 事件驱动）。 */
interface UnitProgress {
  order: number
  title: string
  total: number
  state: 'generating' | 'done' | 'degraded'
}

export default function MaterialsModal({ planId, onClose, onOpenQuiz }: Props) {
  const [depth, setDepth] = useState<'brief' | 'full'>('full')
  const [loading, setLoading] = useState(true)
  const [units, setUnits] = useState<StudyUnitMeta[]>([])
  const [indexMd, setIndexMd] = useState('') // plan/02 §13 index 文档（路线图/计划表/复习节点）
  const [progress, setProgress] = useState<UnitProgress[]>([])
  const [warning, setWarning] = useState('')
  const [error, setError] = useState('')
  const acRef = useRef<AbortController | null>(null)

  const run = useCallback(
    (d: 'brief' | 'full', refresh: boolean) => {
      acRef.current?.abort() // 中止上一次进行中请求
      const ac = new AbortController()
      acRef.current = ac
      setLoading(true)
      setError('')
      setWarning('')
      setProgress([])
      ;(async () => {
        // refresh 或尚无单元 -> 生成（SSE 逐单元进度；幂等命中后端直接返回 JSON）
        await generateMaterialsStream(
          planId,
          d,
          refresh,
          {
            onUnitStart: (u) => {
              if (ac.signal.aborted) return
              setProgress((prev) => [...prev, { order: u.order, title: u.title, total: u.total, state: 'generating' }])
            },
            onUnitDone: (u) => {
              if (ac.signal.aborted) return
              setProgress((prev) =>
                prev.map((p) =>
                  p.order === u.order ? { ...p, state: u.degraded ? 'degraded' : 'done' } : p,
                ),
              )
            },
            onDone: (r) => {
              if (ac.signal.aborted) return
              if (r.degraded_count > 0)
                setWarning(`${r.degraded_count}/${r.unit_count} 个单元生成失败，已降级为简要大纲`)
            },
            onSync: (g) => {
              if (!ac.signal.aborted) setWarning(g.warning ?? '')
            },
          },
          ac.signal,
        )
        const lst = await listStudyUnits(planId, ac.signal)
        if (!ac.signal.aborted) {
          setUnits(lst.units)
          setIndexMd(lst.index_md ?? '')
        }
      })()
        .catch((e) => {
          if (ac.signal.aborted) return
          setError(e instanceof ApiError ? e.message : '生成失败')
        })
        .finally(() => {
          if (!ac.signal.aborted) setLoading(false)
        })
    },
    [planId],
  )

  useEffect(() => {
    // 打开：先查已存单元（幂等免 LLM）；无则按 full 生成
    acRef.current?.abort()
    const ac = new AbortController()
    acRef.current = ac
    setLoading(true)
    listStudyUnits(planId, ac.signal)
      .then((lst) => {
        if (ac.signal.aborted) return
        if (lst.units.length > 0) {
          setUnits(lst.units)
          setIndexMd(lst.index_md ?? '')
          setLoading(false)
        } else {
          run('full', false)
        }
      })
      .catch((e) => {
        if (ac.signal.aborted) return
        setError(e instanceof ApiError ? e.message : '加载失败')
        setLoading(false)
      })
    return () => ac.abort()
  }, [planId, run])

  // Esc 关闭
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose() // 点遮罩关闭
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="自学材料"
        className="flex max-h-[85vh] w-full max-w-4xl flex-col rounded-xl bg-white dark:bg-slate-800 shadow-xl"
      >
        <div className="flex items-center justify-between gap-2 border-b px-4 py-3">
          <h3 className="font-semibold">自学材料</h3>
          <div className="flex items-center gap-2">
            <label htmlFor="materials-depth" className="text-sm text-slate-600 dark:text-slate-300">
              深度
            </label>
            <select
              id="materials-depth"
              value={depth}
              onChange={(e) => {
                const d = e.target.value as 'brief' | 'full'
                setDepth(d)
                run(d, true) // 切换深度 -> 强制重新生成（替换已持久化单元）
              }}
              disabled={loading}
              className="rounded border px-2 py-1 text-sm"
            >
              <option value="full">完整</option>
              <option value="brief">简要</option>
            </select>
            <button
              onClick={onClose}
              aria-label="关闭"
              className="rounded-lg border px-3 py-1 text-sm hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              关闭
            </button>
          </div>
        </div>
        <div className="flex min-h-[40vh] flex-1 flex-col overflow-hidden px-6 py-4">
          {loading && (
            <div>
              <p className="text-slate-500 dark:text-slate-400">生成中…（逐单元组织材料，约需数十秒）</p>
              {progress.length > 0 && (
                <ul aria-live="polite" className="mt-3 space-y-1 text-sm">
                  {progress.map((p) => (
                    <li key={p.order} className="flex items-center gap-2">
                      <span aria-hidden="true">
                        {p.state === 'generating' ? '⏳' : p.state === 'degraded' ? '⚠️' : '✅'}
                      </span>
                      <span className={p.state === 'generating' ? 'text-slate-500 dark:text-slate-400' : ''}>
                        单元 {p.order}/{p.total}：{p.title}
                        <span className="sr-only">
                          {p.state === 'generating' ? '（生成中）' : p.state === 'degraded' ? '（已降级为简要大纲）' : '（已完成）'}
                        </span>
                      </span>
                      {p.state === 'degraded' && <span className="text-amber-600 dark:text-amber-400">已降级为简要大纲</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {error && (
            <div role="alert" className="rounded-lg bg-red-50 dark:bg-red-950 p-3 text-sm text-red-700 dark:text-red-300">
              {error}
            </div>
          )}
          {!loading && !error && (
            <>
              {warning && (
                <div className="mb-3 rounded-lg bg-amber-50 dark:bg-amber-950 p-3 text-sm text-amber-700">{warning}</div>
              )}
              <StudyMaterialReader units={units} indexMd={indexMd} onUnitsChange={setUnits} onOpenQuiz={onOpenQuiz} />
            </>
          )}
        </div>
      </div>
    </div>
  )
}

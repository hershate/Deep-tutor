// 自学材料弹窗（P2，iter 44：内嵌 StudyMaterialReader 逐单元阅读；iter 47：生成改 SSE 逐单元渐进显示）。
// iter 192 F7：生成流纳入 App 级任务引擎（lib/taskEngine.ts）——关弹窗（Esc/遮罩）不再 abort，
// 进度渲染自任务台账；切页/关窗后任务中心「材料 · x/total」持续可见，重开弹窗进度连续。
// 刷新恢复：材料单元逐单元落库，重开时若台账显示 running 但本页无活动流（曾刷新），
// 提示「后台生成可能已完成」+ 一键重读列表（重读即真相）。
import { useCallback, useEffect, useRef, useState } from 'react'
import StudyMaterialReader from './StudyMaterialReader'
import { toast } from './Toast'
import { ApiError, downloadMaterialsZip, listStudyUnits, type StudyUnitMeta } from '../lib/api'
import { engine } from '../lib/taskEngine'
import { materialsTaskId, useTaskStore } from '../store/tasks'

interface Props {
  planId: number
  planTitle?: string // 任务台账标题（任务中心可读性）
  onClose: () => void
  onOpenQuiz: () => void
}

export default function MaterialsModal({ planId, planTitle, onClose, onOpenQuiz }: Props) {
  // iter 145：深度偏好记忆——上次选用的深度作为下次默认值（localStorage）
  const [depth, setDepth] = useState<'brief' | 'full'>(() => {
    const d = localStorage.getItem('dt-materials-depth')
    return d === 'brief' || d === 'full' ? d : 'full'
  })
  const taskId = materialsTaskId(planId)
  const task = useTaskStore((s) => s.tasks[taskId])
  const removeTask = useTaskStore((s) => s.remove)
  const [listLoading, setListLoading] = useState(true)
  const [units, setUnits] = useState<StudyUnitMeta[]>([])
  const [indexMd, setIndexMd] = useState('') // plan/02 §13 index 文档（路线图/计划表/复习节点）
  const [warning, setWarning] = useState('')
  const [error, setError] = useState('')
  const [selectMode, setSelectMode] = useState(false) // iter 98：按单元选择重生成模式
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [zipBusy, setZipBusy] = useState(false) // iter 99：全案 zip 导出中

  const running = task?.phase === 'running'
  const loading = listLoading || running
  // 曾刷新：台账 running 但本页无活动流（引擎材料流不跨刷新续接；单元已逐单元落库）
  const staleRunning = running && !engine.isLive(taskId)

  const taskTitle = `${planTitle ? `${planTitle} · ` : ''}材料 #${planId}`

  const refetchUnits = useCallback(
    async (signal?: AbortSignal) => {
      const lst = await listStudyUnits(planId, signal)
      setUnits(lst.units)
      setIndexMd(lst.index_md ?? '')
      return lst
    },
    [planId],
  )

  async function doExportZip() {
    if (zipBusy) return
    setZipBusy(true)
    try {
      await downloadMaterialsZip(planId)
      toast.success('自学材料 zip 已开始下载')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '导出失败，请重试')
    } finally {
      setZipBusy(false)
    }
  }

  const startGeneration = useCallback(
    (d: 'brief' | 'full', refresh: boolean, unitIds?: number[]) => {
      setError('')
      setWarning('')
      engine.startMaterials(planId, { depth: d, refresh, unitIds, title: taskTitle })
    },
    [planId, taskTitle],
  )

  // 打开：先查已存单元（幂等免 LLM）；无单元且无进行中任务 -> 按 full 生成
  useEffect(() => {
    const ac = new AbortController()
    setListLoading(true)
    listStudyUnits(planId, ac.signal)
      .then((lst) => {
        if (ac.signal.aborted) return
        setUnits(lst.units)
        setIndexMd(lst.index_md ?? '')
        const t = useTaskStore.getState().tasks[materialsTaskId(planId)]
        if (lst.units.length === 0 && t?.phase !== 'running') {
          engine.startMaterials(planId, { depth: 'full', refresh: false, title: taskTitle })
        }
      })
      .catch((e) => {
        if (ac.signal.aborted) return
        setError(e instanceof ApiError ? e.message : '加载失败')
      })
      .finally(() => {
        if (!ac.signal.aborted) setListLoading(false)
      })
    return () => ac.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planId])

  // 引擎流完成（running -> done）：重读单元列表（逐单元落库，重读即真相）；降级计数提示
  const phase = task?.phase
  const prevPhaseRef = useRef(phase)
  useEffect(() => {
    if (prevPhaseRef.current === 'running' && phase === 'done') {
      const degraded = (task?.materialsUnits ?? []).filter((u) => u.status === 'degraded').length
      refetchUnits()
        .then(() => {
          setSelectMode(false)
          setSelected(new Set())
          if (degraded > 0) setWarning(`${degraded} 个单元生成失败，已降级为简要大纲`)
        })
        .catch((e) => setError(e instanceof ApiError ? e.message : '加载失败'))
    }
    prevPhaseRef.current = phase
  }, [phase, task?.materialsUnits, refetchUnits])

  // 引擎流失败（running -> error）：错误展示取台账
  useEffect(() => {
    if (prevPhaseRef.current === 'running' && phase === 'error') {
      setError(task?.errorMessage || '生成失败，请重试')
    }
  }, [phase, task?.errorMessage])

  // Esc 关闭
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // 曾刷新后的「刷新查看」：重读列表；已有单元 -> 任务完成，清台账条目
  async function onRefreshAfterStale() {
    try {
      const lst = await refetchUnits()
      if (lst.units.length > 0) removeTask(taskId)
      else toast.info('还没有生成完成的单元，可稍后再次刷新查看')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '加载失败')
    }
  }

  const progressUnits = task?.materialsUnits ?? []
  const progressTotal = task?.materialsTotal ?? 0

  return (
    <div
      className="fixed inset-0 z-50 flex animate-fade-in items-center justify-center bg-black/40 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose() // 点遮罩关闭（生成由引擎续跑，不中断）
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="自学材料"
        className="flex max-h-[85vh] w-full max-w-4xl animate-scale-in flex-col rounded-xl bg-white shadow-xl dark:bg-slate-800"
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
                localStorage.setItem('dt-materials-depth', d) // iter 145：记忆偏好
                startGeneration(d, true) // 切换深度 -> 强制重新生成（替换已持久化单元）
              }}
              disabled={loading}
              className="rounded-lg border px-2 py-1 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50 dark:border-slate-600 dark:bg-slate-900"
            >
              <option value="full">完整</option>
              <option value="brief">简要</option>
            </select>
            <button
              onClick={doExportZip}
              disabled={loading || units.length === 0 || zipBusy}
              className="rounded-lg border px-3 py-1 text-sm transition-all hover:bg-slate-50 active:scale-95 disabled:opacity-40 dark:border-slate-600 dark:hover:bg-slate-700"
            >
              {zipBusy ? '导出中…' : '导出全部 (.zip)'}
            </button>
            <button
              onClick={() => {
                setSelectMode((v) => !v)
                setSelected(new Set())
              }}
              disabled={loading || units.length === 0}
              aria-pressed={selectMode}
              className={`rounded-lg border px-3 py-1 text-sm transition-all active:scale-95 disabled:opacity-40 ${
                selectMode ? 'border-blue-500 bg-blue-50 text-blue-700 dark:border-blue-500 dark:bg-blue-950 dark:text-blue-300' : 'hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700'
              }`}
            >
              选择重生成
            </button>
            <button
              onClick={onClose}
              aria-label="关闭"
              className="rounded-lg border px-3 py-1 text-sm transition-all hover:bg-slate-50 active:scale-95 disabled:opacity-40 dark:border-slate-600 dark:hover:bg-slate-700"
            >
              关闭
            </button>
          </div>
        </div>
        <div className="flex min-h-[40vh] flex-1 flex-col overflow-hidden px-6 py-4">
          {staleRunning && (
            <div role="status" className="mb-3 animate-fade-in rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-200">
              页面曾刷新，后台生成可能已完成（材料逐单元保存）。
              <button onClick={onRefreshAfterStale} className="ml-2 font-medium underline underline-offset-2 hover:text-blue-900 dark:hover:text-blue-100">
                刷新查看
              </button>
            </div>
          )}
          {loading && !staleRunning && (
            <div>
              <p className="flex items-center gap-2 text-slate-500 dark:text-slate-400">
                <span aria-hidden="true" className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600 dark:border-slate-600 dark:border-t-blue-400" />
                生成中…（逐单元组织材料，约需数十秒；关闭弹窗不会中断，任务中心可随时查看进度）
              </p>
              {progressUnits.length > 0 && (
                <ul aria-live="polite" className="mt-3 space-y-1 text-sm">
                  {progressUnits.map((p) => (
                    <li key={p.order} className="flex animate-fade-in items-center gap-2">
                      <span aria-hidden="true" className="flex w-4 items-center justify-center">
                        {p.status === 'running' ? (
                          <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600 dark:border-slate-600 dark:border-t-blue-400" />
                        ) : p.status === 'degraded' ? (
                          '⚠️'
                        ) : (
                          '✅'
                        )}
                      </span>
                      <span className={p.status === 'running' ? 'text-slate-500 dark:text-slate-400' : ''}>
                        单元 {p.order}/{progressTotal}：{p.title}
                        <span className="sr-only">
                          {p.status === 'running' ? '（生成中）' : p.status === 'degraded' ? '（已降级为简要大纲）' : '（已完成）'}
                        </span>
                      </span>
                      {p.status === 'degraded' && <span className="text-amber-600 dark:text-amber-400">已降级为简要大纲</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {error && !running && (
            <div role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
              {error}
              {task?.phase === 'error' && (
                <button
                  onClick={() => startGeneration(depth, true)}
                  className="ml-2 font-medium underline underline-offset-2 hover:text-red-800 dark:hover:text-red-200"
                >
                  重试生成
                </button>
              )}
            </div>
          )}
          {!loading && !error && selectMode && (
            <div className="flex animate-fade-in flex-col gap-3">
              <p className="text-sm text-slate-500 dark:text-slate-400">
                勾选要重新生成的单元——只有选中的单元会被替换，其余单元内容保持不变：
              </p>
              <ul className="max-h-[45vh] space-y-1 overflow-y-auto rounded-lg border p-2 dark:border-slate-700">
                {units.map((u) => (
                  <li key={u.id}>
                    <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm transition-colors hover:bg-slate-50 dark:hover:bg-slate-700">
                      <input
                        type="checkbox"
                        checked={selected.has(u.id)}
                        onChange={() =>
                          setSelected((prev) => {
                            const next = new Set(prev)
                            if (next.has(u.id)) next.delete(u.id)
                            else next.add(u.id)
                            return next
                          })
                        }
                        className="accent-blue-600"
                      />
                      <span className="min-w-0 flex-1 truncate">{u.title}</span>
                      {u.done && <span className="text-xs text-green-600 dark:text-green-400">✓ 已学</span>}
                    </label>
                  </li>
                ))}
              </ul>
              <div className="flex flex-wrap items-center gap-2 border-t pt-3 dark:border-slate-700">
                <button
                  onClick={() =>
                    setSelected((prev) => (prev.size === units.length ? new Set() : new Set(units.map((u) => u.id))))
                  }
                  className="rounded-lg border px-3 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
                >
                  {selected.size === units.length ? '清空' : '全选'}
                </button>
                <button
                  onClick={() => startGeneration(depth, false, [...selected])}
                  disabled={selected.size === 0}
                  className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98] disabled:opacity-40 disabled:hover:shadow-sm disabled:active:scale-100"
                >
                  重新生成选中单元（<span className="tabular-nums">{selected.size}</span>）
                </button>
                <button
                  onClick={() => {
                    setSelectMode(false)
                    setSelected(new Set())
                  }}
                  className="rounded-lg border px-3 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
                >
                  取消
                </button>
              </div>
            </div>
          )}
          {!loading && !error && !selectMode && (
            <>
              {warning && (
                <div className="mb-3 animate-fade-in rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-700 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">{warning}</div>
              )}
              <StudyMaterialReader units={units} indexMd={indexMd} onUnitsChange={setUnits} onOpenQuiz={onOpenQuiz} />
            </>
          )}
        </div>
      </div>
    </div>
  )
}

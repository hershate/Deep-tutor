// 任务中心（iter 188 F3，用户已定双形态之一）：顶栏指示器按钮 + 点击展开下拉面板。
// 窄屏/侧栏折叠态的唯一任务入口；桌面侧栏展开态另有常驻 TaskPanel（TaskPanel.tsx）。
// 同时负责 document.title 角标：活动任务 n>0 -> `(n) Deep-tutor`，归零恢复。
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { engine } from '../lib/taskEngine'
import {
  isActivePhase,
  isTerminalPhase,
  useTaskStore,
  type TaskEntry,
  type TaskPhase,
} from '../store/tasks'
import { toast } from './Toast'

/** 生成管线工具 -> 中文步骤名（8 工具，iter 184 B2 step_index 对齐）。 */
export const TOOL_LABELS: Record<string, string> = {
  parse_material: '解析资料',
  understand_intent: '理解学习目标',
  build_knowledge_map: '构建知识图谱',
  design_stages: '设计学习阶段',
  search_resources: '搜索学习资源',
  schedule_plan: '编排日程',
  validate_plan: '校验方案',
  export_plan: '导出方案',
}

export const PHASE_META: Record<TaskPhase, { label: string; badge: string }> = {
  running: { label: '进行中', badge: 'bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300' },
  clarify: { label: '待澄清', badge: 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300' },
  done: { label: '已完成', badge: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300' },
  error: { label: '失败', badge: 'bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300' },
  cancelled: { label: '已取消', badge: 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400' },
  interrupted: { label: '已中断', badge: 'bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300' },
}

export function fmtElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return s % 60 ? `${m}m${s % 60}s` : `${m}m`
  return `${Math.floor(m / 60)}h${m % 60}m`
}

/** 进度：方案=已完成步骤（按 stepIndex 去重，反馈循环重跑不重复计）/总步数；材料=单元 x/total。 */
export function taskProgress(t: TaskEntry): { done: number; total: number } | null {
  if (t.kind === 'materials') {
    const total = t.materialsTotal ?? 0
    if (total <= 0) return null
    const done = (t.materialsUnits ?? []).filter((u) => u.status !== 'running').length
    return { done: Math.min(done, total), total }
  }
  const total = t.steps.reduce((m, s) => Math.max(m, s.totalSteps || 0), 0) || 8
  const doneIdx = new Set(t.steps.filter((s) => s.status === 'done').map((s) => s.stepIndex))
  return { done: Math.min(doneIdx.size, total), total }
}

function useNow(tickMs: number | null): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (tickMs == null) return
    const t = setInterval(() => setNow(Date.now()), tickMs)
    return () => clearInterval(t)
  }, [tickMs])
  return now
}

export function sortedTasks(tasks: Record<string, TaskEntry>): TaskEntry[] {
  return Object.values(tasks).sort((a, b) => {
    const act = Number(isActivePhase(b.phase)) - Number(isActivePhase(a.phase))
    return act !== 0 ? act : b.updatedAt - a.updatedAt
  })
}

/** 单任务行（任务中心下拉面板 / 侧栏 TaskPanel 复用）。 */
export function TaskRow({ task, onNavigate }: { task: TaskEntry; onNavigate?: () => void }) {
  const navigate = useNavigate()
  const remove = useTaskStore((s) => s.remove)
  const now = useNow(task.phase === 'running' ? 1000 : null)
  const meta = PHASE_META[task.phase]
  const progress = taskProgress(task)
  const pct = progress && progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0
  const currentStep = [...task.steps].reverse().find((s) => s.status === 'running')

  const go = (path: string) => {
    onNavigate?.()
    navigate(path)
  }
  const openTarget = () => {
    if (task.kind === 'materials') return go(`/plans/${task.planId}?materials=1`)
    if (task.phase === 'clarify') return go(`/clarify/${task.sessionId}`)
    return go(`/generate/${task.sessionId}`)
  }
  const onCancel = () => {
    if (task.sessionId == null) return
    if (!window.confirm(`确定取消「${task.title || '学习方案'}」的生成吗？已产生的费用不会退回。`)) return
    void engine.cancel(task.sessionId).catch((e: unknown) => {
      toast.error(e instanceof Error ? e.message : '取消失败，请重试')
    })
  }
  const onRetry = () => {
    if (task.sessionId == null) return
    // start_rejected（启动即败，服务端 idle）：走 startPlan 重新发起而非 retry（retry 必 409）
    const p =
      task.errorCode === 'start_rejected' && task.rawText
        ? engine.startPlan(task.sessionId, task.rawText)
        : engine.retry(task.sessionId)
    void p.catch((e: unknown) => {
      toast.error(e instanceof Error ? e.message : '重试失败，请稍后再试')
    })
    go(`/generate/${task.sessionId}`)
  }

  return (
    <div className="rounded-lg border px-2.5 py-2 text-sm dark:border-slate-700">
      <div className="flex items-center gap-2">
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs ${meta.badge}`}>{meta.label}</span>
        <button
          type="button"
          onClick={openTarget}
          className="min-w-0 flex-1 truncate text-left font-medium text-slate-700 transition-colors hover:text-blue-600 dark:text-slate-200 dark:hover:text-blue-400"
          title={task.title || '未命名任务'}
        >
          {task.kind === 'materials' ? '📄 ' : ''}
          {task.title || '未命名任务'}
        </button>
        {task.phase === 'running' && task.kind === 'plan' && (
          <button type="button" onClick={onCancel} className="shrink-0 text-xs text-slate-400 underline underline-offset-2 hover:text-red-600 dark:hover:text-red-400">
            取消
          </button>
        )}
        {(task.phase === 'error' || task.phase === 'interrupted' || task.phase === 'cancelled') && task.kind === 'plan' && (
          <button type="button" onClick={onRetry} className="shrink-0 text-xs text-blue-600 underline underline-offset-2 hover:text-blue-700 dark:text-blue-400">
            重试
          </button>
        )}
        {isTerminalPhase(task.phase) && (
          <button
            type="button"
            onClick={() => remove(task.id)}
            aria-label="移除该任务记录"
            className="shrink-0 text-xs text-slate-300 transition-colors hover:text-slate-500 dark:text-slate-600 dark:hover:text-slate-400"
          >
            ✕
          </button>
        )}
      </div>
      {task.phase === 'running' && (
        <div className="mt-1.5">
          {progress && (
            <div
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`进度 ${pct}%`}
              className="h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700"
            >
              <div className="h-full rounded-full bg-blue-500 transition-all duration-500" style={{ width: `${pct}%` }} />
            </div>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-slate-400">
            {task.conn === 'reconnecting' && <span className="text-amber-500">连接中断，重连中…</span>}
            {task.conn === 'offline' && <span className="text-orange-500">连接已断开（后台仍在生成）</span>}
            {task.conn !== 'reconnecting' && task.conn !== 'offline' && (
              <>
                {progress && <span>{task.kind === 'materials' ? `单元 ${progress.done}/${progress.total}` : `${progress.done}/${progress.total} 步`}</span>}
                {task.kind === 'plan' && currentStep && <span>{TOOL_LABELS[currentStep.tool] ?? currentStep.tool}</span>}
                {task.kind === 'materials' && (task.materialsUnits ?? []).find((u) => u.status === 'running') && (
                  <span className="truncate">{(task.materialsUnits ?? []).find((u) => u.status === 'running')?.title}</span>
                )}
              </>
            )}
            <span>{fmtElapsed(now - task.startedAt)}</span>
            {task.kind === 'plan' && task.cost > 0 && <span>${task.cost.toFixed(4)}</span>}
          </div>
        </div>
      )}
      {task.phase !== 'running' && (
        <div className="mt-1 truncate text-xs text-slate-400">
          {task.phase === 'error' && (task.errorMessage || '生成失败')}
          {task.phase === 'interrupted' && (task.errorMessage || '已中断，可重试')}
          {task.phase === 'cancelled' && '已取消，可重试'}
          {task.phase === 'clarify' && `${(task.questions ?? []).length || '几'} 个问题待回答`}
          {task.phase === 'done' && (task.kind === 'materials' ? `共 ${task.materialsTotal ?? '?'} 个单元` : '点击查看方案')}
        </div>
      )}
    </div>
  )
}

/** 顶栏任务中心：指示器按钮（活动计数 + 呼吸点）+ 下拉面板；兼管 document.title 角标。 */
export default function TaskCenter() {
  const tasks = useTaskStore((s) => s.tasks)
  const clearFinished = useTaskStore((s) => s.clearFinished)
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const entries = sortedTasks(tasks)
  const activeCount = entries.filter((t) => isActivePhase(t.phase)).length
  const hasFinished = entries.some((t) => isTerminalPhase(t.phase))

  // document.title 角标（iter 188 F3，用户已定：仅应用内通知 + 标题角标）
  useEffect(() => {
    document.title = activeCount > 0 ? `(${activeCount}) Deep-tutor` : 'Deep-tutor'
    return () => {
      document.title = 'Deep-tutor'
    }
  }, [activeCount])

  // 外点/Esc 关闭下拉
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={`任务中心${activeCount > 0 ? `，${activeCount} 个活动任务` : ''}`}
        aria-expanded={open}
        className={`relative rounded-lg border px-2 py-1 text-sm transition-colors hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700 ${
          activeCount > 0 ? 'text-blue-600 dark:text-blue-400' : 'text-slate-400 dark:text-slate-500'
        }`}
      >
        <span aria-hidden="true">⏳</span>
        {activeCount > 0 && <span className="ml-1 tabular-nums">{activeCount}</span>}
        {activeCount > 0 && (
          <span aria-hidden="true" className="absolute -right-0.5 -top-0.5 flex h-2 w-2">
            <span className="absolute h-full w-full animate-ping rounded-full bg-blue-400 opacity-75" />
            <span className="h-2 w-2 rounded-full bg-blue-500" />
          </span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-2 w-80 animate-scale-in rounded-xl border bg-white p-3 shadow-xl dark:border-slate-700 dark:bg-slate-800">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-semibold">任务中心</span>
            {activeCount > 0 && <span className="text-xs text-slate-400">{activeCount} 个进行中</span>}
          </div>
          {entries.length === 0 ? (
            <p className="py-4 text-center text-sm text-slate-400">暂无任务</p>
          ) : (
            <div className="flex max-h-96 flex-col gap-2 overflow-y-auto">
              {entries.map((t) => (
                <TaskRow key={t.id} task={t} onNavigate={() => setOpen(false)} />
              ))}
            </div>
          )}
          {hasFinished && (
            <button
              type="button"
              onClick={clearFinished}
              className="mt-2 w-full rounded-lg border px-2 py-1 text-xs text-slate-400 transition-colors hover:bg-slate-50 hover:text-slate-600 dark:border-slate-600 dark:hover:bg-slate-700"
            >
              清空已完成记录
            </button>
          )}
        </div>
      )}
    </div>
  )
}

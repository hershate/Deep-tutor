// 侧栏常驻任务面板（iter 188 F3，用户已定双形态之二）：桌面侧栏展开态（md+ 且 !collapsed）
// 常驻显示，导航下方、sidebar.footer 插槽上方；窄屏/折叠态由顶栏 TaskCenter 承接。
// 与 TaskCenter 复用同一 TaskRow。
import { isActivePhase, isTerminalPhase, useTaskStore } from '../store/tasks'
import { TaskRow, sortedTasks } from './TaskCenter'

export default function TaskPanel() {
  const tasks = useTaskStore((s) => s.tasks)
  const clearFinished = useTaskStore((s) => s.clearFinished)
  const entries = sortedTasks(tasks)
  const activeCount = entries.filter((t) => isActivePhase(t.phase)).length
  const hasFinished = entries.some((t) => isTerminalPhase(t.phase))

  return (
    <section aria-label="任务面板" className="mt-4 border-t pt-3 dark:border-slate-700">
      <div className="mb-1.5 flex items-center justify-between px-1">
        <span className="text-xs font-medium text-slate-400 dark:text-slate-500">任务</span>
        {activeCount > 0 && <span className="text-xs text-blue-500 dark:text-blue-400">{activeCount} 进行中</span>}
      </div>
      {entries.length === 0 ? (
        <p className="px-1 text-xs text-slate-400 dark:text-slate-500">暂无任务</p>
      ) : (
        <div className="flex flex-col gap-2">
          {entries.map((t) => (
            <TaskRow key={t.id} task={t} />
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
    </section>
  )
}

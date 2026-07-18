// 日程时间线（plan/06 §四.4 时间线 Tab / §五 Timeline 组件「按日期分组」）：
// schedule 按相对周分组（第 N 周），逐日列出阶段/任务/分钟；头部汇总起止与总时长。
import { useMemo } from 'react'

export interface ScheduleDay {
  date: string
  stage_index?: number
  tasks?: string[]
  est_minutes?: number
}

interface Props {
  schedule: ScheduleDay[]
  stageNames?: string[] // stage_index -> 阶段名
}

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

export default function Timeline({ schedule, stageNames = [] }: Props) {
  const days = useMemo(
    () => [...schedule].filter((d) => d.date).sort((a, b) => a.date.localeCompare(b.date)),
    [schedule],
  )

  const weeks = useMemo(() => {
    if (days.length === 0) return []
    const first = new Date(days[0].date + 'T00:00:00').getTime()
    const groups = new Map<number, ScheduleDay[]>()
    for (const d of days) {
      const t = new Date(d.date + 'T00:00:00').getTime()
      const week = Math.floor((t - first) / (7 * 86400_000)) + 1
      groups.set(week, [...(groups.get(week) ?? []), d])
    }
    return [...groups.entries()].sort((a, b) => a[0] - b[0])
  }, [days])

  if (days.length === 0) {
    return <p className="text-sm text-slate-500 dark:text-slate-400">暂无日程数据（方案未含 schedule）。</p>
  }

  const totalMin = days.reduce((s, d) => s + (d.est_minutes ?? 0), 0)
  const hours = Math.round((totalMin / 60) * 10) / 10

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600 dark:text-slate-300">
        共 {days.length} 个学习日 · 约 {hours} 小时 · {days[0].date} ~ {days[days.length - 1].date}
      </p>
      {weeks.map(([week, ds]) => (
        <section key={week} aria-label={`第 ${week} 周`}>
          <h4 className="mb-1.5 text-sm font-semibold text-slate-500 dark:text-slate-400">第 {week} 周</h4>
          <ul className="space-y-1.5">
            {ds.map((d) => {
              const t = new Date(d.date + 'T00:00:00')
              const weekday = Number.isNaN(t.getTime()) ? '' : `周${WEEKDAYS[t.getDay()]}`
              const stageName = d.stage_index !== undefined ? stageNames[d.stage_index] : undefined
              return (
                <li
                  key={d.date}
                  className="flex flex-wrap items-baseline gap-x-3 rounded-lg border bg-white dark:border-slate-700 dark:bg-slate-800 px-3 py-2 text-sm"
                >
                  <span className="shrink-0 font-medium text-slate-700 dark:text-slate-200">
                    {d.date.slice(5)} {weekday}
                  </span>
                  {stageName && (
                    <span className="shrink-0 rounded bg-indigo-50 dark:bg-indigo-950 px-1.5 py-0.5 text-xs text-indigo-700 dark:text-indigo-300">
                      {stageName}
                    </span>
                  )}
                  <span className="min-w-0 flex-1 text-slate-600 dark:text-slate-300">{(d.tasks ?? []).join('、')}</span>
                  {d.est_minutes !== undefined && (
                    <span className="shrink-0 text-xs text-slate-400">{d.est_minutes} 分钟</span>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      ))}
    </div>
  )
}

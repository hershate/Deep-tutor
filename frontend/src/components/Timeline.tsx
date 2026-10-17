// 日程时间线（plan/06 §四.4 时间线 Tab / §五 Timeline 组件「按日期分组」）：
// schedule 按相对周分组（第 N 周），逐日列出阶段/任务/分钟；头部汇总起止与总时长。
import { useMemo } from 'react'
import EmptyState from './EmptyState'

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
    // iter 371：过滤非法日期串（LLM 产物可能带 '2025/06/01' 等非 ISO 串 -> NaN），
    // 否则全部聚到同一 NaN 周键下且渲染「第 NaN 周」（weekday 守卫同口径）
    const valid = days.filter((d) => !Number.isNaN(new Date(d.date + 'T00:00:00').getTime()))
    if (valid.length === 0) return []
    const first = new Date(valid[0].date + 'T00:00:00').getTime()
    const groups = new Map<number, ScheduleDay[]>()
    for (const d of valid) {
      const t = new Date(d.date + 'T00:00:00').getTime()
      const week = Math.floor((t - first) / (7 * 86400_000)) + 1
      groups.set(week, [...(groups.get(week) ?? []), d])
    }
    return [...groups.entries()].sort((a, b) => a[0] - b[0])
  }, [days])

  // iter 296：今天定位——高亮今日条目；今日未排程则标首个「下一」即将开始项，便于在排程中定位当前位置
  const todayStr = useMemo(() => {
    const d = new Date()
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  }, [])
  const nextDate = useMemo(() => {
    const upcoming = days.filter((d) => d.date >= todayStr)
    return upcoming.length > 0 ? upcoming[0].date : null // 首个「今天及以后」
  }, [days, todayStr])

  if (days.length === 0) {
    return <EmptyState icon="🗓️" title="暂无日程数据" description="方案未包含 schedule 部分" />
  }

  const totalMin = days.reduce((s, d) => s + (d.est_minutes ?? 0), 0)
  const hours = Math.round((totalMin / 60) * 10) / 10

  return (
    <div className="space-y-4">
      <p className="text-sm tabular-nums text-slate-600 dark:text-slate-300">
        共 {days.length} 个学习日 · 约 {hours} 小时 · {days[0].date} ~ {days[days.length - 1].date}
      </p>
      {weeks.map(([week, ds], wi) => (
        <section key={week} aria-label={`第 ${week} 周`} className="animate-fade-in-up" style={{ animationDelay: `${Math.min(wi, 6) * 70}ms` }}>
          <h4 className="mb-1.5 text-sm font-semibold text-slate-500 dark:text-slate-400">第 {week} 周</h4>
          <ul className="space-y-1.5">
            {ds.map((d) => {
              const t = new Date(d.date + 'T00:00:00')
              const weekday = Number.isNaN(t.getTime()) ? '' : `周${WEEKDAYS[t.getDay()]}`
              const stageName = d.stage_index !== undefined ? stageNames[d.stage_index] : undefined
              const isToday = d.date === todayStr
              const isNext = !isToday && d.date === nextDate
              const dateCls = isToday
                ? 'border-blue-400 bg-blue-50 ring-1 ring-blue-300 dark:border-blue-700 dark:bg-blue-950 dark:ring-blue-800'
                : isNext
                  ? 'border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/50'
                  : 'border-slate-200 bg-white hover:border-blue-200 hover:shadow-sm dark:border-slate-700 dark:bg-slate-800 dark:hover:border-blue-900'
              return (
                <li
                  key={d.date}
                  className={`flex flex-wrap items-baseline gap-x-3 rounded-lg px-3 py-2 text-sm transition-all duration-150 ${dateCls}`}
                >
                  <span className="shrink-0 font-medium text-slate-700 dark:text-slate-200">
                    {d.date.slice(5)} {weekday}
                  </span>
                  {isToday && (
                    <span className="shrink-0 rounded bg-blue-600 px-1.5 py-0.5 text-xs font-medium text-white">今天</span>
                  )}
                  {isNext && (
                    <span className="shrink-0 rounded bg-amber-500 px-1.5 py-0.5 text-xs font-medium text-white">下一</span>
                  )}
                  {stageName && (
                    <span className="shrink-0 rounded bg-indigo-50 dark:bg-indigo-950 px-1.5 py-0.5 text-xs text-indigo-700 dark:text-indigo-300">
                      {stageName}
                    </span>
                  )}
                  <span className="min-w-0 flex-1 text-slate-600 dark:text-slate-300">{(d.tasks ?? []).join('、')}</span>
                  {d.est_minutes !== undefined && (
                    <span className="shrink-0 text-xs tabular-nums text-slate-400">{d.est_minutes} 分钟</span>
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

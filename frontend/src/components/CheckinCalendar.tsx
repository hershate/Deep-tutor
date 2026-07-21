// 打卡日历（plan/06 §五 学习中心「打卡日历」）：月历网格展示 learning_logs，
// 按分钟数分档着色，支持前后翻月；本月汇总（打卡天数/总分钟）。
import { useMemo, useState } from 'react'
import type { LearningLogItem } from '../lib/api'

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']

/** 分钟数 -> 色档（0 无打卡）。 */
function levelClass(minutes: number): string {
  if (minutes <= 0) return 'bg-slate-100 text-slate-400 dark:bg-slate-700 dark:text-slate-500'
  if (minutes <= 30) return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-200'
  if (minutes <= 90) return 'bg-emerald-300 text-emerald-900 dark:bg-emerald-700 dark:text-emerald-100'
  return 'bg-emerald-500 text-white dark:bg-emerald-500'
}

export default function CheckinCalendar({ logs }: { logs: LearningLogItem[] }) {
  const now = new Date()
  const [year, setYear] = useState(now.getFullYear())
  const [month, setMonth] = useState(now.getMonth()) // 0-based

  // date(YYYY-MM-DD) -> 当日总分钟（同日多次打卡合并）
  const minutesByDate = useMemo(() => {
    const m = new Map<string, number>()
    for (const l of logs) {
      m.set(l.date, (m.get(l.date) ?? 0) + l.minutes)
    }
    return m
  }, [logs])

  const cells = useMemo(() => {
    const first = new Date(year, month, 1)
    const daysInMonth = new Date(year, month + 1, 0).getDate()
    // 周一为一周起点：getDay() 周日=0 -> 偏移 6
    const offset = (first.getDay() + 6) % 7
    const list: Array<{ day: number; date: string; minutes: number } | null> = []
    for (let i = 0; i < offset; i++) list.push(null)
    for (let d = 1; d <= daysInMonth; d++) {
      const date = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`
      list.push({ day: d, date, minutes: minutesByDate.get(date) ?? 0 })
    }
    return list
  }, [year, month, minutesByDate])

  const monthStats = useMemo(() => {
    let days = 0
    let minutes = 0
    for (const c of cells) {
      if (c && c.minutes > 0) {
        days++
        minutes += c.minutes
      }
    }
    return { days, minutes }
  }, [cells])

  function shift(delta: number) {
    const t = new Date(year, month + delta, 1)
    setYear(t.getFullYear())
    setMonth(t.getMonth())
  }

  const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`

  return (
    <div aria-label="打卡日历">
      <div className="mb-2 flex items-center justify-between">
        <button
          type="button"
          onClick={() => shift(-1)}
          aria-label="上一月"
          className="rounded px-2 py-1 text-sm text-slate-500 transition-all hover:bg-slate-100 active:scale-90 dark:text-slate-400 dark:hover:bg-slate-700"
        >
          ◀
        </button>
        <span className="text-sm font-medium">
          {year} 年 {month + 1} 月
          <span className="ml-2 text-xs font-normal tabular-nums text-slate-400">
            打卡 {monthStats.days} 天 · {monthStats.minutes} 分钟
          </span>
        </span>
        <button
          type="button"
          onClick={() => shift(1)}
          aria-label="下一月"
          className="rounded px-2 py-1 text-sm text-slate-500 transition-all hover:bg-slate-100 active:scale-90 dark:text-slate-400 dark:hover:bg-slate-700"
        >
          ▶
        </button>
      </div>
      {/* iter 96：翻月淡入 + 格子过渡（有打卡的格子 hover 微放大） */}
      <div key={`${year}-${month}`} className="grid animate-fade-in grid-cols-7 gap-1 text-center text-xs">
        {WEEKDAYS.map((w) => (
          <div key={w} className="py-1 text-slate-400">
            {w}
          </div>
        ))}
        {cells.map((c, i) =>
          c === null ? (
            <div key={`pad-${i}`} />
          ) : (
            <div
              key={c.date}
              title={c.minutes > 0 ? `${c.date}：${c.minutes} 分钟` : c.date}
              aria-label={c.minutes > 0 ? `${c.date} 打卡 ${c.minutes} 分钟` : `${c.date} 未打卡`}
              className={`rounded py-1.5 transition-transform duration-150 ${levelClass(c.minutes)} ${
                c.minutes > 0 ? 'hover:scale-110' : ''
              } ${c.date === todayStr ? 'ring-2 ring-blue-500' : ''}`}
            >
              {c.day}
            </div>
          ),
        )}
      </div>
      <div className="mt-2 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400" aria-hidden="true">
        <span>少</span>
        <span className="h-3 w-3 rounded bg-emerald-100 dark:bg-emerald-900" />
        <span className="h-3 w-3 rounded bg-emerald-300 dark:bg-emerald-700" />
        <span className="h-3 w-3 rounded bg-emerald-500" />
        <span>多</span>
      </div>
    </div>
  )
}

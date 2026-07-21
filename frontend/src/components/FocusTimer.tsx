// 专注计时器（iter 251 功能补充循环 3）：选时长 -> 倒计时 -> 完成由父组件自动打卡。
// 设计要点：
// - 终点时间戳计算剩余（Date.now() 差值），后台标签页 setInterval 被节流时不漂移——
//   回到前台下一次 tick 即校正（不用计数递减，iter 231 并发面同款时间语义）；
// - 页面 title 同步剩余时间（切走标签页仍可见剩余），结束/取消/卸载还原；
// - 中途离开页面 = 放弃本次计时（不持久化——跨页持久涉全局状态/恢复 UX，有意取舍见 stage.md）；
// - 倒计时不用 aria-live（每秒更新会刷屏读屏器），role=timer + 静态 aria-label。
import { useEffect, useRef, useState } from 'react'
import { toast } from './Toast'

const PRESETS = [15, 25, 45] // 分钟；25 = 经典番茄钟
const MAX_MINUTES = 180

interface FocusTimerProps {
  busy: boolean // 打卡提交在途（开始避让；完成时父组件决定自动/回填）
  onComplete: (minutes: number) => void
}

export default function FocusTimer({ busy, onComplete }: FocusTimerProps) {
  const [preset, setPreset] = useState(25)
  const [custom, setCustom] = useState('')
  const [remainMs, setRemainMs] = useState<number | null>(null) // null = 空闲
  const endAtRef = useRef(0)
  const minsRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const titleRef = useRef<string | null>(null)

  const running = remainMs !== null

  function clearTimer() {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }

  function restoreTitle() {
    if (titleRef.current !== null) {
      document.title = titleRef.current
      titleRef.current = null
    }
  }

  // 卸载清理：interval 与页面 title 都必须还原
  useEffect(
    () => () => {
      clearTimer()
      restoreTitle()
    },
    [],
  )

  function tick() {
    const left = endAtRef.current - Date.now()
    if (left <= 0) {
      clearTimer()
      setRemainMs(null)
      restoreTitle()
      onComplete(minsRef.current)
      return
    }
    setRemainMs(left)
    const totalSec = Math.ceil(left / 1000)
    const mm = String(Math.floor(totalSec / 60)).padStart(2, '0')
    const ss = String(totalSec % 60).padStart(2, '0')
    document.title = `⏱ ${mm}:${ss} · Deep-tutor`
  }

  function start() {
    const mins = custom.trim() ? Number(custom) : preset
    if (!Number.isInteger(mins) || mins < 1 || mins > MAX_MINUTES) {
      toast.error(`专注时长须为 1-${MAX_MINUTES} 分钟的整数`)
      return
    }
    minsRef.current = mins
    endAtRef.current = Date.now() + mins * 60_000
    if (titleRef.current === null) titleRef.current = document.title
    clearTimer()
    setRemainMs(mins * 60_000)
    timerRef.current = setInterval(tick, 250)
    tick()
  }

  function cancel() {
    if (!window.confirm('放弃本次专注计时？已进行的时间不会记录。')) return
    clearTimer()
    setRemainMs(null)
    restoreTitle()
  }

  const totalSec = remainMs !== null ? Math.ceil(remainMs / 1000) : 0
  const mm = String(Math.floor(totalSec / 60)).padStart(2, '0')
  const ss = String(totalSec % 60).padStart(2, '0')

  if (running) {
    return (
      <div className="mb-3 flex items-center justify-between rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 dark:border-blue-900 dark:bg-blue-950">
        <span
          role="timer"
          aria-label={`专注计时进行中，剩余 ${mm} 分 ${ss} 秒`}
          className="text-2xl font-semibold tabular-nums text-blue-700 dark:text-blue-300"
        >
          ⏱ {mm}:{ss}
        </span>
        <button
          type="button"
          onClick={cancel}
          className="rounded-lg border px-3 py-1 text-sm text-slate-600 transition-colors hover:bg-white dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
        >
          放弃
        </button>
      </div>
    )
  }

  return (
    <div className="mb-3 rounded-lg border border-dashed px-3 py-2 dark:border-slate-600">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-slate-600 dark:text-slate-300">专注计时</span>
        {PRESETS.map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => {
              setPreset(m)
              setCustom('')
            }}
            aria-pressed={preset === m && !custom.trim()}
            className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
              preset === m && !custom.trim()
                ? 'border-blue-500 bg-blue-50 font-medium text-blue-700 dark:bg-blue-950 dark:text-blue-300'
                : 'text-slate-500 hover:bg-slate-50 dark:text-slate-400 dark:hover:bg-slate-700'
            }`}
          >
            {m} 分钟
          </button>
        ))}
        <input
          type="number"
          value={custom}
          onChange={(e) => setCustom(e.target.value)}
          placeholder="自定义"
          aria-label="自定义专注分钟数"
          min={1}
          max={MAX_MINUTES}
          className="w-20 rounded-lg border px-2 py-0.5 text-xs dark:border-slate-600 dark:bg-slate-800"
        />
        <button
          type="button"
          onClick={start}
          disabled={busy}
          className="ml-auto rounded-lg bg-blue-600 px-3 py-1 text-xs font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
        >
          开始专注
        </button>
      </div>
      <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
        计时结束自动按该时长打卡（备注取下方打卡备注，留空记「番茄钟专注」）；离开本页将放弃计时。
      </p>
    </div>
  )
}

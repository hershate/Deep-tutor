// 专注计时器（iter 251 功能补充循环 3）：选时长 -> 倒计时 -> 完成由父组件自动打卡。
// 设计要点：
// - 终点时间戳计算剩余（Date.now() 差值），后台标签页 setInterval 被节流时不漂移——
//   回到前台下一次 tick 即校正（不用计数递减，iter 231 并发面同款时间语义）；
// - 暂停/继续（iter 259）：暂停时冻结剩余毫秒并清 interval，继续时以 当前时间+冻结剩余 重设终点——
//   暂停期间后台节流/锁屏均不计时（真实「暂停」而非「慢走」）；
// - 完成提示音（iter 266：iter 251 支持切走标签页看 title 剩余，但结束瞬间后台无声——
//   振荡器上行双音，无资源文件；AudioContext 需用户手势，在 start() 点击时创建/解锁，
//   浏览器仍拒绝则静默降级——提示音是增强不是必需）；
// - 页面 title 同步剩余时间（切走标签页仍可见剩余；暂停时显示 ⏸），结束/取消/卸载还原；
// - 中途离开页面 = 放弃本次计时（不持久化——跨页持久涉全局状态/恢复 UX，有意取舍见 stage.md）；
// - 倒计时不用 aria-live（每秒更新会刷屏读屏器），role=timer + 静态 aria-label。
import { useEffect, useRef, useState } from 'react'
import { toast } from './Toast'
import { appTitle } from '../lib/title'

const PRESETS = [15, 25, 45] // 分钟；25 = 经典番茄钟
const MAX_MINUTES = 180
// iter 280：预设记忆（iter 145 同款静默降级）——常用 45 分钟的用户免每次重选；
// 自定义分钟不记忆（每次显式输入，防陈旧值误用）
const PRESET_KEY = 'dt-focus-preset'

function readPreset(): number {
  try {
    const v = Number(localStorage.getItem(PRESET_KEY))
    return PRESETS.includes(v) ? v : 25
  } catch {
    return 25
  }
}

interface FocusTimerProps {
  busy: boolean // 打卡提交在途（开始避让；完成时父组件决定自动/回填）
  onComplete: (minutes: number) => void
}

export default function FocusTimer({ busy, onComplete }: FocusTimerProps) {
  const [preset, setPreset] = useState(readPreset)
  const [custom, setCustom] = useState('')
  const [remainMs, setRemainMs] = useState<number | null>(null) // null = 空闲
  const [paused, setPaused] = useState(false)
  const endAtRef = useRef(0)
  const pausedLeftRef = useRef(0) // 暂停时刻冻结的剩余毫秒
  const minsRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const audioRef = useRef<AudioContext | null>(null)
  // iter 365 E4：onComplete 经 ref 同步最新值——tick 闭包捕获 start() 时的 onComplete，
  // 而父组件内联 onComplete 闭包捕获当时的 note；计时期间用户输入 note 变化不会进入旧闭包，
  // 完成打卡记成旧 note（通常空=「番茄钟专注」）。ref 让 tick 调最新 onComplete（含最新 note）。
  const onCompleteRef = useRef(onComplete)
  useEffect(() => {
    onCompleteRef.current = onComplete
  }, [onComplete])

  const running = remainMs !== null

  // iter 266：完成提示音——振荡器上行双音（A5→D6，0.45s 总量克制）；
  // AudioContext 需用户手势解锁：start() 点击时创建/resume；任何失败静默降级
  function ensureAudio() {
    try {
      const Ctor =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!Ctor) return
      audioRef.current ??= new Ctor()
      if (audioRef.current.state === 'suspended') void audioRef.current.resume()
    } catch {
      // 音频不可用：忽略
    }
  }

  function beep() {
    const ctx = audioRef.current
    if (!ctx) return
    try {
      const t0 = ctx.currentTime
      ;[880, 1174.66].forEach((freq, i) => {
        const offset = i * 0.22
        const osc = ctx.createOscillator()
        const gain = ctx.createGain()
        osc.type = 'sine'
        osc.frequency.value = freq
        gain.gain.setValueAtTime(0.0001, t0 + offset)
        gain.gain.exponentialRampToValueAtTime(0.18, t0 + offset + 0.02)
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + offset + 0.2)
        osc.connect(gain).connect(ctx.destination)
        osc.start(t0 + offset)
        osc.stop(t0 + offset + 0.22)
      })
    } catch {
      // 同上：提示音失败不影响打卡主流程
    }
  }

  function clearTimer() {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }

  // iter 371：改走 lib/title 分层——不再快照/恢复整串标题（会恢复到陈旧角标）
  function restoreTitle() {
    appTitle.setTimer(null)
  }

  // 卸载清理：interval、页面 title、AudioContext 都必须还原/释放
  useEffect(
    () => () => {
      clearTimer()
      restoreTitle()
      void audioRef.current?.close().catch(() => {})
      audioRef.current = null
    },
    [],
  )

  function tick() {
    const left = endAtRef.current - Date.now()
    if (left <= 0) {
      clearTimer()
      setRemainMs(null)
      restoreTitle()
      beep() // iter 266：后台标签页可闻的完成信号
      onCompleteRef.current(minsRef.current) // iter 365 E4：调最新 onComplete（含最新 note）
      return
    }
    setRemainMs(left)
    const totalSec = Math.ceil(left / 1000)
    const mm = String(Math.floor(totalSec / 60)).padStart(2, '0')
    const ss = String(totalSec % 60).padStart(2, '0')
    appTitle.setTimer(`⏱ ${mm}:${ss}`)
  }

  function start() {
    const mins = custom.trim() ? Number(custom) : preset
    if (!Number.isInteger(mins) || mins < 1 || mins > MAX_MINUTES) {
      toast.error(`专注时长须为 1-${MAX_MINUTES} 分钟的整数`)
      return
    }
    minsRef.current = mins
    endAtRef.current = Date.now() + mins * 60_000
    ensureAudio() // iter 266：借开始点击的用户手势解锁 AudioContext
    clearTimer()
    setPaused(false)
    setRemainMs(mins * 60_000)
    timerRef.current = setInterval(tick, 250)
    tick()
  }

  // iter 259：暂停/继续——暂停冻结剩余并停 interval（期间不计时），继续重设终点
  function pause() {
    const left = endAtRef.current - Date.now()
    if (left <= 0) return // 已到点，交给 tick 走完完成流程
    pausedLeftRef.current = left
    clearTimer()
    setRemainMs(left)
    setPaused(true)
    const totalSec = Math.ceil(left / 1000)
    const mm = String(Math.floor(totalSec / 60)).padStart(2, '0')
    const ss = String(totalSec % 60).padStart(2, '0')
    appTitle.setTimer(`⏸ ${mm}:${ss}`)
  }

  function resume() {
    endAtRef.current = Date.now() + pausedLeftRef.current
    setPaused(false)
    clearTimer()
    timerRef.current = setInterval(tick, 250)
    tick()
  }

  function cancel() {
    if (!window.confirm('放弃本次专注计时？已进行的时间不会记录。')) return
    clearTimer()
    setRemainMs(null)
    setPaused(false)
    restoreTitle()
  }

  const totalSec = remainMs !== null ? Math.ceil(remainMs / 1000) : 0
  const mm = String(Math.floor(totalSec / 60)).padStart(2, '0')
  const ss = String(totalSec % 60).padStart(2, '0')

  if (running) {
    return (
      <div
        className={`mb-3 flex items-center justify-between rounded-lg border px-3 py-2 ${
          paused
            ? 'border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950'
            : 'border-blue-200 bg-blue-50 dark:border-blue-900 dark:bg-blue-950'
        }`}
      >
        <span
          role="timer"
          aria-label={paused ? `专注计时已暂停，剩余 ${mm} 分 ${ss} 秒` : `专注计时进行中，剩余 ${mm} 分 ${ss} 秒`}
          className={`text-2xl font-semibold tabular-nums ${
            paused ? 'text-amber-700 dark:text-amber-300' : 'text-blue-700 dark:text-blue-300'
          }`}
        >
          {paused ? '⏸' : '⏱'} {mm}:{ss}
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={paused ? resume : pause}
            className="rounded-lg border px-3 py-1 text-sm text-slate-600 transition-colors hover:bg-white dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            {paused ? '继续' : '暂停'}
          </button>
          <button
            type="button"
            onClick={cancel}
            className="rounded-lg border px-3 py-1 text-sm text-slate-600 transition-colors hover:bg-white dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            放弃
          </button>
        </div>
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
              try {
                localStorage.setItem(PRESET_KEY, String(m)) // iter 280：记忆预设
              } catch {
                /* 写失败仅本次会话生效 */
              }
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
        计时结束自动按该时长打卡（备注取下方打卡备注，留空记「番茄钟专注」）；可暂停/继续，结束有提示音；离开本页将放弃计时。
      </p>
    </div>
  )
}

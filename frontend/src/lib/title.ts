// 应用标题分层管理（iter 371）：document.title 由三层独立登记合成——
// base（默认 'Deep-tutor'，插件页可覆写）· badge（任务角标 (n)）· timer（专注倒计时）。
// 此前 TaskCenter/FocusTimer/PluginPageHost 三方各自直写 document.title 互踩：
// 计时开始快照、结束时恢复陈旧快照（残留假角标）；计时中覆盖角标/插件页标题且不再恢复；
// 角标重算又会覆盖插件页标题。分层后任一层变化只重渲染自己的部分，其余层不受影响。

let base: string | null = null // null = 默认 'Deep-tutor'
let badge = 0
let timerLabel: string | null = null

function render() {
  // timerLabel 由调用方带图标传入（⏱ 计时中 / ⏸ 已暂停），原样作前缀
  const prefix = timerLabel !== null ? timerLabel : badge > 0 ? `(${badge})` : ''
  document.title = prefix ? `${prefix} · ${base ?? 'Deep-tutor'}` : (base ?? 'Deep-tutor')
}

export const appTitle = {
  /** 覆写基础标题（插件页名）；null 还原默认 */
  setBase(b: string | null) {
    base = b
    render()
  },
  /** 活动任务角标数（0 = 无角标） */
  setBadge(n: number) {
    badge = Math.max(0, Math.trunc(n))
    render()
  },
  /** 专注计时标签（'mm:ss'，倒计时/暂停期间）；null = 计时结束恢复其余层 */
  setTimer(label: string | null) {
    timerLabel = label
    render()
  },
}

// WAI-ARIA tabs 键盘模式辅助：role=tablist 容器 onKeyDown 用。
// ←/→ 在 role=tab 间循环移动并激活，Home/End 跳首尾（此前键盘用户只能逐个 Tab 过全部 tab）。
import type { KeyboardEvent } from 'react'

export function onTablistKeyDown(e: KeyboardEvent<HTMLElement>) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return
  const tabs = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]'))
  const idx = tabs.indexOf(document.activeElement as HTMLElement)
  if (idx === -1) return
  e.preventDefault()
  let next: number
  if (e.key === 'ArrowRight') next = (idx + 1) % tabs.length
  else if (e.key === 'ArrowLeft') next = (idx + tabs.length - 1) % tabs.length
  else if (e.key === 'Home') next = 0
  else next = tabs.length - 1
  tabs[next].focus()
  tabs[next].click()
}

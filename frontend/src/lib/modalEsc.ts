// iter 368：全局 Esc 弹层栈。
// 多个弹层可叠加打开（测验弹层内再开材料预览、材料内再开 KB 查看等），此前各弹层
// 独立监听 window keydown——一次 Esc 会让所有打开的弹层同时关闭（预期是只关最上层）。
// 用法：useModalEsc(open, () => close())——仅当本弹层位于栈顶时响应 Esc。

import { useEffect, useRef } from 'react'

interface Entry {
  close: () => void
}

const stack: Entry[] = []

/** 栈顶判定：当前是否轮到该 entry 响应 Esc（仅模块内使用）。 */
function isTop(entry: Entry): boolean {
  return stack[stack.length - 1] === entry
}

export function useModalEsc(active: boolean, close: () => void): void {
  const closeRef = useRef(close)
  closeRef.current = close
  useEffect(() => {
    if (!active) return
    const entry: Entry = { close: () => closeRef.current() }
    stack.push(entry)
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (!isTop(entry)) return // 非顶层弹层：留给栈顶处理
      closeRef.current()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      const i = stack.indexOf(entry)
      if (i >= 0) stack.splice(i, 1)
    }
  }, [active])
}

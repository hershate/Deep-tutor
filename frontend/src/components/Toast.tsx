// Toast 轻量通知（plan/06 §二 组件原子）：zustand 全局队列 + Toaster 容器。
// 用法：toast.success('链接已复制')；3s 自动消失，可手动关闭。成功类瞬时反馈用 Toast，
// 表单级错误仍内联（role=alert）不混用。
import { create } from 'zustand'

export type ToastKind = 'success' | 'info' | 'error'

export interface ToastItem {
  id: number
  kind: ToastKind
  message: string
  leaving?: boolean // iter 104：退场阶段（播完 fade-out 再移除）
}

interface ToastState {
  toasts: ToastItem[]
  push: (kind: ToastKind, message: string) => void
  remove: (id: number) => void
  beginRemove: (id: number) => void
}

let nextId = 1
const TTL_MS = 3000
const EXIT_MS = 200 // 与 tailwind animate-fade-out 时长一致

export const useToastStore = create<ToastState>((set, get) => ({
  toasts: [],
  push: (kind, message) => {
    const id = nextId++
    set((s) => ({ toasts: [...s.toasts.slice(-3), { id, kind, message }] })) // 最多叠 4 条
    setTimeout(() => get().beginRemove(id), TTL_MS)
  },
  // 先标 leaving 播退场动画，再真正移除（手动关闭与 TTL 到期同路径）
  beginRemove: (id) => {
    set((s) => ({ toasts: s.toasts.map((t) => (t.id === id ? { ...t, leaving: true } : t)) }))
    setTimeout(() => {
      set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }))
    }, EXIT_MS)
  },
  remove: (id) => get().beginRemove(id),
}))

/** 便捷调用（非组件环境也可用）。 */
export const toast = {
  success: (message: string) => useToastStore.getState().push('success', message),
  info: (message: string) => useToastStore.getState().push('info', message),
  error: (message: string) => useToastStore.getState().push('error', message),
}

const KIND_STYLE: Record<ToastKind, string> = {
  success: 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200',
  info: 'border-blue-300 bg-blue-50 text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200',
  error: 'border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200',
}
const KIND_ICON: Record<ToastKind, string> = { success: '✓', info: 'ℹ', error: '✕' }

/** 容器：挂 Layout 一次，固定右下角。 */
export function Toaster() {
  const toasts = useToastStore((s) => s.toasts)
  const remove = useToastStore((s) => s.remove)
  if (toasts.length === 0) return null
  return (
    <div aria-live="polite" className="fixed bottom-4 right-4 z-50 flex w-72 flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm shadow-lg ${t.leaving ? 'animate-fade-out' : 'animate-fade-in-up'} ${KIND_STYLE[t.kind]}`}
        >
          <span aria-hidden="true">{KIND_ICON[t.kind]}</span>
          <span className="min-w-0 flex-1 break-all">{t.message}</span>
          <button
            type="button"
            onClick={() => remove(t.id)}
            aria-label="关闭通知"
            className="shrink-0 opacity-60 transition-opacity hover:opacity-100"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}

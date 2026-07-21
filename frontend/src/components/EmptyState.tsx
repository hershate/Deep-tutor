// 空态（plan/06 §二 组件原子 EmptyState）：图标 + 标题 + 描述 + 可选动作。
import type { ReactNode } from 'react'

interface EmptyStateProps {
  icon?: string
  title: string
  description?: string
  action?: ReactNode
}

export default function EmptyState({ icon = '📭', title, description, action }: EmptyStateProps) {
  return (
    <div className="flex animate-fade-in-up flex-col items-center gap-2 rounded-xl border border-dashed px-4 py-10 text-center dark:border-slate-600">
      <span aria-hidden="true" className="animate-float text-3xl">
        {icon}
      </span>
      <p className="text-sm font-medium text-slate-600 dark:text-slate-300">{title}</p>
      {description && <p className="text-xs text-slate-500 dark:text-slate-400">{description}</p>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  )
}

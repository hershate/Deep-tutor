// 加载骨架屏（plan/06 §二 组件原子 Skeleton）：脉冲占位块。
interface SkeletonProps {
  className?: string
}

export function Skeleton({ className = '' }: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      className={`animate-pulse rounded bg-slate-200 dark:bg-slate-700 ${className}`}
    />
  )
}

/** 多行文本骨架（列表/段落加载占位）。 */
export function SkeletonLines({ lines = 3, className = '' }: { lines?: number; className?: string }) {
  return (
    <div className={`space-y-2 ${className}`} role="status" aria-label="加载中">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={`h-4 ${i === lines - 1 ? 'w-2/3' : 'w-full'}`} />
      ))}
    </div>
  )
}

/** 卡片骨架（列表页加载占位）。 */
export function SkeletonCards({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-2" role="status" aria-label="加载中">
      {Array.from({ length: count }, (_, i) => (
        <Skeleton key={i} className="h-16 w-full" />
      ))}
    </div>
  )
}

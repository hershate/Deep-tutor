import { useQuery } from '@tanstack/react-query'
import { Skeleton } from '../components/Skeleton'
import { getUsage } from '../lib/api'

/** 用量详情页（/usage）：汇总 token / 费用 / 缓存命中率 / 调用数。 */
export default function Usage() {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['usage'],
    queryFn: getUsage,
    staleTime: 30_000,
    refetchOnWindowFocus: true,
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-bold">用量统计</h2>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="rounded-lg border px-3 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700 disabled:opacity-50 disabled:active:scale-100"
        >
          {isFetching ? '刷新中…' : '刷新'}
        </button>
      </div>

      {/* iter 95 美化：加载态由纯文本改骨架卡片（与数据卡片同构，无跳变） */}
      {isLoading && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4" role="status" aria-label="加载中">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-[76px] w-full" />
          ))}
        </div>
      )}
      {error && (
        <p role="alert" className="text-red-600 dark:text-red-400">
          {/* err.md D16：显示真实错误信息（此前任何错误都笼统提示 token，误导排障） */}
          加载失败：{error instanceof Error ? error.message : '未知错误'}
        </p>
      )}

      {data && (
        <>
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="总 Token" value={data.total_tokens.toLocaleString()} delay={0} />
            <Stat label="总费用" value={`$${data.total_cost.toFixed(6)}`} delay={60} />
            <Stat label="缓存命中率" value={`${(data.cache_hit_rate * 100).toFixed(1)}%`} delay={120} />
            <Stat label="API 调用数" value={String(data.calls)} delay={180} />
          </dl>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            汇总所有会话的 LLM 调用（api_usage 表，成本计量 Pillar 3）。
          </p>
        </>
      )}
    </div>
  )
}

function Stat({ label, value, delay = 0 }: { label: string; value: string; delay?: number }) {
  return (
    <div
      className="animate-fade-in-up rounded-xl border bg-white p-4 shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md dark:border-slate-700 dark:bg-slate-800"
      style={{ animationDelay: `${delay}ms` }}
    >
      <dt className="text-xs text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="mt-1 text-xl font-semibold tabular-nums">{value}</dd>
    </div>
  )
}

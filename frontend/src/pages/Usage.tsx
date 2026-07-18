import { useQuery } from '@tanstack/react-query'
import { Skeleton } from '../components/Skeleton'
import { getUsage, getUsageDaily } from '../lib/api'

/** 用量详情页（/usage）：汇总 token / 费用 / 缓存命中率 / 调用数。 */
export default function Usage() {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['usage'],
    queryFn: getUsage,
    staleTime: 30_000,
    refetchOnWindowFocus: true,
  })
  // iter 131：近 14 日按日趋势（CSS 柱状图，无图表库依赖）
  const daily = useQuery({ queryKey: ['usage-daily'], queryFn: () => getUsageDaily(14), staleTime: 60_000 })

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

      {/* iter 131：近 14 日按日费用柱状图（纯 CSS，悬浮看明细） */}
      {daily.data && daily.data.items.length > 0 && <DailyChart items={daily.data.items} />}
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

/** iter 131：按日费用柱状图（无图表库；每柱 title 悬浮显示 日期/费用/token/调用数） */
function DailyChart({ items }: { items: { date: string; cost: number; tokens: number; calls: number }[] }) {
  const maxCost = Math.max(...items.map((i) => i.cost), 1e-9)
  const total = items.reduce((s, i) => s + i.cost, 0)
  return (
    <section className="animate-fade-in-up rounded-xl border bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-medium text-slate-600 dark:text-slate-300">近 {items.length} 天费用</h3>
        <span className="text-xs tabular-nums text-slate-400">合计 ${total.toFixed(6)}</span>
      </div>
      <div className="flex h-28 items-end gap-1" role="img" aria-label="近14天按日费用柱状图">
        {items.map((i, bi) => (
          <div
            key={i.date}
            title={`${i.date}\n费用 $${i.cost.toFixed(6)} · ${i.tokens.toLocaleString()} tokens · ${i.calls} 次调用`}
            className="group relative flex-1 animate-fade-in rounded-t bg-blue-500/70 transition-all duration-200 hover:bg-blue-600 dark:bg-blue-400/60 dark:hover:bg-blue-400"
            style={{ height: `${Math.max((i.cost / maxCost) * 100, i.cost > 0 ? 6 : 2)}%`, animationDelay: `${Math.min(bi, 13) * 35}ms` }}
          />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] tabular-nums text-slate-400">
        <span>{items[0].date.slice(5)}</span>
        <span>{items[items.length - 1].date.slice(5)}</span>
      </div>
    </section>
  )
}

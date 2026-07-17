import { useQuery } from '@tanstack/react-query'
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
          className="rounded-lg border px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-50"
        >
          {isFetching ? '刷新中…' : '刷新'}
        </button>
      </div>

      {isLoading && <p className="text-slate-500">加载中…</p>}
      {error && (
        <p role="alert" className="text-red-600">
          加载失败（请确认已配置认证 token）
        </p>
      )}

      {data && (
        <>
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="总 Token" value={data.total_tokens.toLocaleString()} />
            <Stat label="总费用" value={`$${data.total_cost.toFixed(6)}`} />
            <Stat label="缓存命中率" value={`${(data.cache_hit_rate * 100).toFixed(1)}%`} />
            <Stat label="API 调用数" value={String(data.calls)} />
          </dl>
          <p className="text-sm text-slate-500">
            汇总所有会话的 LLM 调用（api_usage 表，成本计量 Pillar 3）。
          </p>
        </>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-white p-4">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-1 text-xl font-semibold">{value}</dd>
    </div>
  )
}

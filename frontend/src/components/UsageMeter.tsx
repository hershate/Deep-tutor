import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { getUsage } from '../lib/api'

/**
 * 顶栏用量小窗（plan/06 §五 UsageMeter）：总费用 + token，点击跳详情。
 * 数据来自 GET /api/usage（api_usage 表聚合，成本计量 Pillar 3 可见性）。
 */
export default function UsageMeter() {
  const { data } = useQuery({
    queryKey: ['usage'],
    queryFn: getUsage,
    staleTime: 30_000,
    // iter 366：移除 refetchOnWindowFocus——本组件挂在顶栏（每个鉴权页都有），100 用户
    // ALT-Tab 即齐刷刷打 /usage。改由 taskEngine 在方案完成时 invalidateQueries(['usage']) 刷新。
  })
  if (!data) return null
  return (
    <Link
      to="/usage"
      className="text-sm tabular-nums text-slate-500 transition-colors hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
      aria-label={`用量：总费用 ￥${data.total_cost.toFixed(4)}，共 ${data.total_tokens} token`}
      title="查看用量详情"
    >
      <span>{`￥${data.total_cost.toFixed(4)}`}</span>
      <span className="mx-1 text-slate-300 dark:text-slate-600">·</span>
      <span>{(data.total_tokens / 1000).toFixed(1)}k tok</span>
    </Link>
  )
}

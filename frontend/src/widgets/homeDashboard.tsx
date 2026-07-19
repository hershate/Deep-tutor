// 内置首页 dashboard 小组件（iter 183）——全部经插件 API 注册（与第三方插件同一机制），
// 既是功能也是插件开发的活样例（API.md 示例即简化自此文件）。
// 每个组件无数据时返回 null 自行隐藏（新用户首页保持清爽表单）。
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getReviewDue, getStudyStats, getUsage } from '../lib/api'
import { widgets } from '../lib/plugins'
import Card from '../components/Card'

function StatCard({ to, icon, value, label }: { to: string; icon: string; value: string; label: string }) {
  return (
    <Link to={to} aria-label={`${label}：${value}`}>
      <Card interactive className="flex items-center gap-3 !p-3">
        <span aria-hidden="true" className="text-xl">{icon}</span>
        <span className="min-w-0">
          <span className="block truncate text-lg font-semibold tabular-nums leading-tight">{value}</span>
          <span className="block truncate text-xs text-slate-500 dark:text-slate-400">{label}</span>
        </span>
      </Card>
    </Link>
  )
}

function StreakWidget() {
  const { data } = useQuery({ queryKey: ['study-stats'], queryFn: getStudyStats, staleTime: 60_000 })
  if (!data || data.total_logs === 0) return null
  return <StatCard to="/wiki" icon="🔥" value={`${data.streak_days} 天`} label="连续打卡" />
}

function WeekMinutesWidget() {
  const { data } = useQuery({ queryKey: ['study-stats'], queryFn: getStudyStats, staleTime: 60_000 })
  if (!data || data.total_logs === 0) return null
  return <StatCard to="/wiki" icon="📊" value={`${data.week_minutes} 分钟`} label="本周已学" />
}

function ReviewDueWidget() {
  const { data } = useQuery({ queryKey: ['review-due'], queryFn: getReviewDue, staleTime: 60_000 })
  if (!data || data.total === 0) return null
  return <StatCard to="/wiki" icon="📅" value={`${data.total} 项`} label="今日待复习" />
}

function TodayCostWidget() {
  const { data } = useQuery({ queryKey: ['usage'], queryFn: getUsage, staleTime: 60_000 })
  if (!data || (data.today_cost === 0 && data.daily_budget_usd === 0)) return null
  const budget = data.daily_budget_usd > 0 ? ` / $${data.daily_budget_usd.toFixed(2)}` : ''
  return <StatCard to="/usage" icon="💰" value={`$${data.today_cost.toFixed(4)}${budget}`} label="今日费用 / 预算" />
}

/** 应用启动时调用一次：注册全部内置小组件 */
export function registerBuiltinWidgets(): void {
  widgets.register({ id: 'builtin.streak', slot: 'home.dashboard', component: StreakWidget, order: 10 })
  widgets.register({ id: 'builtin.week-minutes', slot: 'home.dashboard', component: WeekMinutesWidget, order: 20 })
  widgets.register({ id: 'builtin.review-due', slot: 'home.dashboard', component: ReviewDueWidget, order: 30 })
  widgets.register({ id: 'builtin.today-cost', slot: 'home.dashboard', component: TodayCostWidget, order: 40 })
}

// iter 346（F9）：学习中心独立聚合页 /learn（LMS 仪表盘模式 §八 M）。
// 此前 /learn/:planId 强制带方案 id、侧栏无独立入口，须从方案列表进入。
// 现聚合：今日学习任务 + 各方案进度卡片，drill-down 进 /learn/:planId 方案详情。
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { getTodayTasks, listPlans, type PlanSummary } from '../lib/api'

export default function LearnIndex() {
  const plansQ = useQuery({ queryKey: ['plans', 'learn'], queryFn: () => listPlans(), staleTime: 30_000 })
  const todayQ = useQuery({ queryKey: ['plans', 'today'], queryFn: getTodayTasks, staleTime: 60_000 })
  const plans = plansQ.data?.items ?? []
  const todayItems = todayQ.data?.items ?? []

  return (
    <div className="space-y-6">
      <header className="animate-fade-in-up">
        <h1 className="text-xl font-bold text-slate-800 dark:text-slate-100">🎯 学习中心</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          跨方案学习总览——选择方案进入其学习详情（进度看板 / 测验 / 自学材料）
        </p>
      </header>

      {/* 今日学习任务（iter 260 GET /plans/today 聚合） */}
      <section
        className="animate-fade-in-up rounded-xl border bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800"
        aria-label="今日学习"
      >
        <h2 className="mb-2 text-sm font-semibold text-slate-700 dark:text-slate-200">📅 今日学习</h2>
        {todayItems.length === 0 ? (
          <p className="text-sm text-slate-400">今日暂无排程任务</p>
        ) : (
          <ul className="space-y-2">
            {todayItems.map((t) => {
              const tasks = t.days.flatMap((d) => d.tasks)
              const mins = t.days.reduce((s, d) => s + (d.est_minutes ?? 0), 0)
              return (
                <li key={t.plan_id}>
                  <Link
                    to={`/learn/${t.plan_id}`}
                    className="block rounded-lg border px-3 py-2 transition-colors hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">{t.topic}</span>
                      {mins > 0 && <span className="shrink-0 text-xs text-slate-400">约 {mins} 分钟</span>}
                    </div>
                    {tasks.length > 0 && (
                      <div className="mt-1 truncate text-xs text-slate-500 dark:text-slate-400">{tasks.join(' · ')}</div>
                    )}
                  </Link>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      {/* 各方案进度卡片（drill-down 进方案学习详情） */}
      <section
        className="animate-fade-in-up rounded-xl border bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800"
        style={{ animationDelay: '60ms' }}
        aria-label="我的方案"
      >
        <h2 className="mb-2 text-sm font-semibold text-slate-700 dark:text-slate-200">📚 我的方案（{plans.length}）</h2>
        {plansQ.isLoading ? (
          <p className="text-sm text-slate-400">加载中…</p>
        ) : plans.length === 0 ? (
          <div className="text-sm text-slate-400">
            <p className="mb-2">尚无学习方案</p>
            <Link to="/" className="rounded bg-blue-600 px-3 py-1 text-white hover:bg-blue-700">
              去生成方案
            </Link>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {plans.map((p: PlanSummary) => (
              <Link
                key={p.id}
                to={`/learn/${p.id}`}
                className="block rounded-lg border p-3 transition-all hover:-translate-y-0.5 hover:shadow-md dark:border-slate-600"
              >
                <div className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">{p.topic || `方案 ${p.id}`}</div>
                <div className="mt-2">
                  {p.avg_mastery != null ? (
                    <>
                      <div className="mb-0.5 flex items-center justify-between text-xs text-slate-400">
                        <span>平均掌握度</span>
                        <span>{Math.round((p.avg_mastery ?? 0) * 100)}%</span>
                      </div>
                      <div className="h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700">
                        <div
                          className="h-full rounded-full bg-blue-500"
                          style={{ width: `${Math.round((p.avg_mastery ?? 0) * 100)}%` }}
                        />
                      </div>
                    </>
                  ) : (
                    <span className="text-xs text-slate-400">尚无掌握度数据</span>
                  )}
                </div>
                <div className="mt-2 text-xs text-blue-600 dark:text-blue-400">进入学习 →</div>
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

// 个人知识库 Wiki 页（iter 100）：跨方案聚合知识节点，可检索/按状态筛选，
// 按方案分组浏览；点击节点跳方案详情。数据：GET /api/knowledge。
import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import EmptyState from '../components/EmptyState'
import NodeNoteEditor from '../components/NodeNoteEditor'
import { SkeletonCards } from '../components/Skeleton'
import { toast } from '../components/Toast'
import { ApiError, downloadKnowledgeHandbook, getKnowledge, getNotesOverview, getReviewDue, getStatsCalendar, getStudyStats, getWrongOverview, updateProgress, type CalendarDay, type KnowledgeItem, type ProgressStatus } from '../lib/api'

const STATUS_LABEL: Record<ProgressStatus, string> = {
  not_started: '未开始',
  learning: '学习中',
  done: '已掌握',
}
const STATUS_STYLE: Record<ProgressStatus, string> = {
  not_started: 'bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300',
  learning: 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300',
  done: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300',
}
const DIFFICULTY_LABEL = ['', '入门', '基础', '中等', '进阶', '挑战']

export default function Wiki() {
  const qc = useQueryClient()
  const [q, setQ] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')
  const [status, setStatus] = useState<ProgressStatus | null>(null)
  const [openNoteId, setOpenNoteId] = useState<number | null>(null) // iter 105：展开中的笔记编辑器
  const [exportBusy, setExportBusy] = useState(false) // iter 116：手册导出防重入

  async function onExport() {
    if (exportBusy) return
    setExportBusy(true)
    try {
      await downloadKnowledgeHandbook()
      toast.success('知识手册已下载')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '导出失败，请重试')
    } finally {
      setExportBusy(false)
    }
  }

  // 快捷状态标记（iter 142）：不进学习中心直接循环 未开始->学习中->已掌握
  const [statusBusyId, setStatusBusyId] = useState<number | null>(null)
  const NEXT_STATUS: Record<ProgressStatus, ProgressStatus> = {
    not_started: 'learning',
    learning: 'done',
    done: 'not_started',
  }

  async function cycleStatus(n: KnowledgeItem) {
    if (statusBusyId !== null) return
    setStatusBusyId(n.node_id)
    try {
      await updateProgress(n.plan_id, n.sid, NEXT_STATUS[n.status])
      qc.invalidateQueries({ queryKey: ['knowledge'] })
      qc.invalidateQueries({ queryKey: ['review-due'] }) // 掌握度变化影响复习日
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '状态更新失败，请重试')
    } finally {
      setStatusBusyId(null)
    }
  }

  // 搜索防抖（300ms）：避免每击键一次请求
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300)
    return () => clearTimeout(t)
  }, [q])

  const { data, isLoading, error } = useQuery({
    queryKey: ['knowledge', debouncedQ, status],
    queryFn: () => getKnowledge({ q: debouncedQ, status: status ?? undefined }),
    staleTime: 15_000,
  })

  // 今日待复习（iter 119）：跨方案到期节点，顶部提醒卡
  const { data: due } = useQuery({
    queryKey: ['review-due'],
    queryFn: getReviewDue,
    staleTime: 60_000,
  })

  // 学习统计（iter 122）：连续打卡/本周/累计分钟
  const { data: studyStats } = useQuery({
    queryKey: ['study-stats'],
    queryFn: getStudyStats,
    staleTime: 60_000,
  })

  // 打卡热力图（iter 139）：近 12 周按日学习分钟
  const { data: calendar } = useQuery({
    queryKey: ['stats-calendar'],
    queryFn: () => getStatsCalendar(84),
    staleTime: 60_000,
  })

  // 全局错题（iter 156）：跨方案错题总览卡
  const { data: wrongOv } = useQuery({
    queryKey: ['wrong-overview'],
    queryFn: getWrongOverview,
    staleTime: 60_000,
  })

  // 笔记视图（iter 161）：知识点 / 我的笔记 tab 切换
  const [view, setView] = useState<'nodes' | 'notes'>('nodes')
  const { data: notesOv } = useQuery({
    queryKey: ['notes-overview'],
    queryFn: getNotesOverview,
    staleTime: 30_000,
    enabled: view === 'notes',
  })

  // 排序（iter 146）：组内节点排序，默认保持后端顺序
  const [sortKey, setSortKey] = useState<'default' | 'mastery_asc' | 'mastery_desc' | 'difficulty' | 'name'>('default')

  // 按方案分组（保持后端返回顺序：新方案在前）；组内按 sortKey 排序
  const groups = useMemo(() => {
    const map = new Map<number, { topic: string; items: KnowledgeItem[] }>()
    for (const item of data?.items ?? []) {
      const g = map.get(item.plan_id) ?? { topic: item.plan_topic || `方案 #${item.plan_id}`, items: [] }
      g.items.push(item)
      map.set(item.plan_id, g)
    }
    if (sortKey !== 'default') {
      const cmp: Record<Exclude<typeof sortKey, 'default'>, (a: KnowledgeItem, b: KnowledgeItem) => number> = {
        mastery_asc: (a, b) => a.mastery - b.mastery, // 薄弱在前——优先补
        mastery_desc: (a, b) => b.mastery - a.mastery,
        difficulty: (a, b) => a.difficulty - b.difficulty,
        name: (a, b) => a.name.localeCompare(b.name, 'zh'),
      }
      for (const g of map.values()) g.items.sort(cmp[sortKey])
    }
    return [...map.entries()]
  }, [data, sortKey])

  const stats = data?.stats

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-2xl font-bold">知识库</h2>
        <div className="flex items-center gap-2">
          {/* 知识手册导出（iter 116）：全部节点+笔记 -> Markdown 下载 */}
          <button
            onClick={onExport}
            disabled={exportBusy}
            className="rounded-lg border px-3 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            {exportBusy ? '导出中…' : '导出手册 (.md)'}
          </button>
          {/* 排序（iter 146） */}
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as typeof sortKey)}
            aria-label="知识点排序"
            className="rounded-lg border px-2 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500/60 dark:border-slate-600 dark:bg-slate-800"
          >
            <option value="default">默认排序</option>
            <option value="mastery_asc">掌握度↑（薄弱优先）</option>
            <option value="mastery_desc">掌握度↓</option>
            <option value="difficulty">难度↑</option>
            <option value="name">名称</option>
          </select>
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索知识点 / 笔记…"
            aria-label="搜索知识点或笔记内容"
            className="w-56 rounded-lg border px-3 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500/60 dark:border-slate-600 dark:bg-slate-800"
          />
        </div>
      </div>

      {/* 学习统计行（iter 122）：有打卡记录时显示 */}
      {studyStats && studyStats.total_logs > 0 && (
        <div className="flex animate-fade-in-up flex-wrap gap-2 text-xs" aria-label="学习统计">
          <span className="animate-fade-in rounded-full bg-orange-100 px-3 py-1 font-medium text-orange-700 dark:bg-orange-950 dark:text-orange-300" style={{ animationDelay: '40ms' }} title="连续打卡天数">
            🔥 连续 <span className="tabular-nums">{studyStats.streak_days}</span> 天
          </span>
          <span className="animate-fade-in rounded-full bg-blue-100 px-3 py-1 font-medium text-blue-700 dark:bg-blue-950 dark:text-blue-300" style={{ animationDelay: '100ms' }} title="近 7 天学习时长">
            📊 本周 <span className="tabular-nums">{studyStats.week_minutes}</span> 分钟
          </span>
          <span className="animate-fade-in rounded-full bg-slate-100 px-3 py-1 font-medium text-slate-600 dark:bg-slate-700 dark:text-slate-300" style={{ animationDelay: '160ms' }} title="累计学习时长">
            ⏱ 累计 <span className="tabular-nums">{studyStats.total_minutes}</span> 分钟
          </span>
        </div>
      )}

      {/* 打卡热力图（iter 139）：近 12 周学习分钟，GitHub 风格周列 */}
      {studyStats && studyStats.total_logs > 0 && calendar && (
        <StudyHeatmap items={calendar.items} />
      )}

      {/* 今日待复习提醒（iter 119）：有到期节点时置顶 */}
      {due && due.total > 0 && (
        <section className="animate-fade-in-up rounded-xl border border-amber-200 bg-amber-50 p-4 dark:border-amber-900 dark:bg-amber-950">
          <h3 className="mb-2 text-sm font-semibold text-amber-800 dark:text-amber-200">
            📅 今日待复习 · <span className="tabular-nums">{due.total}</span> 个知识点
          </h3>
          <ul className="flex flex-wrap gap-1.5">
            {due.items.slice(0, 10).map((d, i) => (
              <li key={`${d.plan_id}-${d.node_id}`} className="animate-fade-in" style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}>
                <Link
                  to={`/learn/${d.plan_id}`}
                  title={`${d.plan_topic || '方案'} · 掌握度 ${Math.round(d.mastery * 100)}% · 复习日 ${d.next_review}`}
                  className="inline-block rounded-full border border-amber-300 bg-white px-2.5 py-1 text-xs text-amber-800 transition-all hover:-translate-y-0.5 hover:border-amber-400 hover:shadow-sm active:scale-95 dark:border-amber-800 dark:bg-slate-800 dark:text-amber-200 dark:hover:border-amber-700"
                >
                  {d.name}
                </Link>
              </li>
            ))}
            {due.total > 10 && (
              <li className="animate-fade-in px-1.5 py-1 text-xs tabular-nums text-amber-700 dark:text-amber-300" style={{ animationDelay: '360ms' }}>等 {due.total} 个</li>
            )}
          </ul>
        </section>
      )}

      {/* 全局错题（iter 156）：跨方案错题总览，有错题时显示 */}
      {wrongOv && wrongOv.total > 0 && (
        <section className="animate-fade-in-up rounded-xl border border-red-200 bg-red-50 p-4 dark:border-red-900 dark:bg-red-950">
          <h3 className="mb-2 text-sm font-semibold text-red-800 dark:text-red-200">
            <span aria-hidden="true">🎯</span> 当前错题 · <span className="tabular-nums">{wrongOv.total}</span> 道
          </h3>
          <ul className="flex flex-wrap gap-1.5">
            {wrongOv.items.map((w, i) => (
              <li key={w.plan_id} className="animate-fade-in-up" style={{ animationDelay: `${Math.min(i, 10) * 40}ms` }}>
                <Link
                  to={`/plans/${w.plan_id}`}
                  title={`${w.topic || '方案'} · ${w.wrong_count} 道错题，去错题重练`}
                  className="inline-block rounded-full border border-red-300 bg-white px-2.5 py-1 text-xs text-red-800 transition-all hover:-translate-y-0.5 hover:border-red-400 hover:shadow-sm active:scale-95 dark:border-red-800 dark:bg-slate-800 dark:text-red-200 dark:hover:border-red-700"
                >
                  {w.topic || `方案 #${w.plan_id}`} <span className="tabular-nums">({w.wrong_count})</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* 视图切换（iter 161）：知识点 / 我的笔记 */}
      <div className="flex gap-2" role="tablist" aria-label="知识库视图">
        {([['nodes', '知识点'], ['notes', '我的笔记']] as const).map(([v, label]) => (
          <button
            key={v}
            role="tab"
            aria-selected={view === v}
            onClick={() => setView(v)}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-all active:scale-95 ${
              view === v
                ? 'bg-blue-600 text-white shadow-sm'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-300 dark:hover:bg-slate-600'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* key=view：切换 tab 时内容淡入（iter 163） */}
      <div key={view} className="animate-fade-in space-y-4">
      {view === 'notes' ? (
        /* 我的笔记（iter 161）：跨方案全部笔记，更新近者在前 */
        !notesOv ? (
          <SkeletonCards count={3} />
        ) : notesOv.total === 0 ? (
          <EmptyState
            icon="📝"
            title="还没有笔记"
            description="在知识点卡片上点 📝 即可记录笔记，全部笔记会汇聚到这里"
          />
        ) : (
          <ul className="space-y-2">
            {notesOv.items.map((n, i) => (
              <li
                key={n.node_id}
                className="animate-fade-in-up rounded-xl border bg-white p-4 shadow-sm transition-all hover:shadow-md dark:border-slate-700 dark:bg-slate-800"
                style={{ animationDelay: `${Math.min(i, 10) * 40}ms` }}
              >
                <div className="flex items-center justify-between gap-2">
                  <Link
                    to={`/plans/${n.plan_id}`}
                    className="min-w-0 truncate text-sm font-medium text-blue-700 underline-offset-2 hover:underline dark:text-blue-300"
                    title={`查看方案：${n.plan_topic || '方案'}`}
                  >
                    {n.node_name}
                    <span className="ml-2 text-xs font-normal text-slate-400">{n.plan_topic || `方案 #${n.plan_id}`}</span>
                  </Link>
                  {n.updated_at && (
                    <span className="shrink-0 text-xs tabular-nums text-slate-400">
                      {n.updated_at.slice(0, 16).replace('T', ' ')}
                    </span>
                  )}
                </div>
                <p className="mt-1.5 whitespace-pre-wrap text-sm text-slate-600 dark:text-slate-300">
                  {n.content_preview}
                  {n.truncated && <span className="text-slate-400"> …</span>}
                </p>
              </li>
            ))}
          </ul>
        )
      ) : (
      <>
      {/* 状态筛选 chips（带计数） */}
      {stats && (
        <div className="flex flex-wrap gap-2" role="group" aria-label="按学习状态筛选">
          {(Object.keys(STATUS_LABEL) as ProgressStatus[]).map((s) => (
            <button
              key={s}
              onClick={() => setStatus((prev) => (prev === s ? null : s))}
              aria-pressed={status === s}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-all active:scale-95 ${
                status === s
                  ? 'bg-blue-600 text-white shadow-sm'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-700 dark:text-slate-300 dark:hover:bg-slate-600'
              }`}
            >
              {STATUS_LABEL[s]} {stats[s] ?? 0}
            </button>
          ))}
        </div>
      )}

      {isLoading && <SkeletonCards count={3} />}
      {error && (
        <p role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          加载失败：{error instanceof Error ? error.message : '未知错误'}
        </p>
      )}

      {!isLoading && !error && groups.length === 0 && (
        <EmptyState
          icon="🧠"
          title={debouncedQ || status ? '没有匹配的知识点' : '知识库还是空的'}
          description={
            debouncedQ || status
              ? '换个关键词或清除筛选试试'
              : '生成学习方案后，方案的知识图谱节点会自动汇聚到这里'
          }
          action={
            debouncedQ || status ? (
              <button
                onClick={() => {
                  setQ('')
                  setStatus(null)
                }}
                className="rounded-lg border px-3 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
              >
                清除筛选
              </button>
            ) : (
              <Link
                to="/"
                className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98]"
              >
                去新建方案
              </Link>
            )
          }
        />
      )}

      {groups.map(([planId, g], gi) => (
        <section
          key={planId}
          className="animate-fade-in-up rounded-xl border bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800"
          style={{ animationDelay: `${Math.min(gi, 8) * 60}ms` }}
        >
          <header className="flex items-center justify-between border-b px-4 py-2.5 dark:border-slate-700">
            <h3 className="truncate font-semibold">{g.topic}</h3>
            <Link
              to={`/plans/${planId}`}
              className="shrink-0 text-sm text-blue-600 underline-offset-2 transition-colors hover:text-blue-500 hover:underline dark:text-blue-400 dark:hover:text-blue-300"
            >
              查看方案 →
            </Link>
          </header>
          <ul className="grid gap-2 p-3 sm:grid-cols-2">
            {g.items.map((n) => (
              <li key={n.node_id}>
                <div className="rounded-lg border transition-all hover:border-blue-300 hover:shadow-md dark:border-slate-700 dark:hover:border-blue-700">
                  <div className="flex items-start">
                    <Link to={`/plans/${planId}`} className="block min-w-0 flex-1 px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="min-w-0 truncate text-sm font-medium">
                          {n.must_learn && <span className="mr-1 text-amber-500" title="必学">★</span>}
                          {n.name}
                        </span>
                      </div>
                      <div className="mt-1.5 flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                        <span>{DIFFICULTY_LABEL[n.difficulty] ?? `难度${n.difficulty}`}</span>
                        <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700">
                          <span
                            className="block h-full rounded-full bg-emerald-500 transition-all duration-500 ease-out"
                            style={{ width: `${Math.round(n.mastery * 100)}%` }}
                            aria-label={`掌握度 ${Math.round(n.mastery * 100)}%`}
                          />
                        </span>
                        <span className="tabular-nums">{Math.round(n.mastery * 100)}%</span>
                      </div>
                    </Link>
                    <div className="flex shrink-0 flex-col items-end gap-1 px-1 py-1.5">
                      {/* 快捷状态标记（iter 142）：点击循环 未开始->学习中->已掌握->未开始 */}
                      <button
                        onClick={() => cycleStatus(n)}
                        disabled={statusBusyId !== null}
                        aria-label={`${n.name} 当前 ${STATUS_LABEL[n.status]}，点击切换状态`}
                        title="点击切换状态（未开始 → 学习中 → 已掌握）"
                        className={`rounded-full px-2 py-0.5 text-xs transition-all hover:ring-2 hover:ring-blue-300 active:scale-95 disabled:opacity-50 dark:hover:ring-blue-700 ${STATUS_STYLE[n.status]}`}
                      >
                        {statusBusyId === n.node_id ? '…' : STATUS_LABEL[n.status]}
                      </button>
                      {/* 笔记开关（iter 105）：有笔记时 📝 常驻着色 */}
                      <button
                        onClick={() => setOpenNoteId((prev) => (prev === n.node_id ? null : n.node_id))}
                        aria-expanded={openNoteId === n.node_id}
                        aria-label={`${n.name} 笔记`}
                        title={n.has_note ? '查看/编辑笔记' : '添加笔记'}
                        className={`rounded-lg px-2 py-1 text-sm transition-all hover:bg-slate-100 active:scale-90 dark:hover:bg-slate-700 ${
                          n.has_note ? 'text-amber-500' : 'text-slate-300 dark:text-slate-600'
                        }`}
                      >
                        📝
                      </button>
                    </div>
                  </div>
                  {openNoteId === n.node_id && (
                    <NodeNoteEditor
                      nodeId={n.node_id}
                      onSaved={() => {
                        qc.invalidateQueries({ queryKey: ['knowledge'] })
                        qc.invalidateQueries({ queryKey: ['notes-overview'] }) // iter 161：笔记视图同步
                      }}
                    />
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
      </>
      )}
      </div>
    </div>
  )
}

/** iter 139：打卡热力图——近 12 周（84 天）学习分钟，GitHub 风格周列，悬浮看 日期+分钟 */
function StudyHeatmap({ items }: { items: CalendarDay[] }) {
  // 顺序 7 天一列（最旧在左），列内自上而下时间递增
  const weeks: CalendarDay[][] = []
  for (let i = 0; i < items.length; i += 7) weeks.push(items.slice(i, i + 7))
  return (
    <section
      className="animate-fade-in-up rounded-xl border bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800"
      aria-label="近12周学习打卡热力图"
    >
      <h3 className="mb-2 text-sm font-medium text-slate-600 dark:text-slate-300">学习打卡 · 近 12 周</h3>
      <div className="flex gap-1 overflow-x-auto pb-1" role="img" aria-label={`近 ${items.length} 天学习打卡热力图，颜色越深分钟越多`}>
        {weeks.map((week, wi) => (
          <div key={wi} className="flex shrink-0 animate-fade-in flex-col gap-1" style={{ animationDelay: `${Math.min(wi, 11) * 30}ms` }}>
            {week.map((d) => (
              <div
                key={d.date}
                title={`${d.date} · ${d.minutes > 0 ? `${d.minutes} 分钟` : '未打卡'}`}
                className={`h-3.5 w-3.5 rounded-sm transition-all hover:scale-125 hover:shadow-sm ${heatClass(d.minutes)}`}
              />
            ))}
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center justify-end gap-1 text-[10px] text-slate-400" aria-hidden="true">
        少
        <span className={`h-3 w-3 rounded-sm ${heatClass(0)}`} />
        <span className={`h-3 w-3 rounded-sm ${heatClass(15)}`} />
        <span className={`h-3 w-3 rounded-sm ${heatClass(45)}`} />
        <span className={`h-3 w-3 rounded-sm ${heatClass(90)}`} />
        <span className={`h-3 w-3 rounded-sm ${heatClass(150)}`} />
        多
      </div>
    </section>
  )
}

function heatClass(minutes: number): string {
  if (minutes <= 0) return 'bg-slate-100 dark:bg-slate-700'
  if (minutes < 30) return 'bg-emerald-200 dark:bg-emerald-900'
  if (minutes < 60) return 'bg-emerald-400 dark:bg-emerald-700'
  if (minutes < 120) return 'bg-emerald-500 dark:bg-emerald-600'
  return 'bg-emerald-600 dark:bg-emerald-500'
}

// 自学材料阅读器（P2，plan/07 §六 / plan/06 §五 StudyMaterialReader）：
// 单元导航 + 内容渲染（react-markdown + sanitize）+ 上一/下一单元 + 标记已学 + 去测验。
// 数据：GET /plans/{id}/materials（列表）+ GET /materials/{unit_id}（内容，选中时按需加载）。
import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import { ApiError, getStudyUnitContent, markUnitDone, type StudyUnitMeta, type UnitResource } from '../lib/api'

interface Props {
  units: StudyUnitMeta[]
  onUnitsChange: (units: StudyUnitMeta[]) => void
  onOpenQuiz: () => void
}

export default function StudyMaterialReader({ units, onUnitsChange, onOpenQuiz }: Props) {
  const [currentId, setCurrentId] = useState<number | null>(units[0]?.id ?? null)
  const [content, setContent] = useState('')
  const [contentTitle, setContentTitle] = useState('')
  const [resources, setResources] = useState<UnitResource[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [doneBusy, setDoneBusy] = useState(false)
  const acRef = useRef<AbortController | null>(null)

  const idx = units.findIndex((u) => u.id === currentId)
  const current = idx >= 0 ? units[idx] : null

  // 选中单元 -> 按需加载内容（卸载/切换中止进行中请求）
  useEffect(() => {
    if (currentId === null) return
    acRef.current?.abort()
    const ac = new AbortController()
    acRef.current = ac
    setLoading(true)
    setError('')
    getStudyUnitContent(currentId, ac.signal)
      .then((r) => {
        if (ac.signal.aborted) return
        setContent(r.content_md)
        setContentTitle(r.title)
        setResources(r.resources ?? [])
      })
      .catch((e) => {
        if (ac.signal.aborted) return
        setError(e instanceof ApiError ? e.message : '内容加载失败')
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false)
      })
    return () => ac.abort()
  }, [currentId])

  async function toggleDone() {
    if (!current || doneBusy) return
    setDoneBusy(true)
    try {
      const r = await markUnitDone(current.id, !current.done)
      onUnitsChange(units.map((u) => (u.id === current.id ? { ...u, done: r.done } : u)))
    } catch {
      // 标记失败不打断阅读（下次进入列表会重取）
    } finally {
      setDoneBusy(false)
    }
  }

  if (units.length === 0) return <p className="text-sm text-slate-500">暂无自学单元</p>

  return (
    <div className="flex min-h-0 flex-1 gap-4">
      {/* 单元导航 */}
      <nav aria-label="自学单元" className="w-44 shrink-0 overflow-y-auto border-r pr-2">
        <ul className="space-y-1">
          {units.map((u, i) => (
            <li key={u.id}>
              <button
                onClick={() => setCurrentId(u.id)}
                aria-current={u.id === currentId ? 'true' : undefined}
                className={`w-full rounded px-2 py-1.5 text-left text-sm ${
                  u.id === currentId ? 'bg-blue-50 font-medium text-blue-700' : 'hover:bg-slate-50'
                }`}
              >
                <span className="mr-1 text-xs text-slate-400">{i + 1}.</span>
                {u.title}
                {u.done && (
                  <span className="ml-1 text-xs text-green-600" aria-label="已学">
                    ✓<span className="sr-only">（已学）</span>
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      </nav>

      {/* 内容区 */}
      <div className="min-w-0 flex-1 overflow-y-auto">
        {loading && <p className="text-sm text-slate-500">加载单元内容…</p>}
        {error && (
          <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}
        {!loading && !error && current && (
          <>
            <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
              {current.estimated_time && <span>预计 {current.estimated_time}</span>}
              {current.objectives.length > 0 && <span>目标：{current.objectives.join('；')}</span>}
            </div>
            <div className="markdown-body text-sm">
              <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
                {content || `## ${contentTitle}\n\n（本单元暂无内容）`}
              </ReactMarkdown>
            </div>
            {resources.length > 0 && (
              <div className="mt-3 rounded-lg border border-slate-200 p-3">
                <h4 className="mb-1 text-xs font-medium text-slate-500">参考资料</h4>
                <ul className="space-y-1 text-sm">
                  {resources.map((r, i) => (
                    <li key={i} className="flex items-center gap-1.5">
                      <span aria-hidden="true">{r.verified === 'ok' ? '✅' : '⚠️'}</span>
                      <span className="sr-only">{r.verified === 'ok' ? '已验证' : '不确定'}</span>
                      <a
                        href={r.url}
                        target="_blank"
                        rel="noreferrer"
                        className="truncate text-blue-600 hover:underline"
                      >
                        {r.title || r.url}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="mt-4 flex items-center justify-between gap-2 border-t pt-3">
              <button
                onClick={() => idx > 0 && setCurrentId(units[idx - 1].id)}
                disabled={idx <= 0}
                className="rounded-lg border px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-40"
              >
                上一单元
              </button>
              <div className="flex items-center gap-2">
                <button
                  onClick={toggleDone}
                  disabled={doneBusy}
                  aria-pressed={current.done}
                  className={`rounded-lg px-3 py-1.5 text-sm ${
                    current.done
                      ? 'bg-green-100 text-green-700 hover:bg-green-200'
                      : 'border hover:bg-slate-50'
                  } disabled:opacity-50`}
                >
                  {current.done ? '✓ 已学（点击取消）' : '标记已学'}
                </button>
                <button
                  onClick={onOpenQuiz}
                  className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm text-white hover:bg-emerald-700"
                >
                  去测验
                </button>
              </div>
              <button
                onClick={() => idx < units.length - 1 && setCurrentId(units[idx + 1].id)}
                disabled={idx < 0 || idx >= units.length - 1}
                className="rounded-lg border px-3 py-1.5 text-sm hover:bg-slate-50 disabled:opacity-40"
              >
                下一单元
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

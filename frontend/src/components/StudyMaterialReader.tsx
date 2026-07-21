// 自学材料阅读器（P2，plan/07 §六 / plan/06 §五 StudyMaterialReader）：
// 单元导航 + 内容渲染（react-markdown + sanitize）+ 上一/下一单元 + 标记已学 + 去测验。
// 数据：GET /plans/{id}/materials（列表）+ GET /materials/{unit_id}（内容，选中时按需加载）。
import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import { ApiError, getStudyUnitContent, markUnitDone, saveTextFile, type StudyUnitMeta, type UnitResource } from '../lib/api'
import { isHttpUrl } from '../lib/url'

interface Props {
  units: StudyUnitMeta[]
  indexMd?: string // plan/02 §13 index 文档：导航首部「📑 路线图」伪单元（id=-1）
  onUnitsChange: (units: StudyUnitMeta[]) => void
  onOpenQuiz: () => void
}

const INDEX_ID = -1
// iter 256：阅读器字号调节（A−/A+ 四档，默认标准=text-sm）——localStorage 记忆（iter 145 同款模式，
// 隐私模式等读写失败静默降级）；.reader-font 内标题/表格在 index.css 改 em 相对尺寸，随档位整体缩放
const FONT_STEPS = ['text-xs', 'text-sm', 'text-base', 'text-lg'] as const
const FONT_LABELS = ['较小', '标准', '较大', '特大'] as const
const FONT_STORAGE_KEY = 'dt-reader-font-step'
const FONT_DEFAULT = 1

export default function StudyMaterialReader({ units, indexMd = '', onUnitsChange, onOpenQuiz }: Props) {
  const [currentId, setCurrentId] = useState<number | null>(indexMd ? INDEX_ID : (units[0]?.id ?? null))
  const [fontStep, setFontStep] = useState(() => {
    try {
      const v = Number(localStorage.getItem(FONT_STORAGE_KEY))
      return Number.isInteger(v) && v >= 0 && v < FONT_STEPS.length ? v : FONT_DEFAULT
    } catch {
      return FONT_DEFAULT
    }
  })

  function changeFont(delta: number) {
    setFontStep((s) => {
      const next = Math.min(FONT_STEPS.length - 1, Math.max(0, s + delta))
      try {
        localStorage.setItem(FONT_STORAGE_KEY, String(next))
      } catch {
        // 写失败仅本次会话生效
      }
      return next
    })
  }
  const [content, setContent] = useState('')
  const [contentTitle, setContentTitle] = useState('')
  const [resources, setResources] = useState<UnitResource[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [doneBusy, setDoneBusy] = useState(false)
  const acRef = useRef<AbortController | null>(null)
  // iter 261：滚动位置记忆（会话级）——内容区 key=currentId 切单元即重挂载滚动必丢，
  // 「切去下一单元瞥一眼再切回来从头找」是阅读流断点。仅会话内有效（组件卸载即清）：
  // 不碰 localStorage——跨会话陈旧位置 + 字号调节改变偏移，恢复错误位置比不恢复更糟
  const scrollMemRef = useRef(new Map<number, number>())
  const contentRef = useRef<HTMLDivElement | null>(null)

  const isIndex = currentId === INDEX_ID
  const idx = units.findIndex((u) => u.id === currentId)
  const current = idx >= 0 ? units[idx] : null

  // 选中单元 -> 按需加载内容（卸载/切换中止进行中请求）；index 伪单元用传入的 indexMd 不请求
  useEffect(() => {
    if (currentId === null || currentId === INDEX_ID) return
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

  // iter 261：内容就绪（加载完成/路线图直出）后恢复本单元上次滚动位置；
  // rAF 等一帧布局完成再设 scrollTop，卸载/再切换取消
  useEffect(() => {
    if (loading || currentId === null) return
    const saved = scrollMemRef.current.get(currentId)
    if (!saved) return
    const raf = requestAnimationFrame(() => {
      const el = contentRef.current
      if (el) el.scrollTop = saved
    })
    return () => cancelAnimationFrame(raf)
  }, [loading, currentId])

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

  if (units.length === 0) return <p className="text-sm text-slate-500 dark:text-slate-400">暂无自学单元</p>

  return (
    <div className="flex min-h-0 flex-1 gap-4">
      {/* 单元导航 */}
      <nav aria-label="自学单元" className="w-44 shrink-0 overflow-y-auto border-r pr-2 dark:border-slate-700">
        <ul className="space-y-1">
          {indexMd && (
            <li>
              <button
                onClick={() => setCurrentId(INDEX_ID)}
                aria-current={isIndex ? 'true' : undefined}
                className={`w-full rounded-lg px-2 py-1.5 text-left text-sm transition-colors ${
                  isIndex ? 'bg-blue-50 dark:bg-blue-950 font-medium text-blue-700 dark:text-blue-300' : 'hover:bg-slate-50 dark:hover:bg-slate-700'
                }`}
              >
                📑 路线图
              </button>
            </li>
          )}
          {units.map((u, i) => (
            <li key={u.id} className="animate-fade-in" style={{ animationDelay: `${Math.min(i, 12) * 30}ms` }}>
              <button
                onClick={() => setCurrentId(u.id)}
                aria-current={u.id === currentId ? 'true' : undefined}
                className={`w-full rounded-lg px-2 py-1.5 text-left text-sm transition-colors ${
                  u.id === currentId ? 'bg-blue-50 dark:bg-blue-950 font-medium text-blue-700 dark:text-blue-300' : 'hover:bg-slate-50 dark:hover:bg-slate-700'
                }`}
              >
                <span className="mr-1 text-xs text-slate-500 dark:text-slate-400">{i + 1}.</span>
                {u.title}
                {u.done && (
                  <span className="ml-1 text-xs text-green-600 dark:text-green-400" aria-label="已学">
                    ✓<span className="sr-only">（已学）</span>
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      </nav>

      {/* 内容区（iter 101：切单元按 key 淡入；iter 256：reader-font 字号调节作用域；
          iter 261：ref+onScroll 滚动位置会话级记忆/恢复） */}
      <div
        key={currentId}
        ref={contentRef}
        onScroll={(e) => {
          if (currentId !== null) scrollMemRef.current.set(currentId, e.currentTarget.scrollTop)
        }}
        className="reader-font min-w-0 flex-1 animate-fade-in overflow-y-auto"
      >
        {/* iter 256：字号调节工具条（路线图/单元两视图同位置） */}
        <div
          className="mb-2 flex items-center justify-end gap-1"
          role="group"
          aria-label="正文字号"
          title={`正文字号：${FONT_LABELS[fontStep]}`}
        >
          <button
            onClick={() => changeFont(-1)}
            disabled={fontStep <= 0}
            aria-label="减小字号"
            className="rounded border px-2 py-0.5 text-xs transition-colors hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700 disabled:opacity-40"
          >
            A−
          </button>
          <button
            onClick={() => changeFont(1)}
            disabled={fontStep >= FONT_STEPS.length - 1}
            aria-label="增大字号"
            className="rounded border px-2 py-0.5 text-xs transition-colors hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700 disabled:opacity-40"
          >
            A+
          </button>
        </div>
        {isIndex ? (
          <>
            <div className={`markdown-body ${FONT_STEPS[fontStep]}`}>
              <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
                {indexMd}
              </ReactMarkdown>
            </div>
            <div className="mt-4 flex items-center justify-end border-t pt-3 dark:border-slate-700">
              <button
                onClick={() => units.length > 0 && setCurrentId(units[0].id)}
                className="rounded-lg border px-3 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
              >
                开始学习 →
              </button>
            </div>
          </>
        ) : (
          <>
            {loading && (
              <p className="flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400" role="status">
                <span aria-hidden="true" className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600 dark:border-slate-600 dark:border-t-blue-400" />
                加载单元内容…
              </p>
            )}
            {error && (
              <div role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950 p-3 text-sm text-red-700 dark:text-red-300">
                {error}
              </div>
            )}
            {!loading && !error && current && (
          <>
            <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
              {current.estimated_time && <span>预计 {current.estimated_time}</span>}
              {current.objectives.length > 0 && <span>目标：{current.objectives.join('；')}</span>}
            </div>
            <div className={`markdown-body ${FONT_STEPS[fontStep]}`}>
              <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
                {content || `## ${contentTitle}\n\n（本单元暂无内容）`}
              </ReactMarkdown>
            </div>
            {resources.length > 0 && (
              <div className="mt-3 rounded-lg border border-slate-200 p-3 dark:border-slate-700">
                <h4 className="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">参考资料</h4>
                <ul className="space-y-1 text-sm">
                  {resources.map((r, i) => (
                    <li key={i} className="flex items-center gap-1.5">
                      <span aria-hidden="true">{r.verified === 'ok' ? '✅' : '⚠️'}</span>
                      <span className="sr-only">{r.verified === 'ok' ? '已验证' : '不确定'}</span>
                      {isHttpUrl(r.url) ? (
                        <a
                          href={r.url}
                          target="_blank"
                          rel="noreferrer"
                          className="truncate text-blue-600 underline-offset-2 transition-colors hover:text-blue-500 hover:underline dark:text-blue-400 dark:hover:text-blue-300"
                        >
                          {r.title || r.url}
                        </a>
                      ) : (
                        <span className="truncate text-slate-600 dark:text-slate-300">{r.title || r.url}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <div className="mt-4 flex items-center justify-between gap-2 border-t pt-3 dark:border-slate-700">
              <button
                onClick={() => idx > 0 && setCurrentId(units[idx - 1].id)}
                disabled={idx <= 0}
                className="rounded-lg border px-3 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700 disabled:opacity-40 disabled:active:scale-100"
              >
                上一单元
              </button>
              <div className="flex items-center gap-2">
                <button
                  onClick={() =>
                    // iter 99：单单元导出为 .md（用已加载内容，零额外请求）
                    saveTextFile(content || `# ${contentTitle}\n\n（本单元暂无内容）`, `${contentTitle || '单元'}.md`)
                  }
                  className="rounded-lg border px-3 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
                >
                  下载 .md
                </button>
                <button
                  onClick={toggleDone}
                  disabled={doneBusy}
                  aria-pressed={current.done}
                  className={`rounded-lg px-3 py-1.5 text-sm transition-all active:scale-95 ${
                    current.done
                      ? 'bg-green-100 dark:bg-green-900 text-green-700 dark:text-green-300 hover:bg-green-200 dark:hover:bg-green-800'
                      : 'border hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700'
                  } disabled:opacity-50 disabled:active:scale-100`}
                >
                  {current.done ? '✓ 已学（点击取消）' : '标记已学'}
                </button>
                <button
                  onClick={onOpenQuiz}
                  className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm text-white shadow-sm transition-all hover:bg-emerald-700 hover:shadow-md active:scale-[.98]"
                >
                  去测验
                </button>
              </div>
              <button
                onClick={() => idx < units.length - 1 && setCurrentId(units[idx + 1].id)}
                disabled={idx < 0 || idx >= units.length - 1}
                className="rounded-lg border px-3 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700 disabled:opacity-40 disabled:active:scale-100"
              >
                下一单元
              </button>
            </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// 自学材料阅读器（P2，plan/07 §六 / plan/06 §五 StudyMaterialReader）：
// 单元导航 + 内容渲染（react-markdown + sanitize）+ 上一/下一单元 + 标记已学 + 去测验。
// 数据：GET /plans/{id}/materials（列表）+ GET /materials/{unit_id}（内容，选中时按需加载）。
import { useEffect, useMemo, useRef, useState } from 'react'
import Markdown from './Markdown'
import { ApiError, downloadUnitSlides, generateMaterialsStream, getStudyUnitContent, markUnitDone, saveTextFile, searchStudyUnits, type StudyUnitMeta, type UnitResource, type UnitSearchHit } from '../lib/api'
import { isHttpUrl } from '../lib/url'
import { toast } from './Toast'

interface Props {
  planId: number // iter 269：单元内容检索需要
  units: StudyUnitMeta[]
  indexMd?: string // plan/02 §13 index 文档：导航首部「📑 路线图」伪单元（id=-1）
  onUnitsChange: (units: StudyUnitMeta[]) => void
  onOpenQuiz: () => void
}

const INDEX_ID = -1

// iter 262：单元内目录（本节导航）——从 markdown 提取 h1-h3 大纲，点击滚动到对应标题。
// 无需标题 id（rehype-sanitize schema 不保证保留 id 属性）：提取顺序与渲染容器内
// querySelectorAll('h1,h2,h3') 文档序一一对齐，按索引 scrollIntoView。
interface TocItem {
  level: number
  text: string
}

function extractToc(md: string): TocItem[] {
  const out: TocItem[] = []
  let inCode = false
  let prevText = '' // setext 标题（===/--- 下划线式）兜底：上一非空纯文本行
  for (const line of md.split('\n')) {
    if (/^\s*```/.test(line)) {
      inCode = !inCode
      prevText = ''
      continue
    }
    if (inCode) continue
    const atx = /^(#{1,3})\s+(.+?)\s*#*\s*$/.exec(line)
    if (atx) {
      const text = atx[2].replace(/\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/[*_`~]/g, '').trim()
      if (text) out.push({ level: atx[1].length, text })
      prevText = ''
      continue
    }
    if (/^(=+|-+)\s*$/.test(line) && prevText) {
      out.push({ level: line.startsWith('=') ? 1 : 2, text: prevText })
      prevText = ''
      continue
    }
    // 纯文本行才可能是 setext 标题上一行（排除列表/引用/表格/空行）
    prevText = /^[^#>\-*|\s].*\S/.test(line)
      ? line.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/[*_`~]/g, '').trim()
      : ''
  }
  return out.slice(0, 50) // 防御：畸形文档超长目录截断
}

// iter 256：阅读器字号调节（A−/A+ 四档，默认标准=text-sm）——localStorage 记忆（iter 145 同款模式，
// 隐私模式等读写失败静默降级）；.reader-font 内标题/表格在 index.css 改 em 相对尺寸，随档位整体缩放
const FONT_STEPS = ['text-xs', 'text-sm', 'text-base', 'text-lg'] as const
const FONT_LABELS = ['较小', '标准', '较大', '特大'] as const
const FONT_STORAGE_KEY = 'dt-reader-font-step'
const FONT_DEFAULT = 1

export default function StudyMaterialReader({ planId, units, indexMd = '', onUnitsChange, onOpenQuiz }: Props) {
  const [currentId, setCurrentId] = useState<number | null>(indexMd ? INDEX_ID : (units[0]?.id ?? null))
  // iter 269：单元内容检索（「这个方案里哪里讲过 X」）——300ms 防抖，命中列表替换单元导航；
  // 点击命中打开该单元；检索期间保留查询便于连试多个命中（✕ 清空回导航）
  const [unitQuery, setUnitQuery] = useState('')
  const [debouncedUnitQ, setDebouncedUnitQ] = useState('')
  const [unitHits, setUnitHits] = useState<UnitSearchHit[] | null>(null)
  const [unitSearchTruncated, setUnitSearchTruncated] = useState(false)
  useEffect(() => {
    const t = setTimeout(() => setDebouncedUnitQ(unitQuery.trim()), 300)
    return () => clearTimeout(t)
  }, [unitQuery])
  useEffect(() => {
    if (!debouncedUnitQ) {
      setUnitHits(null)
      setUnitSearchTruncated(false)
      return
    }
    let cancelled = false
    searchStudyUnits(planId, debouncedUnitQ)
      .then((r) => {
        if (cancelled) return
        setUnitHits(r.items)
        setUnitSearchTruncated(r.truncated)
      })
      .catch(() => {
        if (!cancelled) setUnitHits([]) // 检索失败按无命中呈现（导航仍可用——清空即回）
      })
    return () => {
      cancelled = true
    }
  }, [planId, debouncedUnitQ])
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
  const [pptBusy, setPptBusy] = useState(false) // iter 359：单元导出 HTML-PPT 进行中
  // iter 346（F6）：阅读内重写本单元——复用 generateMaterialsStream（单单元 unitIds + depth）
  const [showRewrite, setShowRewrite] = useState(false)
  const [rewriteDepth, setRewriteDepth] = useState<'brief' | 'full'>('full')
  const [rewriting, setRewriting] = useState(false)
  const [rewriteMsg, setRewriteMsg] = useState('')
  const [rewriteErr, setRewriteErr] = useState('')
  const rewriteAcRef = useRef<AbortController | null>(null)
  const acRef = useRef<AbortController | null>(null)
  // iter 261：滚动位置记忆（会话级）——内容区 key=currentId 切单元即重挂载滚动必丢，
  // 「切去下一单元瞥一眼再切回来从头找」是阅读流断点。仅会话内有效（组件卸载即清）：
  // 不碰 localStorage——跨会话陈旧位置 + 字号调节改变偏移，恢复错误位置比不恢复更糟
  const scrollMemRef = useRef(new Map<number, number>())
  const contentRef = useRef<HTMLDivElement | null>(null)

  const isIndex = currentId === INDEX_ID
  const idx = units.findIndex((u) => u.id === currentId)
  const current = idx >= 0 ? units[idx] : null
  const doneCount = units.filter((u) => u.done).length // iter 265：导航顶部已学进度

  // iter 262：当前视图（路线图/单元）markdown 的标题大纲
  const toc = useMemo(() => extractToc(isIndex ? indexMd : content), [isIndex, indexMd, content])

  // iter 262：目录跳转——提取顺序与渲染标题文档序对齐，按索引 scrollIntoView
  function jumpToHeading(index: number) {
    const heads = contentRef.current?.querySelectorAll('.markdown-body h1, .markdown-body h2, .markdown-body h3')
    heads?.[index]?.scrollIntoView({ block: 'start' })
  }

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
    if (pendingLocateRef.current?.unitId === currentId) return // iter 271：检索定位优先于位置恢复
    const saved = scrollMemRef.current.get(currentId)
    if (!saved) return
    const raf = requestAnimationFrame(() => {
      const el = contentRef.current
      if (el) el.scrollTop = saved
    })
    return () => cancelAnimationFrame(raf)
  }, [loading, currentId])

  // iter 271：内容命中定位（iter 269 闭环补全）——点击检索「内容」命中后单元打开，
  // 但命中位置不可见（用户须在长文里再人工找一次）。加载完成后在渲染 DOM 找首个命中
  // 文本节点：包临时 <mark> 高亮 + 滚动到视口中央。ref 带 unitId 防竞态
  // （点击命中后未加载完又手动切单元，定位不会落在错单元）。
  // mark 直接操作 DOM：react-markdown 子树仅在 content 变化时重建（命中标记随之自然消失），
  // 容器 className（字号档）变化不重建子树，标记存活期内稳定。
  const pendingLocateRef = useRef<{ unitId: number; q: string } | null>(null)

  function clearLocateMarks() {
    contentRef.current?.querySelectorAll('mark[data-locate-hit]').forEach((m) => {
      const parent = m.parentNode
      if (!parent) return
      parent.replaceChild(document.createTextNode(m.textContent ?? ''), m)
      parent.normalize() // 合并相邻文本节点，防反复定位后 DOM 碎片化
    })
  }

  function locateFirstHit(q: string) {
    const root = contentRef.current
    if (!root || !q) return
    const needle = q.toLowerCase()
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
    let node = walker.nextNode() as Text | null
    while (node) {
      const text = node.data
      const idx = text.toLowerCase().indexOf(needle)
      if (idx >= 0 && text.trim()) {
        clearLocateMarks() // 同单元重复定位：先清上一处
        const hit = node.splitText(idx) // hit 以命中词开头
        hit.splitText(q.length) // hit 截为恰好命中词
        const mark = document.createElement('mark')
        mark.setAttribute('data-locate-hit', '1')
        mark.className = 'rounded-sm bg-yellow-200 px-0 dark:bg-yellow-800'
        node.parentNode?.replaceChild(mark, hit)
        mark.appendChild(hit)
        mark.scrollIntoView({ block: 'center' })
        return
      }
      node = walker.nextNode() as Text | null
    }
  }

  // 单元加载完成后执行挂起定位（rAF 等一帧布局）；查询清空时清残留标记
  useEffect(() => {
    if (loading || currentId === null) return
    const pending = pendingLocateRef.current
    if (!pending || pending.unitId !== currentId) return
    pendingLocateRef.current = null
    const raf = requestAnimationFrame(() => locateFirstHit(pending.q))
    return () => cancelAnimationFrame(raf)
  }, [loading, currentId, content])
  useEffect(() => {
    if (!debouncedUnitQ) clearLocateMarks()
  }, [debouncedUnitQ])

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

  // iter 359：当前单元导出为 HTML 幻灯片（借助 ppt-slide-builder，后端 LLM 生成 + 确定性兜底）。
  // 与「下载 .md」并列：.md 是纯文本快照，PPT 是可演示幻灯片（后端生成，故有 busy/失败反馈）。
  async function downloadPpt() {
    if (!current || pptBusy) return
    setPptBusy(true)
    try {
      await downloadUnitSlides(current.id, contentTitle || current.title)
      toast.success('HTML 幻灯片已开始下载')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'PPT 导出失败，请重试')
    } finally {
      setPptBusy(false)
    }
  }

  // iter 346（F6）：阅读内重写本单元——后端 /plans/{id}/materials 已支持 unit_ids 单元替换 +
  // depth（full 详细展开 / brief 简单说说）+ SSE 逐单元；完成后重取本单元内容刷新。
  // 对齐「Partial Result Regeneration」局部重写 UX（§八 J）——对选中单元细粒度重写，非整体重生。
  async function startRewrite() {
    if (!current) return
    setRewriteErr('')
    setRewriting(true)
    setRewriteMsg('正在重写…')
    rewriteAcRef.current?.abort()
    const ac = new AbortController()
    rewriteAcRef.current = ac
    try {
      await generateMaterialsStream(planId, rewriteDepth, false, {
        onUnitStart: (d) => setRewriteMsg(`正在重写「${d.title}」…`),
        onUnitDone: (d) => setRewriteMsg(d.degraded ? `「${d.title}」已重写（降级）` : `「${d.title}」已重写`),
      }, ac.signal, [current.id])
      if (ac.signal.aborted) return
      setRewriteMsg('重写完成，正在刷新…')
      // currentId 未变，加载 effect 不会自动重跑 -> 手动重取内容刷新
      const r = await getStudyUnitContent(current.id, ac.signal)
      if (!ac.signal.aborted) {
        setContent(r.content_md)
        setContentTitle(r.title)
        setResources(r.resources ?? [])
        setRewriting(false)
        setShowRewrite(false)
        setRewriteMsg('')
      }
    } catch (e) {
      if (ac.signal.aborted) {
        setRewriting(false)
        setRewriteMsg('')
        return
      }
      setRewriteErr(e instanceof ApiError ? e.message : '重写失败')
      setRewriting(false)
    }
  }

  if (units.length === 0) return <p className="text-sm text-slate-500 dark:text-slate-400">暂无自学单元</p>

  return (
    <div className="flex min-h-0 flex-1 gap-4">
      {/* 单元导航 */}
      <nav aria-label="自学单元" className="w-44 shrink-0 overflow-y-auto border-r pr-2 dark:border-slate-700">
        {/* iter 265：已学进度（导航顶部）——每单元 ✓ 之外的整体方位感与完成激励；
            路线图伪单元不计入分母（它不是学习单元） */}
        <div className="mb-2 border-b pb-2 dark:border-slate-700">
          <div className="flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
            <span>
              已学 <span className="tabular-nums">{doneCount}</span>/<span className="tabular-nums">{units.length}</span>
            </span>
            {doneCount === units.length && (
              <span className="text-emerald-600 dark:text-emerald-400">🎉 全部完成</span>
            )}
          </div>
          <div
            className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-700"
            role="progressbar"
            aria-valuenow={Math.round((doneCount / units.length) * 100)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`已学进度 ${doneCount}/${units.length} 单元`}
          >
            <div
              className="h-full rounded-full bg-emerald-500 transition-all duration-500"
              style={{ width: `${Math.round((doneCount / units.length) * 100)}%` }}
            />
          </div>
        </div>
        {/* iter 269：单元内容检索——输入即搜（300ms 防抖）；命中列表替换导航，✕ 清空恢复 */}
        <div className="relative mb-2">
          <input
            type="search"
            value={unitQuery}
            onChange={(e) => setUnitQuery(e.target.value)}
            placeholder="搜单元内容…"
            aria-label="搜索本方案单元内容"
            className="w-full rounded-lg border px-2 py-1 text-xs transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500/60 dark:border-slate-600 dark:bg-slate-800"
          />
          {unitQuery && (
            <button
              type="button"
              onClick={() => setUnitQuery('')}
              aria-label="清空搜索"
              className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded px-1 text-xs text-slate-400 transition-colors hover:text-slate-600 dark:hover:text-slate-200"
            >
              ✕
            </button>
          )}
        </div>
        {unitHits !== null ? (
          <ul className="space-y-1" aria-label={`单元内容检索结果，${unitHits.length} 个命中`}>
            {unitHits.length === 0 && (
              <li className="px-1 text-xs text-slate-400 dark:text-slate-500">无命中单元</li>
            )}
            {unitSearchTruncated && (
              <li className="px-1 text-[10px] text-amber-600 dark:text-amber-400">单元较多，仅扫描前 50 个</li>
            )}
            {unitHits.map((h) => (
              <li key={h.unit_id}>
                <button
                  onClick={() => {
                    // iter 271：内容命中 -> 挂起定位（加载完成后滚动到命中并高亮）；
                    // 已在该单元则无需等加载立即定位；标题命中无定位必要
                    if (h.match === 'content') {
                      if (h.unit_id === currentId && !loading) {
                        locateFirstHit(debouncedUnitQ)
                      } else {
                        pendingLocateRef.current = { unitId: h.unit_id, q: debouncedUnitQ }
                      }
                    }
                    setCurrentId(h.unit_id)
                  }}
                  aria-current={h.unit_id === currentId ? 'true' : undefined}
                  className={`w-full rounded-lg px-2 py-1.5 text-left text-sm transition-colors ${
                    h.unit_id === currentId ? 'bg-blue-50 dark:bg-blue-950 font-medium text-blue-700 dark:text-blue-300' : 'hover:bg-slate-50 dark:hover:bg-slate-700'
                  }`}
                >
                  <span className="flex items-center gap-1">
                    <span className="min-w-0 truncate">{h.title}</span>
                    {h.match === 'content' && (
                      <span className="shrink-0 rounded bg-emerald-100 px-1 text-[10px] text-emerald-700 dark:bg-emerald-900 dark:text-emerald-300">内容</span>
                    )}
                  </span>
                  {h.snippet && (
                    <span className="mt-0.5 line-clamp-2 block text-xs text-slate-400 dark:text-slate-500">{h.snippet}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        ) : (
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
        )}
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
        {/* iter 262：本节导航——≥3 个标题才显示（短内容无导航价值）；加载/出错期间隐藏防过期目录 */}
        {!loading && !error && toc.length >= 3 && (
          <details className="mb-2 rounded-lg border border-slate-200 px-3 py-1.5 text-xs dark:border-slate-700">
            <summary className="cursor-pointer select-none font-medium text-slate-500 transition-colors hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200">
              📑 本节导航（{toc.length}）
            </summary>
            <ul className="mt-1.5 space-y-0.5">
              {toc.map((h, i) => (
                <li key={i} style={{ paddingLeft: `${(h.level - 1) * 12}px` }}>
                  <button
                    onClick={() => jumpToHeading(i)}
                    className="w-full truncate text-left text-slate-600 transition-colors hover:text-blue-600 dark:text-slate-300 dark:hover:text-blue-400"
                  >
                    {h.text}
                  </button>
                </li>
              ))}
            </ul>
          </details>
        )}
        {isIndex ? (
          <>
            <div className={`markdown-body ${FONT_STEPS[fontStep]}`}>
              <Markdown>{indexMd}</Markdown>
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
              <Markdown>{content || `## ${contentTitle}\n\n（本单元暂无内容）`}</Markdown>
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
                  onClick={downloadPpt}
                  disabled={pptBusy}
                  title="导出本单元为 HTML 幻灯片（借助 ppt-slide-builder）"
                  className="rounded-lg border px-3 py-1.5 text-sm transition-all hover:bg-slate-50 active:scale-95 disabled:opacity-50 disabled:active:scale-100 dark:border-slate-600 dark:hover:bg-slate-700"
                >
                  {pptBusy ? '生成中…' : '下载 PPT'}
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
                  onClick={() => {
                    setShowRewrite(true)
                    setRewriteErr('')
                  }}
                  disabled={rewriting}
                  className="rounded-lg border border-amber-300 px-3 py-1.5 text-sm text-amber-700 transition-all hover:bg-amber-50 active:scale-95 disabled:opacity-50 dark:border-amber-800 dark:text-amber-300 dark:hover:bg-amber-950"
                  title="重写本单元（详细展开 / 简单说说）"
                >
                  ✍️ 重写
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
            {showRewrite && current && (
              <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50/60 p-3 text-sm dark:border-amber-900 dark:bg-amber-950/40">
                <div className="mb-2 font-medium text-amber-800 dark:text-amber-200">重写「{current.title}」</div>
                <div className="mb-2 flex flex-wrap items-center gap-3">
                  <span className="text-slate-600 dark:text-slate-300">深度：</span>
                  <label className="flex items-center gap-1">
                    <input
                      type="radio"
                      name={`rewriteDepth-${current.id}`}
                      checked={rewriteDepth === 'full'}
                      onChange={() => setRewriteDepth('full')}
                      disabled={rewriting}
                    />
                    详细展开
                  </label>
                  <label className="flex items-center gap-1">
                    <input
                      type="radio"
                      name={`rewriteDepth-${current.id}`}
                      checked={rewriteDepth === 'brief'}
                      onChange={() => setRewriteDepth('brief')}
                      disabled={rewriting}
                    />
                    简单说说
                  </label>
                </div>
                {rewriting ? (
                  <div className="flex items-center gap-2">
                    <span className="animate-pulse text-amber-700 dark:text-amber-300">
                      {rewriteMsg || '正在重写…'}
                    </span>
                    <button
                      type="button"
                      onClick={() => rewriteAcRef.current?.abort()}
                      className="rounded border border-amber-300 px-2 py-0.5 text-xs hover:bg-amber-100 dark:border-amber-700 dark:hover:bg-amber-900"
                    >
                      取消
                    </button>
                  </div>
                ) : (
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={startRewrite}
                      className="rounded bg-amber-600 px-3 py-1 text-xs text-white hover:bg-amber-700"
                    >
                      开始重写
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setShowRewrite(false)
                        setRewriteErr('')
                      }}
                      className="rounded border border-amber-300 px-3 py-1 text-xs hover:bg-amber-100 dark:border-amber-700 dark:hover:bg-amber-900"
                    >
                      取消返回
                    </button>
                    {rewriteErr && <span className="text-red-600 dark:text-red-400">{rewriteErr}</span>}
                  </div>
                )}
              </div>
            )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}

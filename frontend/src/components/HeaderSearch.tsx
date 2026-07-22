// iter 289：顶栏全局搜索 + 输入建议（typeahead）。
// 此前顶栏搜索须 Enter 跳 /search 页才见结果；本组件输入即出跨域建议（方案/知识点/笔记/知识库文档），
// 点击或键盘直达。从 Layout 抽出为独立组件：combobox 的焦点/键盘/点击外部边界封装于此，
// 降低应用壳（Layout 每页渲染）的爆炸半径；Enter 仍可全量搜索（/search 页回退）。
// 防抖 250ms + ≥2 字 + abort 前次；限流(30/min)/网络错误静默关下拉（Enter 仍可走全量）。
import { useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent as ReactKeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { globalSearch, type GlobalSearchResult } from '../lib/api'

interface Suggestion {
  key: string
  icon: string
  label: string
  title: string
  snippet?: string
  route: string
}

function toSuggestions(data: GlobalSearchResult): Suggestion[] {
  const out: Suggestion[] = []
  for (const p of data.plans.items)
    out.push({ key: `plan-${p.id}`, icon: '📚', label: '方案', title: p.topic || `方案 #${p.id}`, route: `/plans/${p.id}` })
  for (const n of data.nodes.items)
    out.push({ key: `node-${n.id}`, icon: '🧠', label: '知识点', title: n.name, snippet: n.plan_topic || undefined, route: `/plans/${n.plan_id}` })
  for (const n of data.notes.items)
    out.push({ key: `note-${n.id}`, icon: '📝', label: '笔记', title: n.title || '（无标题）', snippet: n.snippet || undefined, route: `/notes?note=${n.id}` })
  for (const d of data.kb_docs.items)
    out.push({ key: `kb-${d.id}-${d.match}`, icon: '🗄️', label: '文档', title: d.title || `文档 #${d.id}`, snippet: d.snippet ?? undefined, route: `/wiki?doc=${d.id}` })
  return out
}

const MIN_CHARS = 2
const DEBOUNCE_MS = 250

export default function HeaderSearch() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [highlight, setHighlight] = useState(-1) // -1 = 无高亮，Enter 走全量 /search
  const inputRef = useRef<HTMLInputElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // `/` 快捷键聚焦（从 Layout 迁入；输入态/修饰键/输入法组合态/弹窗不劫持；隐藏时退化跳 /search）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
      if (document.querySelector('[role="dialog"]')) return
      e.preventDefault() // 防 '/' 触发浏览器快速查找（Firefox）
      const input = inputRef.current
      if (input && input.offsetParent !== null) input.focus()
      else navigate('/search')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigate])

  // 防抖 typeahead
  useEffect(() => {
    const q = query.trim()
    if (q.length < MIN_CHARS) {
      abortRef.current?.abort()
      setSuggestions([])
      setOpen(false)
      setLoading(false)
      setHighlight(-1)
      return
    }
    setLoading(true)
    const t = window.setTimeout(() => {
      abortRef.current?.abort()
      const ac = new AbortController()
      abortRef.current = ac
      globalSearch(q, 3, ac.signal)
        .then((data) => {
          if (ac.signal.aborted) return
          const sugg = toSuggestions(data)
          setSuggestions(sugg)
          setHighlight(-1)
          setOpen(true)
        })
        .catch(() => {
          if (ac.signal.aborted) return
          // 限流/网络错误：静默关下拉（Enter 仍可走 /search 全量）
          setSuggestions([])
          setOpen(false)
        })
        .finally(() => {
          if (!ac.signal.aborted) setLoading(false)
        })
    }, DEBOUNCE_MS)
    return () => window.clearTimeout(t)
  }, [query])

  // 点击外部关闭
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  function go(s: Suggestion | undefined) {
    setOpen(false)
    if (s) navigate(s.route)
    else if (query.trim()) navigate(`/search?q=${encodeURIComponent(query.trim())}`)
  }

  function onInputKeyDown(e: ReactKeyboardEvent<HTMLInputElement>) {
    if (e.nativeEvent.isComposing) {
      if (e.key === 'Enter') e.preventDefault() // IME 候选确认不触发提交（iter 248 同款）
      return
    }
    if (e.key === 'ArrowDown' && suggestions.length > 0) {
      e.preventDefault()
      setOpen(true)
      setHighlight((h) => (h + 1) % suggestions.length)
    } else if (e.key === 'ArrowUp' && suggestions.length > 0) {
      e.preventDefault()
      setOpen(true)
      setHighlight((h) => (h - 1 + suggestions.length) % suggestions.length)
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
    // Enter：有高亮则开该项（form onSubmit 兜底全量 /search）
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (highlight >= 0 && suggestions[highlight]) go(suggestions[highlight])
    else if (query.trim()) {
      navigate(`/search?q=${encodeURIComponent(query.trim())}`)
      setOpen(false)
    }
  }

  const showList = open && (suggestions.length > 0 || loading)

  return (
    <div ref={containerRef} className="relative hidden md:block">
      <form role="search" onSubmit={onSubmit}>
        <input
          ref={inputRef}
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onInputKeyDown}
          onFocus={() => {
            if (suggestions.length > 0) setOpen(true)
          }}
          placeholder="全局搜索…"
          aria-label="全局搜索"
          title="全局搜索（快捷键 /，输入即出建议）"
          maxLength={100}
          role="combobox"
          aria-expanded={showList}
          aria-controls="header-search-listbox"
          aria-activedescendant={highlight >= 0 ? suggestions[highlight]?.key : undefined}
          autoComplete="off"
          className="w-44 rounded-lg border bg-slate-50 px-2.5 py-1 text-sm transition-all focus:w-64 focus:bg-white focus:outline-none dark:border-slate-600 dark:bg-slate-700 dark:focus:bg-slate-800"
        />
      </form>
      {showList && (
        <div
          id="header-search-listbox"
          role="listbox"
          aria-label="搜索建议"
          className="absolute right-0 z-50 mt-1 max-h-80 w-80 overflow-y-auto rounded-lg border bg-white shadow-lg dark:border-slate-700 dark:bg-slate-800"
        >
          {loading && suggestions.length === 0 && <div className="px-3 py-2 text-xs text-slate-400">搜索中…</div>}
          {suggestions.map((s, i) => (
            <button
              key={s.key}
              type="button"
              onClick={() => go(s)}
              onMouseEnter={() => setHighlight(i)}
              role="option"
              aria-selected={i === highlight}
              className={`flex w-full items-start gap-2 px-3 py-2 text-left transition-colors ${
                i === highlight ? 'bg-blue-50 dark:bg-blue-950' : 'hover:bg-slate-50 dark:hover:bg-slate-700'
              }`}
            >
              <span aria-hidden="true" className="mt-0.5 shrink-0 text-sm">{s.icon}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-slate-800 dark:text-slate-100">{s.title}</span>
                {s.snippet && <span className="block truncate text-xs text-slate-500 dark:text-slate-400">{s.snippet}</span>}
              </span>
              <span className="shrink-0 text-[10px] text-slate-400">{s.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

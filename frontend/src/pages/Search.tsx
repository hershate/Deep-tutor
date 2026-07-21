// 全局搜索页（iter 249）：消费 GET /api/search——方案/笔记/知识库文档/知识节点四域聚合。
// URL ?q= 为唯一查询源（顶栏搜索框/本页输入/前进后退共享）；命中关键词 <mark> 高亮（首个命中）。
// 深链落点：方案/节点 -> 方案详情；笔记 -> /notes?note=<id>（Notes 页读取并打开）；文档 -> /wiki?doc=<id>（iter 257：KbDocsView 消费深链直接打开查看器）。
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { globalSearch, type GlobalSearchResult } from '../lib/api'
import Card from '../components/Card'
import EmptyState from '../components/EmptyState'
import { SkeletonLines } from '../components/Skeleton'

/** 首个命中（大小写不敏感）包 <mark>；无命中原样渲染。 */
function Highlight({ text, q }: { text: string; q: string }) {
  const needle = q.trim().toLowerCase()
  if (!needle) return <>{text}</>
  const idx = text.toLowerCase().indexOf(needle)
  if (idx < 0) return <>{text}</>
  return (
    <>
      {text.slice(0, idx)}
      <mark className="rounded-sm bg-yellow-200 px-0 dark:bg-yellow-800">{text.slice(idx, idx + needle.length)}</mark>
      {text.slice(idx + needle.length)}
    </>
  )
}

const KIND_LABEL: Record<string, string> = {
  file: '文件', text: '文本', url: '链接', image: '图片', audio: '音频', video: '视频',
}

export default function Search() {
  const [params, setParams] = useSearchParams()
  const q = (params.get('q') ?? '').trim()
  const [input, setInput] = useState(q)
  // URL 变化（前进/后退/顶栏提交）同步输入框
  useEffect(() => setInput(q), [q])

  const { data, isLoading, isError, error, refetch } = useQuery<GlobalSearchResult>({
    queryKey: ['global-search', q],
    queryFn: () => globalSearch(q),
    enabled: q.length > 0,
    staleTime: 10_000,
    retry: 1,
  })

  const totalAll = useMemo(
    () => (data ? data.plans.total + data.notes.total + data.kb_docs.total + data.nodes.total : 0),
    [data],
  )

  function submit(e: React.FormEvent) {
    e.preventDefault()
    const v = input.trim()
    setParams(v ? { q: v } : {})
  }

  // iter 270：结果键盘导航——iter 264「/」聚焦搜索后的闭环（/ → 输入 → Enter → ↓ → Enter 全程键盘）。
  // 实际焦点漫游（非 aria-activedescendant：读屏器跟随真实焦点最可靠）：
  // 输入框 ↓ -> 首条结果；结果间 ↑↓ 移动；首条 ↑/任意位置 Esc -> 回输入框。
  // 事件委托在结果容器（免逐链接注册）；data-search-result 标记参与漫游的锚点。
  const inputRef = useRef<HTMLInputElement | null>(null)
  const resultsRef = useRef<HTMLDivElement | null>(null)

  function resultLinks(): HTMLAnchorElement[] {
    return Array.from(resultsRef.current?.querySelectorAll<HTMLAnchorElement>('a[data-search-result]') ?? [])
  }

  function onInputKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    // iter 248 同款：CJK 输入法候选词确认的 Enter 不提交
    if (e.key === 'Enter' && e.nativeEvent.isComposing) {
      e.preventDefault()
      return
    }
    if (e.key === 'ArrowDown') {
      const first = resultLinks()[0]
      if (first) {
        e.preventDefault() // 防光标移到行尾
        first.focus()
      }
    }
  }

  function onResultsKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    const t = e.target as HTMLElement
    if (t.tagName !== 'A' || !t.hasAttribute('data-search-result')) return
    const links = resultLinks()
    const idx = links.indexOf(t as HTMLAnchorElement)
    if (idx < 0) return
    if (e.key === 'ArrowDown' && idx < links.length - 1) {
      e.preventDefault()
      links[idx + 1].focus()
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (idx > 0) links[idx - 1].focus()
      else inputRef.current?.focus() // 首条再 ↑ 回输入框
    } else if (e.key === 'Escape') {
      e.preventDefault()
      inputRef.current?.focus()
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">全局搜索</h1>
      <form onSubmit={submit} role="search" className="flex gap-2">
        <input
          ref={inputRef}
          type="search"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onInputKeyDown}
          placeholder="搜索方案、笔记、知识库文档、知识节点…"
          aria-label="全局搜索"
          maxLength={100}
          autoFocus
          className="w-full rounded-lg border bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
        />
        <button
          type="submit"
          className="shrink-0 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
        >
          搜索
        </button>
      </form>

      {!q && (
        <EmptyState icon="🔍" title="输入关键词开始搜索" description="一次搜索方案、笔记、知识库文档与知识节点" />
      )}

      {q && isLoading && <SkeletonLines lines={4} />}

      {q && isError && (
        <Card className="border-red-200 dark:border-red-900">
          <p className="text-sm text-red-600 dark:text-red-400">
            搜索失败：{error instanceof Error ? error.message : '未知错误'}
          </p>
          <button type="button" onClick={() => refetch()} className="mt-2 text-sm text-blue-600 underline underline-offset-2 dark:text-blue-400">
            重试
          </button>
        </Card>
      )}

      {q && data && totalAll === 0 && (
        <EmptyState icon="🗂️" title={`没有找到与「${q}」相关的内容`} description="换个关键词试试；搜索范围为方案主题、笔记标题与正文、文档标题、知识节点名" />
      )}

      {q && data && totalAll > 0 && (
        <div className="flex flex-col gap-4" ref={resultsRef} onKeyDown={onResultsKeyDown}>
          <p className="text-xs text-slate-500 dark:text-slate-400" role="status">
            共 {totalAll} 条结果 · ↑↓ 在结果间移动，Enter 打开，Esc 回搜索框
          </p>

          {data.plans.total > 0 && (
            <section aria-label="方案搜索结果">
              <h2 className="mb-2 text-sm font-semibold text-slate-700 dark:text-slate-200">📚 方案（{data.plans.total}）</h2>
              <div className="flex flex-col gap-2">
                {data.plans.items.map((p) => (
                  <Link key={p.id} to={`/plans/${p.id}`} data-search-result>
                    <Card interactive>
                      <p className="text-sm font-medium"><Highlight text={p.topic || `方案 #${p.id}`} q={q} /></p>
                      <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">v{p.version} · {p.created_at.slice(0, 10)}</p>
                    </Card>
                  </Link>
                ))}
              </div>
            </section>
          )}

          {data.nodes.total > 0 && (
            <section aria-label="知识节点搜索结果">
              <h2 className="mb-2 text-sm font-semibold text-slate-700 dark:text-slate-200">🧠 知识节点（{data.nodes.total}）</h2>
              <div className="flex flex-col gap-2">
                {data.nodes.items.map((n) => (
                  <Link key={n.id} to={`/plans/${n.plan_id}`} data-search-result>
                    <Card interactive>
                      <p className="text-sm font-medium"><Highlight text={n.name} q={q} /></p>
                      {n.plan_topic && <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">所属方案：{n.plan_topic}</p>}
                    </Card>
                  </Link>
                ))}
              </div>
            </section>
          )}

          {data.notes.total > 0 && (
            <section aria-label="笔记搜索结果">
              <h2 className="mb-2 text-sm font-semibold text-slate-700 dark:text-slate-200">📝 笔记（{data.notes.total}）</h2>
              <div className="flex flex-col gap-2">
                {data.notes.items.map((n) => (
                  <Link key={n.id} to={`/notes?note=${n.id}`} data-search-result>
                    <Card interactive>
                      <p className="text-sm font-medium"><Highlight text={n.title || '（无标题）'} q={q} /></p>
                      {n.snippet && (
                        <p className="mt-0.5 line-clamp-2 text-xs text-slate-500 dark:text-slate-400"><Highlight text={n.snippet} q={q} /></p>
                      )}
                    </Card>
                  </Link>
                ))}
              </div>
            </section>
          )}

          {data.kb_docs.total > 0 && (
            <section aria-label="知识库文档搜索结果">
              <h2 className="mb-2 text-sm font-semibold text-slate-700 dark:text-slate-200">🗄️ 知识库文档（{data.kb_docs.total}）</h2>
              <div className="flex flex-col gap-2">
                {data.kb_docs.items.map((d) => (
                  <Link key={d.id} to={`/wiki?doc=${d.id}`} data-search-result>
                    <Card interactive>
                      <p className="text-sm font-medium">
                        <Highlight text={d.title || `文档 #${d.id}`} q={q} />
                        {d.match === 'content' && (
                          <span className="ml-1.5 rounded-full bg-emerald-50 px-1.5 py-0.5 text-xs font-normal text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                            内容命中
                          </span>
                        )}
                      </p>
                      {d.snippet && (
                        <p className="mt-0.5 line-clamp-2 text-xs text-slate-500 dark:text-slate-400"><Highlight text={d.snippet} q={q} /></p>
                      )}
                      <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                        {(d.kind && KIND_LABEL[d.kind]) ?? '文档'} · {d.created_at.slice(0, 10)}
                      </p>
                    </Card>
                  </Link>
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  )
}

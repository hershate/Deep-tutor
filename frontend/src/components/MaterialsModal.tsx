// 自学材料弹窗（P2，plan/07 §六 简化版：单文档 markdown + react-markdown 渲染）。
// 打开即取（幂等：iter 43 起已持久化直接返回，免重复 LLM 成本）；切换深度强制重新生成；
// 关闭/卸载时中止进行中请求。逐单元阅读器（StudyMaterialReader）在 iter 44。
import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import remarkGfm from 'remark-gfm'
import { ApiError, generateMaterials } from '../lib/api'

interface Props {
  planId: number
  onClose: () => void
}

export default function MaterialsModal({ planId, onClose }: Props) {
  const [depth, setDepth] = useState<'brief' | 'full'>('full')
  const [loading, setLoading] = useState(true)
  const [markdown, setMarkdown] = useState('')
  const [warning, setWarning] = useState('')
  const [error, setError] = useState('')
  const acRef = useRef<AbortController | null>(null)

  const run = useCallback(
    (d: 'brief' | 'full', refresh = false) => {
      acRef.current?.abort() // 中止上一次进行中请求
      const ac = new AbortController()
      acRef.current = ac
      setLoading(true)
      setError('')
      setWarning('')
      generateMaterials(planId, d, ac.signal, refresh)
        .then((r) => {
          if (ac.signal.aborted) return
          setMarkdown(r.markdown)
          setWarning(r.warning ?? '')
        })
        .catch((e) => {
          if (ac.signal.aborted) return
          setError(e instanceof ApiError ? e.message : '生成失败')
        })
        .finally(() => {
          if (!ac.signal.aborted) setLoading(false)
        })
    },
    [planId],
  )

  useEffect(() => {
    run('full') // 打开：已持久化则直接返回（幂等，免重复 LLM 成本）
    return () => acRef.current?.abort() // 卸载中止
  }, [run])

  // Esc 关闭
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose() // 点遮罩关闭
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="自学材料"
        className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl bg-white shadow-xl"
      >
        <div className="flex items-center justify-between gap-2 border-b px-4 py-3">
          <h3 className="font-semibold">自学材料</h3>
          <div className="flex items-center gap-2">
            <label htmlFor="materials-depth" className="text-sm text-slate-600">
              深度
            </label>
            <select
              id="materials-depth"
              value={depth}
              onChange={(e) => {
                const d = e.target.value as 'brief' | 'full'
                setDepth(d)
                run(d, true) // 切换深度 -> 强制重新生成（替换已持久化单元）
              }}
              disabled={loading}
              className="rounded border px-2 py-1 text-sm"
            >
              <option value="full">完整</option>
              <option value="brief">简要</option>
            </select>
            <button
              onClick={onClose}
              aria-label="关闭"
              className="rounded-lg border px-3 py-1 text-sm hover:bg-slate-50"
            >
              关闭
            </button>
          </div>
        </div>
        <div className="overflow-y-auto px-6 py-4">
          {loading && <p className="text-slate-500">生成中…（逐单元组织材料，约需数十秒）</p>}
          {error && (
            <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
              {error}
            </div>
          )}
          {!loading && !error && (
            <>
              {warning && (
                <div className="mb-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-700">{warning}</div>
              )}
              <div className="markdown-body text-sm">
                <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
                  {markdown}
                </ReactMarkdown>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

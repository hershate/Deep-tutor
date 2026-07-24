// 模型计费管理（iter 356）：用户在设置页维护「模型名 -> 各项费用」列表（¥/1M tokens）。
// 计费按精确模型名匹配此表 -> Anthropic 族默认兜底 -> 记 0 告警。
// 列表初始预填主流国产模型参考价（DeepSeek/Qwen/GLM/Kimi，可改可删可加）。
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, getPricing, updatePricing, type ModelPricingEntry } from '../lib/api'
import { SkeletonLines } from './Skeleton'
import { toast } from './Toast'

interface Row {
  model: string
  in: string
  out: string
  cr: string
  cw: string
}

const EMPTY_ROW: Row = { model: '', in: '', out: '', cr: '', cw: '' }

/** Record<模型, Entry> -> 可编辑行数组（顺序稳定：按 key 排序） */
function toRows(map: Record<string, ModelPricingEntry> | undefined): Row[] {
  if (!map) return []
  return Object.keys(map)
    .sort()
    .map((m) => ({
      model: m,
      in: String(map[m].input_per_mtok ?? ''),
      out: String(map[m].output_per_mtok ?? ''),
      cr: String(map[m].cache_read_per_mtok ?? ''),
      cw: String(map[m].cache_write_per_mtok ?? ''),
    }))
}

/** 字符串 -> 非负数；空串/非法 -> 0（后端清洗同样对缺失字段补 0） */
function num(v: string): number {
  const n = Number(v)
  return Number.isFinite(n) && n >= 0 ? n : 0
}

export default function ModelPricingManager() {
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({ queryKey: ['pricing'], queryFn: getPricing, staleTime: 60_000 })
  const [rows, setRows] = useState<Row[]>([])
  const [inited, setInited] = useState(false)
  const [busy, setBusy] = useState(false)
  const [opError, setOpError] = useState('')

  // 首次加载：用户已保存表非空 -> 用之；否则用内置默认（国产模型参考价）预填
  useEffect(() => {
    if (inited || !data) return
    const src = data.table && Object.keys(data.table).length > 0 ? data.table : data.defaults
    setRows(toRows(src))
    setInited(true)
  }, [data, inited])

  function setRow(i: number, patch: Partial<Row>) {
    setRows((prev) => prev.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  }

  async function save() {
    if (busy) return
    const obj: Record<string, Partial<ModelPricingEntry>> = {}
    const seen = new Set<string>()
    for (const r of rows) {
      const m = r.model.trim()
      if (!m) continue
      const key = m.toLowerCase()
      if (seen.has(key)) {
        setOpError(`模型名重复：${m}`)
        return
      }
      seen.add(key)
      obj[m] = {
        input_per_mtok: num(r.in),
        output_per_mtok: num(r.out),
        cache_read_per_mtok: num(r.cr),
        cache_write_per_mtok: num(r.cw),
      }
    }
    setBusy(true)
    setOpError('')
    try {
      await updatePricing(obj)
      toast.success(`模型计费已保存（${Object.keys(obj).length} 个模型）`)
      await qc.invalidateQueries({ queryKey: ['pricing'] })
    } catch (e) {
      setOpError(e instanceof ApiError ? e.message : '保存失败，请重试')
    } finally {
      setBusy(false)
    }
  }

  function resetToDefaults() {
    if (data?.defaults) setRows(toRows(data.defaults))
    toast.info('已重置为内置默认价（需点保存才生效）')
  }

  return (
    <section aria-label="模型计费" className="rounded-xl border bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800 p-4">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="font-semibold">模型计费</h3>
        <button
          onClick={resetToDefaults}
          disabled={!data || busy}
          className="rounded-lg border px-3 py-1 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700 disabled:opacity-50 disabled:active:scale-100"
        >
          重置为默认
        </button>
      </div>
      <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">
        维护「模型名 - 各项费用」（单位 <strong>￥/百万 tokens</strong>）。计费时按调用的模型名精确匹配此表；
        未命中回退 Anthropic 族默认（opus/sonnet/haiku）；仍无则记 0。默认预填 DeepSeek V4-Flash / V4-Pro（参考价，请按实际账单调整，可添加其他模型）。
      </p>

      {opError && (
        <div role="alert" className="mb-3 animate-fade-in rounded-lg border border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950 p-2 text-sm text-red-700 dark:text-red-300">
          {opError}
        </div>
      )}

      {isLoading && <SkeletonLines lines={4} />}
      {error && (
        <p role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          计费表加载失败
        </p>
      )}

      {inited && (
        <>
          {/* 表头（窄屏隐藏，移动端靠 placeholder 辨识） */}
          <div className="hidden grid-cols-[1.4fr_repeat(4,1fr)_auto] gap-2 px-1 pb-1 text-xs font-medium text-slate-500 dark:text-slate-400 sm:grid">
            <span>模型名</span>
            <span className="text-right">输入</span>
            <span className="text-right">输出</span>
            <span className="text-right">缓存读</span>
            <span className="text-right">缓存写</span>
            <span />
          </div>
          <ul className="space-y-2">
            {rows.map((r, i) => (
              <li key={i} className="grid grid-cols-2 gap-2 sm:grid-cols-[1.4fr_repeat(4,1fr)_auto] sm:items-center">
                <label className="text-sm sm:col-span-1 col-span-2">
                  <span className="sm:hidden text-xs text-slate-500">模型名</span>
                  <input
                    value={r.model}
                    onChange={(e) => setRow(i, { model: e.target.value })}
                    className="mt-0.5 sm:mt-0 w-full rounded-lg border px-2 py-1 font-mono text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-900"
                    placeholder="如：deepseek-chat"
                  />
                </label>
                {([
                  ['in', '输入'], ['out', '输出'], ['cr', '缓存读'], ['cw', '缓存写'],
                ] as const).map(([k, label]) => (
                  <label key={k} className="text-sm">
                    <span className="sm:hidden text-xs text-slate-500">{label}（￥）</span>
                    <input
                      value={r[k]}
                      onChange={(e) => setRow(i, { [k]: e.target.value } as Partial<Row>)}
                      className="mt-0.5 sm:mt-0 w-full rounded-lg border px-2 py-1 text-right tabular-nums transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-900"
                      placeholder="0"
                      inputMode="decimal"
                    />
                  </label>
                ))}
                <button
                  onClick={() => setRows((prev) => prev.filter((_, j) => j !== i))}
                  disabled={busy}
                  aria-label={`删除 ${r.model || '该行'}`}
                  className="rounded-lg border border-red-200 px-2 py-1 text-sm text-red-600 transition-all hover:bg-red-50 active:scale-95 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950 disabled:opacity-50 sm:h-[34px]"
                >
                  删除
                </button>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              onClick={() => setRows((prev) => [...prev, { ...EMPTY_ROW }])}
              disabled={busy}
              className="rounded-lg border px-3 py-1 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700 disabled:opacity-50"
            >
              + 添加模型
            </button>
            <button
              onClick={save}
              disabled={busy}
              className="rounded-lg bg-blue-600 px-4 py-1 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[.98] disabled:opacity-50"
            >
              {busy ? '保存中…' : '保存计费表'}
            </button>
            <span className="text-xs text-slate-400 dark:text-slate-500">
              共 {rows.filter((r) => r.model.trim()).length} 个模型；空模型名的行保存时自动忽略
            </span>
          </div>
        </>
      )}
    </section>
  )
}

// iter 293：键盘快捷键帮助面板。
// app 累积了多处键盘 UX（/ 搜索、typeahead、/review 翻卡自评、测验作答、Esc 关闭等），
// 无发现入口则这些 power feature 易被忽视。`?` 全局快捷键 + 顶栏「?」按钮打开本面板列出。
// 自包含：全局 keydown 监听 + overlay 状态封装于此；Layout 仅渲染本组件（应用壳爆炸半径小）。
import { useEffect, useState } from 'react'

interface Shortcut {
  keys: string
  desc: string
}
const GROUPS: { title: string; items: Shortcut[] }[] = [
  {
    title: '全局',
    items: [
      { keys: '/', desc: '聚焦顶栏搜索' },
      { keys: '?', desc: '打开本快捷键面板' },
    ],
  },
  {
    title: '搜索（顶栏输入时）',
    items: [
      { keys: '↑ ↓', desc: '在建议间选择' },
      { keys: 'Enter', desc: '打开选中（无选中则全量搜索）' },
      { keys: 'Esc', desc: '关闭建议' },
    ],
  },
  {
    title: '复习（/review）',
    items: [
      { keys: '空格 / Enter', desc: '翻面（知识点卡）' },
      { keys: '1 / 2 / 3', desc: '自评：忘了 / 模糊 / 记住了' },
      { keys: '→', desc: '跳过当前卡' },
      { keys: '1-9', desc: '选答案（错题卡）' },
      { keys: 'Enter', desc: '判定（错题卡）' },
    ],
  },
  {
    title: '测验',
    items: [
      { keys: '1-9', desc: '选择选项' },
      { keys: 'Enter', desc: '判定并推进下一题' },
    ],
  },
  {
    title: '弹窗 / 抽屉',
    items: [{ keys: 'Esc', desc: '关闭当前弹窗或抽屉' }],
  },
]

export default function ShortcutsHelp() {
  const [open, setOpen] = useState(false)

  // `?` 打开（Shift+/ 产出 '?'；输入态/修饰键/已有弹窗不劫持）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '?' || e.ctrlKey || e.metaKey || e.altKey) return
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
      if (document.querySelector('[role="dialog"]')) return // 已有弹窗打开，不抢键
      e.preventDefault()
      setOpen(true)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Esc 关闭（仅本面板打开时挂监听）
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="键盘快捷键帮助"
        title="键盘快捷键（?）"
        className="rounded-lg border px-2 py-1 text-sm font-semibold text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-700 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-700 dark:hover:text-slate-200"
      >
        ?
      </button>
      {open && (
        <div
          className="fixed inset-0 z-50 flex animate-fade-in items-center justify-center bg-black/40 p-4"
          onClick={(e) => {
            if (e.target === e.currentTarget) setOpen(false)
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label="键盘快捷键"
            className="flex max-h-[85vh] w-full max-w-lg animate-scale-in flex-col overflow-y-auto rounded-xl bg-white p-5 shadow-xl dark:bg-slate-800"
          >
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-lg font-bold text-slate-800 dark:text-slate-100">键盘快捷键</h2>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="关闭"
                className="rounded px-2 py-1 text-slate-400 transition-colors hover:bg-slate-100 dark:hover:bg-slate-700"
              >
                ✕
              </button>
            </div>
            <div className="space-y-4">
              {GROUPS.map((g) => (
                <div key={g.title}>
                  <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">{g.title}</h3>
                  <dl className="space-y-1">
                    {g.items.map((s) => (
                      <div key={s.desc} className="flex items-center justify-between gap-3 text-sm">
                        <dt className="text-slate-600 dark:text-slate-300">{s.desc}</dt>
                        <dd className="shrink-0">
                          <kbd className="rounded border bg-slate-50 px-1.5 py-0.5 font-sans text-xs text-slate-700 dark:border-slate-600 dark:bg-slate-700 dark:text-slate-200">
                            {s.keys}
                          </kbd>
                        </dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  )
}

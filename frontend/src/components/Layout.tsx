import type { ReactNode } from 'react'

// AppShell（plan/06 §设计系统 / AppShell）。骨架阶段最小版。
export default function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b bg-white">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between">
          <h1 className="text-lg font-semibold tracking-tight">Deep-tutor</h1>
          <span className="text-sm text-slate-500">AI 学习方案生成</span>
        </div>
      </header>
      <main className="flex-1 w-full max-w-5xl mx-auto px-4 py-6">{children}</main>
      <footer className="border-t bg-white">
        <div className="max-w-5xl mx-auto px-4 py-3 text-xs text-slate-400">
          Deep-tutor · Phase 1 MVP
        </div>
      </footer>
    </div>
  )
}

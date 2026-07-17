import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import UsageMeter from './UsageMeter'

// AppShell（plan/06 §设计系统 / AppShell）。骨架阶段最小版 + 顶栏导航 + 用量小窗。
export default function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b bg-white">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-5">
            <Link to="/" className="text-lg font-semibold tracking-tight hover:text-slate-700">
              Deep-tutor
            </Link>
            <nav className="flex items-center gap-3 text-sm" aria-label="主导航">
              <Link to="/" className="text-slate-600 hover:text-slate-900">
                首页
              </Link>
              <Link to="/usage" className="text-slate-600 hover:text-slate-900">
                用量
              </Link>
              <Link to="/settings" className="text-slate-600 hover:text-slate-900">
                设置
              </Link>
            </nav>
          </div>
          <UsageMeter />
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

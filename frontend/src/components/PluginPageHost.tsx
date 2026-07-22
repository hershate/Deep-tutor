import { useEffect, useSyncExternalStore } from 'react'
import { Link, useLocation } from 'react-router-dom'
import ErrorBoundary from './ErrorBoundary'
import { pages as _pages, PAGES_PREFIX } from '../lib/plugins'

// 插件页面宿主（插件 API v1.4.0）：挂载在 /p/* 单一路由下，按子路径匹配插件注册的页面渲染。
// 设计要点：
// - 命名空间隔离：插件页面一律在 /p/ 下，与内置路由零碰撞（不可能覆盖 /plans、/notes 等）。
// - 单页错误边界：插件页面渲染异常被隔离（不白屏整应用），resetKey=子路径，换页自愈。
// - 响应式：useSyncExternalStore 订阅注册表——插件在导航后异步注册页面时，版本号变化
//   触发重渲染即可命中（覆盖「先访问 URL、后加载插件」的顺序）。
// - 未命中（未注册/已卸载）-> 显式「页面不存在」提示 + 回首页，绝不静默。
export default function PluginPageHost() {
  useSyncExternalStore(_pages.subscribe, _pages.getVersion)
  const { pathname } = useLocation()
  const sub = pathname.startsWith(PAGES_PREFIX) ? pathname.slice(PAGES_PREFIX.length) : ''
  const def = sub ? _pages.get(sub) : undefined

  // document.title 跟随插件页面标题；卸载/换页还原默认
  useEffect(() => {
    if (def?.title) {
      document.title = `${def.title} · Deep-tutor`
      return () => {
        document.title = 'Deep-tutor'
      }
    }
  }, [def?.title])

  if (!def) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-6 text-center dark:border-slate-700 dark:bg-slate-800">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          插件页面不存在：
          <code className="mx-1 rounded bg-slate-100 px-1.5 py-0.5 text-xs dark:bg-slate-700">{sub || '(空)'}</code>
        </p>
        <p className="mt-2 text-xs text-slate-400 dark:text-slate-500">
          该页面可能尚未加载对应插件，或已被移除。
        </p>
        <Link
          to="/"
          className="mt-4 inline-block rounded-lg border px-3 py-1.5 text-sm transition-colors hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
        >
          返回首页
        </Link>
      </div>
    )
  }

  const C = def.component
  return (
    <ErrorBoundary resetKey={sub}>
      <C />
    </ErrorBoundary>
  )
}

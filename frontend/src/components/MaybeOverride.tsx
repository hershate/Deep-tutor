// 插件页面覆盖（插件 API v1.16.0）：检查当前路由是否有插件注册的 override。
// 有则渲染插件组件（经 ErrorBoundary 隔离），无则渲染宿主原页面（children）。
// 受保护路由（/plugins /settings /login /setup）不包裹 MaybeOverride——始终渲染宿主页。
import { type ReactNode, useSyncExternalStore } from 'react'
import ErrorBoundary from './ErrorBoundary'
import { pages as _pages } from '../lib/plugins'

export default function MaybeOverride({ hostPath, children }: { hostPath: string; children: ReactNode }) {
  useSyncExternalStore(_pages.subscribe, _pages.getVersion)
  const override = _pages.getOverride(hostPath)
  if (override) {
    const C = override.component
    return (
      <ErrorBoundary resetKey={`override:${hostPath}`}>
        <C />
      </ErrorBoundary>
    )
  }
  return <>{children}</>
}

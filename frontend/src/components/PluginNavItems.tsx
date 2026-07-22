import { useSyncExternalStore } from 'react'
import { NavLink } from 'react-router-dom'
import { pages as _pages, PAGES_PREFIX } from '../lib/plugins'

// 插件侧栏导航项渲染器（插件 API v1.4.0）：把带 nav 的插件页面渲染为侧栏 NavLink。
// 自身无导航项时返回 null（无空壳）；响应式订阅注册表（插件动态注册/卸载导航即时反映）。
// 样式与内置导航一致（经 props 注入 navLinkClass），桌面折叠态只显图标、抽屉态强制展开。

interface PluginNavItemsProps {
  /** 与内置导航同一 className 生成器（激活态/暗色一致） */
  navLinkClass: (arg: { isActive: boolean }) => string
  /** 折叠态只显图标（桌面侧栏）；抽屉态传 false 强制展开 */
  collapsed: boolean
  /** 点击后回调（抽屉场景用于收起抽屉） */
  onNavigate?: () => void
}

export default function PluginNavItems({ navLinkClass, collapsed, onNavigate }: PluginNavItemsProps) {
  useSyncExternalStore(_pages.subscribe, _pages.getVersion)
  const items = _pages.navItems()
  if (items.length === 0) return null
  return (
    <>
      {items.map((p) => {
        const nav = p.nav
        if (!nav) return null
        return (
          <NavLink
            key={p.path}
            to={`${PAGES_PREFIX}${p.path}`}
            onClick={onNavigate}
            className={navLinkClass}
            title={collapsed ? nav.label : undefined}
          >
            <span aria-hidden="true" className="w-5 shrink-0 text-center">
              {nav.icon ?? '🧩'}
            </span>
            {!collapsed && <span className="truncate">{nav.label}</span>}
          </NavLink>
        )
      })}
    </>
  )
}

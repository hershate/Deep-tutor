import { useSyncExternalStore, type ComponentType } from 'react'
import { widgets as _widgets } from '../lib/plugins'

// 小组件插槽渲染器：把插件 API 注册到指定 slot 的组件按 order 渲染出来。
// 自身无任何内置样式假设；layout 只决定排布容器。

interface WidgetSlotProps {
  slot: Parameters<typeof _widgets.list>[0]
  /** grid = 响应式宫格（2 列起步，宽屏 4 列）；stack = 纵向堆叠 */
  layout?: 'grid' | 'stack'
  className?: string
}

export default function WidgetSlot({ slot, layout = 'stack', className = '' }: WidgetSlotProps) {
  // 注册表版本号作 snapshot；变化时重渲染读取最新列表
  useSyncExternalStore(_widgets.subscribe, _widgets.getVersion)
  const defs = _widgets.list(slot)
  if (defs.length === 0) return null
  const container =
    layout === 'grid'
      ? `grid grid-cols-2 gap-3 lg:grid-cols-4 ${className}`
      : `flex flex-col gap-3 ${className}`
  return (
    <div className={container} data-widget-slot={slot}>
      {defs.map((d) => {
        const C = d.component as ComponentType
        return <C key={d.id} />
      })}
    </div>
  )
}

import { Component, useSyncExternalStore, type ComponentType, type ReactNode } from 'react'
import { widgets as _widgets } from '../lib/plugins'

// 小组件插槽渲染器：把插件 API 注册到指定 slot 的组件按 order 渲染出来。
// 自身无任何内置样式假设；layout 只决定排布容器。

interface ErrorBoundaryProps {
  id: string
  /** 当前组件引用：插件重新注册同 id 的新组件时重置失败态（给修复后的组件第二次机会） */
  component: ComponentType
  children: ReactNode
}

// 单组件级错误边界（v1.2.0）：插件组件渲染异常只隐藏该组件本身，
// 不影响应用其余部分与同插槽的其他小组件；异常输出到控制台便于插件作者排查。
class WidgetErrorBoundary extends Component<ErrorBoundaryProps, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true }
  }

  componentDidCatch(err: unknown): void {
    console.error(`[DeepTutor] 小组件渲染异常已隔离 (${this.props.id})`, err)
  }

  componentDidUpdate(prev: ErrorBoundaryProps): void {
    if (this.state.failed && prev.component !== this.props.component) {
      this.setState({ failed: false })
    }
  }

  render(): ReactNode {
    return this.state.failed ? null : this.props.children
  }
}

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
        return (
          <WidgetErrorBoundary key={d.id} id={d.id} component={C}>
            <C />
          </WidgetErrorBoundary>
        )
      })}
    </div>
  )
}

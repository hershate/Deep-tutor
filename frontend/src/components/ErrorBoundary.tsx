// 全局页面级错误边界（iter 273）：此前仅插件小组件有隔离边界（WidgetSlot），
// 任何页面组件渲染异常 = 整屏白屏且无恢复手段，只能手动刷新碰运气。
// 设计：包在 Layout <main> 内——顶栏/侧栏/导航保持可用（比整应用包裹降级面小）；
// resetKey=路由路径——路由切换自动重置错误态（用户可点侧栏离开出错页）；
// 异常输出控制台（便于排查），fallback 提供重载/回首页两个恢复出口。
import { Component, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

interface Props {
  resetKey: string // 变化即重置错误态（路由切换给应用自愈机会）
  children: ReactNode
}

interface State {
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(err: unknown): void {
    console.error('[DeepTutor] 页面渲染异常已拦截', err)
  }

  componentDidUpdate(prev: Props): void {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div
        role="alert"
        className="mx-auto mt-16 max-w-md rounded-xl border border-red-200 bg-red-50 p-6 text-center dark:border-red-900 dark:bg-red-950"
      >
        <p className="text-3xl" aria-hidden="true">
          😵
        </p>
        <h2 className="mt-2 text-lg font-semibold text-red-700 dark:text-red-300">页面出错了</h2>
        <p className="mt-1 break-all text-sm text-red-600/80 dark:text-red-400/80">{error.message || '未知错误'}</p>
        <div className="mt-4 flex items-center justify-center gap-3">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-lg bg-red-600 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-red-700"
          >
            重新加载
          </button>
          <Link
            to="/"
            className="rounded-lg border border-red-300 px-4 py-1.5 text-sm text-red-700 transition-colors hover:bg-red-100 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-900"
          >
            回到首页
          </Link>
        </div>
      </div>
    )
  }
}

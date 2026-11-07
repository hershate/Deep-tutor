import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles.css'
// 公式样式全局引入：学习空间等懒加载页面也渲染 KaTeX，不能只随 Library 块加载
import 'katex/dist/katex.min.css'

// 顶层错误边界（信息项 14）：任一组件渲染抛错时 React 会卸载整树导致
// 白屏且无恢复入口——这里兜底渲染错误说明与重载按钮，用户不必重启应用
class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { message: string }
> {
  state = { message: '' }
  static getDerivedStateFromError(error: unknown) {
    return { message: error instanceof Error ? error.message : String(error) }
  }
  componentDidCatch(error: unknown) {
    console.error('界面渲染失败', error)
  }
  render() {
    if (this.state.message) {
      return <div className="page-loading" role="alert" style={{ minHeight: '100vh' }}>
        <h1>界面遇到问题</h1>
        <p>{this.state.message}</p>
        <button className="btn btn-primary btn-md" onClick={() => window.location.reload()}>重新加载</button>
      </div>
    }
    return this.props.children
  }
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)

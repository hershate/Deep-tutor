import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles.css'
// 公式样式全局引入：学习空间等懒加载页面也渲染 KaTeX，不能只随 Library 块加载
import 'katex/dist/katex.min.css'
// P-128：mhchem 扩展不是 KaTeX 核心的一部分，必须单独引入——否则
// \ce{} / \pu{} 会被当成未定义命令按错误色渲染（KaTeX contrib README 明示）
import 'katex/contrib/mhchem'
// P-167：copy-tex 扩展改写复制行为——未引入时选中已渲染公式复制得到的是
// 视觉层与无障碍层拼接的重复乱码（两层各含一份公式内容）；该扩展把
// 复制结果替换为带定界符的 LaTeX 源码（contrib/copy-tex README 明示）
import 'katex/contrib/copy-tex'
import { applyCachedTheme } from './theme'
import { readableErrorMessage } from './errors'

// 顶层错误边界（信息项 14）：任一组件渲染抛错时 React 会卸载整树导致
// 白屏且无恢复入口——这里兜底渲染错误说明与重载按钮，用户不必重启应用
class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { failed: boolean; message: string }
> {
  state = { failed: false, message: '' }
  static getDerivedStateFromError(error: unknown) {
    // P-130：生产构建的 React 内部错误是压缩串，原样展示只剩英文错误码
    // 评审 NEW：失败标志独立于文案——原先用 message 真值判定是否渲染兜底页，
    // 错误描述为空串时状态仍是空值，边界重新渲染子树并再次抛错；该边界已捕获
    // 过（react-dom 18.3.1 throwException 跳过 DidCapture 已置位的边界），
    // 错误会继续上抛到根，兜底页不出现、整树被卸载成白屏
    return { failed: true, message: readableErrorMessage(error) }
  }
  componentDidCatch(error: unknown) {
    console.error('界面渲染失败', error)
  }
  render() {
    if (this.state.failed) {
      return <div className="page-loading" role="alert" style={{ minHeight: '100vh' }}>
        <h1>界面遇到问题</h1>
        <p>{this.state.message}</p>
        <button className="btn btn-primary btn-md" onClick={() => window.location.reload()}>重新加载</button>
      </div>
    }
    return this.props.children
  }
}

// 142#3：全局 unhandledrejection 兜底——136#2 仓级整改后现有 fire-and-forget
// 调用点均有 .catch 或内部 try/catch；未来漏网的 rejection 只进 console 时
// 用户无可见反馈，此处经 backend-notice 通道（134#8 状态类提示）补一条可见横幅
window.addEventListener('unhandledrejection', event => {
  console.error('未处理的 Promise 拒绝', event.reason)
  window.dispatchEvent(new CustomEvent('backend-notice', { detail: '一个后台操作未能完成，部分数据可能未刷新；可重试相关操作。' }))
})

// P-123：首帧之前套用上次已知主题——bootstrap 期间拿不到真实偏好，
// 深色主题用户原先每次启动都先看到浅色引导页再跳深色（浅→深闪变）
applyCachedTheme()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
)

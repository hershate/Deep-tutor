import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles.css'
// 公式样式全局引入：学习空间等懒加载页面也渲染 KaTeX，不能只随 Library 块加载
import 'katex/dist/katex.min.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)

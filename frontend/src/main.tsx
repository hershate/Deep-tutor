import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { initPlugins } from './lib/plugins'
import { registerBuiltinWidgets } from './widgets/homeDashboard'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    // iter 366：staleTime 5s——查询在 5s 内不重复 refetch（页面来回切换/多组件同键复用缓存），
    // 降低 100 用户下的挂载/失效洪流；变更后仍经 invalidateQueries 强制刷新，不受影响。
    queries: { refetchOnWindowFocus: false, retry: 1, staleTime: 5_000 },
  },
})

// 插件 API：挂 window.DeepTutor + 恢复皮肤 + 自动加载持久化插件；注册内置小组件
registerBuiltinWidgets()
void initPlugins(React)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </BrowserRouter>
  </React.StrictMode>,
)

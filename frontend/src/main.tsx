import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { AUTH_CHANGED_EVENT, AUTH_STORAGE_KEYS, getStoredUser } from './lib/auth'
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

// iter 368：登录身份切换/登出即清 react-query 缓存——缓存键不带用户维度，公共设备
// 同标签换用户（或 401 强制登出后换号）时，新用户会命中前任的 sessions/plans/notes
// 等缓存（跨用户数据泄漏窗口直至 refetch）。改名等原地更新（id 不变）不清缓存免闪烁。
let _lastAuthUserId = getStoredUser()?.id ?? null
function syncAuthCache(): void {
  const cur = getStoredUser()?.id ?? null
  if (cur !== _lastAuthUserId) {
    queryClient.clear()
    _lastAuthUserId = cur
  }
}
window.addEventListener(AUTH_CHANGED_EVENT, syncAuthCache)
// iter 369 B16：跨标签登出/换号——他标签 clearAuth/setAuth 触发本标签 storage 事件
// （AUTH_CHANGED_EVENT 仅本标签派发）。token/user 两键各发一次事件，微任务去抖后统一
// 比对用户 id；变化即清缓存，防本标签继续命中前任用户的 sessions/plans/notes 缓存
// （任务台账的内存清理由 store/tasks.ts 的 storage 监听同步处理）。
window.addEventListener('storage', (e) => {
  if (e.key == null || (AUTH_STORAGE_KEYS as readonly string[]).includes(e.key)) {
    setTimeout(syncAuthCache, 0)
  }
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

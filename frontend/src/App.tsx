import type { ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Layout from './components/Layout'
import { TaskEngineBridge } from './components/TaskEngineBridge'
import { getToken } from './lib/auth'
import { useSetupStatus } from './lib/setup'
import Home from './pages/Home'
import GenerateProgress from './pages/GenerateProgress'
import Clarify from './pages/Clarify'
import Learn from './pages/Learn'
import Login from './pages/Login'
import NotFound from './pages/NotFound'
import PlanDetail from './pages/PlanDetail'
import Plans from './pages/Plans'
import Plugins from './pages/Plugins'
import Setup from './pages/Setup'
import SharedPlan from './pages/SharedPlan'
import Usage from './pages/Usage'
import Settings from './pages/Settings'
import Wiki from './pages/Wiki'
import Notes from './pages/Notes'

// 路由守卫（P4 多用户，iter 59；iter 89 零配置向导）：
// - 状态加载中 -> 占位；查询失败 -> 回退旧逻辑（仅查 token，不让 status 端点故障阻塞应用）
// - needs_setup -> /setup 首次设置向导
// - open_mode（零配置免登录）-> 免 token 放行
// - 否则无 token -> /login，记住来源
function RequireAuth({ children }: { children: ReactNode }) {
  const location = useLocation()
  const { data: status, isLoading, isError } = useSetupStatus()
  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center gap-2 text-sm text-slate-400" role="status">
        <span aria-hidden="true" className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600 dark:border-slate-600 dark:border-t-blue-400" />
        加载中…
      </div>
    )
  }
  if (!isError && status) {
    if (status.needs_setup) {
      return <Navigate to="/setup" replace />
    }
    if (status.open_mode) {
      return <>{children}</>
    }
  }
  if (!getToken()) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      {/* iter 89：首次设置向导（零配置开箱即用），公开页 */}
      <Route path="/setup" element={<Setup />} />
      {/* P5 协作分享：公开只读页，无需登录（链接本身即凭证，192bit 随机 token） */}
      <Route path="/shared/:token" element={<SharedPlan />} />
      <Route
        path="*"
        element={
          <RequireAuth>
            <Layout>
              {/* iter 187 F2：任务引擎桥（注入 navigate/queryClient + boot 对账 + 路由同步） */}
              <TaskEngineBridge />
              <Routes>
                <Route path="/" element={<Home />} />
                <Route path="/generate/:sessionId" element={<GenerateProgress />} />
                <Route path="/clarify/:sessionId" element={<Clarify />} />
                <Route path="/plans" element={<Plans />} />
                <Route path="/plans/:planId" element={<PlanDetail />} />
                <Route path="/learn/:planId" element={<Learn />} />
                <Route path="/wiki" element={<Wiki />} />
                <Route path="/notes" element={<Notes />} />
                <Route path="/usage" element={<Usage />} />
                {/* iter 204：插件管理页（前端插件加载/移除 + 生效概览 + 后端状态） */}
                <Route path="/plugins" element={<Plugins />} />
                <Route path="/settings" element={<Settings />} />
                {/* iter 157：未知路由显式 404（原静默重定向首页，输错 URL 无感知） */}
                <Route path="*" element={<NotFound />} />
              </Routes>
            </Layout>
          </RequireAuth>
        }
      />
    </Routes>
  )
}

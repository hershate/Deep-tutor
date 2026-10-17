import { Children, cloneElement, type ReactElement, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import Layout from './components/Layout'
import { TaskEngineBridge } from './components/TaskEngineBridge'
import { getToken } from './lib/auth'
import { useSetupStatus } from './lib/setup'
import Home from './pages/Home'
import Chat from './pages/Chat'
import GenerateProgress from './pages/GenerateProgress'
import Clarify from './pages/Clarify'
import Learn from './pages/Learn'
import LearnIndex from './pages/LearnIndex'
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
import Review from './pages/Review'
import Search from './pages/Search'
import PluginPageHost from './components/PluginPageHost'
import PluginOverlayHost from './components/PluginOverlayHost'
import EventsBridge from './components/EventsBridge'
import MaybeOverride from './components/MaybeOverride'
import RouterBridge from './components/RouterBridge'

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
    // iter 368：带上 search（如 /plans/1?materials=1）——此前登录后回跳丢查询参数
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }
  return <>{children}</>
}

/** iter 367（批次六 M11）：按当前路由 :sessionId 参数给子树加 key——同路由换参数
 *  默认不重挂载，子页挂载期初始化的会话级状态（Clarify 答案草稿等）会残留旧会话。 */
function RemountByKeyParam({ children }: { children: ReactElement }) {
  const { sessionId } = useParams()
  return sessionId ? cloneElement(Children.only(children), { key: sessionId }) : children
}

export default function App() {
  return (
    <>
      {/* iter 308：插件路由桥（插件 API v1.5.0 DeepTutor.router 的 navigate 注入 + 路由同步） */}
      <RouterBridge />
      {/* iter 310：插件覆盖层宿主（插件 API v1.6.0 DeepTutor.overlay）——全路由根级渲染层，
          挂 Routes 外、BrowserRouter/QueryClientProvider 内：overlay 组件可用路由与取数 hooks */}
      <PluginOverlayHost />
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
              {/* iter 315：插件事件桥（DeepTutor.events ← SSE /api/events/stream），仅登录态连接 */}
              <EventsBridge />
              <Routes>
                <Route path="/" element={<MaybeOverride hostPath="home"><Home /></MaybeOverride>} />
                <Route path="/chat" element={<Chat />} />
                <Route path="/generate/:sessionId" element={<GenerateProgress />} />
                {/* iter 367（批次六 M11）：key=sessionId 强制换会话重挂载——Clarify 的
                    answers 草稿仅在挂载时按 sessionId 初始化，同路由换参数不重挂会
                    残留上一会话的问题与答案 */}
                <Route path="/clarify/:sessionId" element={<RemountByKeyParam><Clarify /></RemountByKeyParam>} />
                <Route path="/plans" element={<MaybeOverride hostPath="plans"><Plans /></MaybeOverride>} />
                <Route path="/plans/:planId" element={<MaybeOverride hostPath="plans"><PlanDetail /></MaybeOverride>} />
                {/* iter 346（F9）：学习中心独立聚合页 /learn（今日学习 + 各方案卡片，drill-down /learn/:planId） */}
                <Route path="/learn" element={<MaybeOverride hostPath="learn"><LearnIndex /></MaybeOverride>} />
                <Route path="/learn/:planId" element={<MaybeOverride hostPath="learn"><Learn /></MaybeOverride>} />
                {/* iter 281：跨方案统一复习工作流页（翻卡自评 + 掌握度回写） */}
                <Route path="/review" element={<MaybeOverride hostPath="review"><Review /></MaybeOverride>} />
                <Route path="/wiki" element={<MaybeOverride hostPath="wiki"><Wiki /></MaybeOverride>} />
                <Route path="/notes" element={<MaybeOverride hostPath="notes"><Notes /></MaybeOverride>} />
                {/* iter 249：全局搜索页（方案/笔记/知识库文档/知识节点四域聚合） */}
                <Route path="/search" element={<Search />} />
                <Route path="/usage" element={<MaybeOverride hostPath="usage"><Usage /></MaybeOverride>} />
                {/* iter 204：插件管理页（前端插件加载/移除 + 生效概览 + 后端状态） */}
                <Route path="/plugins" element={<Plugins />} />
                <Route path="/settings" element={<Settings />} />
                {/* iter 306：插件页面命名空间 /p/*（插件 API v1.4.0 pages 注册表宿主路由） */}
                <Route path="/p/*" element={<PluginPageHost />} />
                {/* iter 157：未知路由显式 404（原静默重定向首页，输错 URL 无感知） */}
                <Route path="*" element={<NotFound />} />
              </Routes>
            </Layout>
          </RequireAuth>
        }
      />
    </Routes>
    </>
  )
}

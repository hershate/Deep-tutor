import type { ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Layout from './components/Layout'
import { getToken } from './lib/auth'
import Home from './pages/Home'
import GenerateProgress from './pages/GenerateProgress'
import Clarify from './pages/Clarify'
import Learn from './pages/Learn'
import Login from './pages/Login'
import PlanDetail from './pages/PlanDetail'
import Usage from './pages/Usage'
import Settings from './pages/Settings'

// 路由守卫（P4 多用户，iter 59）：无任何 token（JWT 与 env AUTH_TOKEN 皆空）-> 跳登录页，记住来源。
function RequireAuth({ children }: { children: ReactNode }) {
  const location = useLocation()
  if (!getToken()) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="*"
        element={
          <RequireAuth>
            <Layout>
              <Routes>
                <Route path="/" element={<Home />} />
                <Route path="/generate/:sessionId" element={<GenerateProgress />} />
                <Route path="/clarify/:sessionId" element={<Clarify />} />
                <Route path="/plans/:planId" element={<PlanDetail />} />
                <Route path="/learn/:planId" element={<Learn />} />
                <Route path="/usage" element={<Usage />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Layout>
          </RequireAuth>
        }
      />
    </Routes>
  )
}

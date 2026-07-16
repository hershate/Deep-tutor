import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Home from './pages/Home'

// 路由骨架（plan/06）。生成流程各页面在 step 7 落地：
// /, /clarify/:sessionId, /generate/:sessionId, /plans/:id, /usage, /settings
export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}

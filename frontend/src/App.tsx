import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Home from './pages/Home'
import GenerateProgress from './pages/GenerateProgress'
import Clarify from './pages/Clarify'
import PlanDetail from './pages/PlanDetail'
import Usage from './pages/Usage'
import Settings from './pages/Settings'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/generate/:sessionId" element={<GenerateProgress />} />
        <Route path="/clarify/:sessionId" element={<Clarify />} />
        <Route path="/plans/:planId" element={<PlanDetail />} />
        <Route path="/usage" element={<Usage />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}

import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Home from './pages/Home'
import GenerateProgress from './pages/GenerateProgress'
import Clarify from './pages/Clarify'
import PlanDetail from './pages/PlanDetail'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/generate/:sessionId" element={<GenerateProgress />} />
        <Route path="/clarify/:sessionId" element={<Clarify />} />
        <Route path="/plans/:planId" element={<PlanDetail />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}

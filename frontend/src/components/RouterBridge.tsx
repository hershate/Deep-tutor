// 插件路由桥（插件 API v1.5.0）：window.DeepTutor.router 是 Router 上下文之外的模块
// 单例（纯 JS 插件拿不到 react-router 实例），本空组件挂载时注入 navigate 并在路由
// 变化时同步路径——TaskEngineBridge 同范式。
// 挂载在 App 根部（BrowserRouter 内、Routes 外）：覆盖含 /login 在内的全部路由，
// 插件在任何页面上下文调用 router.navigate 都走 SPA 导航（不整页刷新）。
import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { bindRouter, setRouterLocation } from '../lib/plugins'

export default function RouterBridge() {
  const navigate = useNavigate()
  const location = useLocation()
  useEffect(() => {
    bindRouter(
      (to, opts) => navigate(to, opts),
      location.pathname,
      location.search,
      location.hash,
    )
  }, [navigate, location.pathname, location.search, location.hash])
  useEffect(() => {
    setRouterLocation(location.pathname, location.search, location.hash)
  }, [location.pathname, location.search, location.hash])
  return null
}

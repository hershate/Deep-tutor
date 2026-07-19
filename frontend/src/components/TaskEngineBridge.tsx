// 任务引擎桥（iter 187 F2）：引擎是 Router/QueryClient 之外的模块单例，
// 本空组件挂载时注入 navigate/queryClient 并 boot 对账；路由变化同步给引擎
// （终态 toast 打扰抑制：用户正在该任务页则不 toast）。
import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { engine } from '../lib/taskEngine'

export function TaskEngineBridge() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  useEffect(() => {
    engine.bind({ navigate, queryClient })
    void engine.boot() // boot 内部幂等（StrictMode 双挂载安全）
  }, [navigate, queryClient])
  useEffect(() => {
    engine.setCurrentPath(location.pathname)
  }, [location.pathname])
  return null
}

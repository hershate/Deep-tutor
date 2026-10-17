// 插件事件桥（插件 API v1.7.0，iter 315）：连接后端实时事件流（GET /api/events/stream SSE），
// 把后端事件总线事件转发为 window.DeepTutor.events —— 插件 events.on('plan_created', ...) 即
// 实时收到（学习进度更新 / 方案生成完成 / 测验提交等），免轮询。"用 API 完全替换前端"的实时性。
//
// 挂载在鉴权区内（App.tsx 的 RequireAuth > Layout 内，TaskEngineBridge 旁）：仅登录态连接，
// 登出/卸载即 abort。重连：流结束或瞬态错误（非 401）按指数退避重连（≤30s），401 由 openSSE
// 触发登录跳转（不重连）。心跳由后端 15s 发、openSSE 60s 看门狗兜底，正常无重连。
import { useEffect } from 'react'
import { openEventStream } from '../lib/api'
import { events } from '../lib/plugins'
import { SseError } from '../lib/sse'

const MAX_BACKOFF_MS = 30_000
const BASE_BACKOFF_MS = 1_000

export default function EventsBridge() {
  useEffect(() => {
    const controller = new AbortController()
    let stopped = false
    let attempt = 0
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined

    const loop = async () => {
      while (!stopped) {
        try {
          await openEventStream((ev) => {
            // 后端帧：event: <name>\ndata: <json>；心跳（: ping）已被 openSSE 跳过
            if (!ev.event || ev.event === 'message' || !ev.data) return
            let payload: unknown
            try {
              payload = JSON.parse(ev.data)
            } catch {
              payload = ev.data // 非 JSON 载荷原样传递
            }
            events.emit(ev.event, payload)
          }, controller.signal)
          // 流干净结束（后端关闭/HTTP2 EOF）——非停止则重连。
          // iter 370：不再重置 attempt——「接受连接即关」循环（后端重启风暴/代理拒绝）
          // 每次都被当干净结束，退避永停在 250ms-1s 区间无限快打 /api/events/stream；
          // 让 attempt 继续递增（正常长连接的 attempt 本就随每次重连增长，上限 30s）
        } catch (err) {
          if (stopped || controller.signal.aborted) break
          // 401：openSSE 已触发登录跳转，不再重连
          if (err instanceof SseError && err.status === 401) break
          // 瞬态/网络错误：指数退避重连
        }
        if (stopped) break
        // iter 366：full jitter（AWS "Exponential Backoff And Jitter"）——100 客户端在后端
        // 抖动同时断开后，无抖动会按同一 1/2/4s 调度齐刷刷重连（惊群）。随机化到 [250ms, cap]
        // 错峰，避免重连风暴击垮刚恢复的后端。
        const cap = Math.min(BASE_BACKOFF_MS * 2 ** attempt, MAX_BACKOFF_MS)
        const backoff = 250 + Math.random() * (cap - 250)
        attempt += 1
        // iter 368：sleep 可被 abort 唤醒——此前卸载只 clearTimeout，promise 永不 resolve，
        // loop 协程永久挂起（每次挂载/卸载泄漏一个；登出登录循环下累积）
        await new Promise<void>((resolve) => {
          const onAbort = () => {
            clearTimeout(timer)
            resolve()
          }
          const timer = setTimeout(() => {
            controller.signal.removeEventListener('abort', onAbort)
            resolve()
          }, backoff)
          reconnectTimer = timer
          if (controller.signal.aborted) {
            clearTimeout(timer)
            resolve()
            return
          }
          controller.signal.addEventListener('abort', onAbort)
        })
        reconnectTimer = undefined
      }
    }

    void loop()

    return () => {
      stopped = true
      controller.abort()
      if (reconnectTimer) clearTimeout(reconnectTimer)
    }
  }, [])

  return null
}

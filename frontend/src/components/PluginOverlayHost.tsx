// 插件覆盖层宿主（插件 API v1.6.0）：把 overlay 注册表里的组件经 React 门户渲染到
// document.body 上的分层容器——脱离页面、覆盖整个视口，用于 WebGL 背景特效 / 可拖放浮窗 /
// 2D 物理叠层 / 3D 小游戏等"在整个应用之上绘制"的场景（详见 API.md「覆盖层 API」）。
//
// 设计要点：
// - 三层 z-index 分层（互不遮挡应用正常使用）：
//     background  z-index:-1    应用内容之后（透过透明区域可见）——WebGL 背景特效
//     content     z-index:45     应用内容之上、模态/Toast 之下 ——浮窗、HUD、画布叠层
//     top         z-index:9999   万物之上（盖过模态/Toast）——全屏 3D 游戏、接管式特效
// - pointer-events：层容器与 overlay 包裹层默认 'none'（点击穿透到下层应用，不阻塞 UI）；
//   注册时 pointerEvents:'auto' 的 overlay 才捕获指针事件（浮窗/游戏交互用）。
// - 单 overlay 错误边界：一个 overlay 渲染异常只隐藏自身（不白屏整应用、不影响他层）。
// - 生命周期：分层容器为页面级单例（首次取用时惰性创建，pointer-events:none 空容器零影响），
//   overlay 注册表变化经 useSyncExternalStore 响应（覆盖"先访问页面、后加载插件"序）。
// - 全路由覆盖：挂载在 App 根部（与 RouterBridge 同级，BrowserRouter + QueryClientProvider 内），
//   overlay 组件可用 useLocation/useNavigate（按路由自显隐）与 TanStack Query 取数据。
import { Component, useSyncExternalStore, type ComponentType, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { OVERLAY_LAYERS, overlays as _overlays, type OverlayLayer } from '../lib/plugins'

/** 各分层 → document.body 上的门户容器 id 与 z-index（z-index 相对 body 直接子元素） */
const LAYER_TARGETS: Record<OverlayLayer, { id: string; zIndex: number }> = {
  // background: z-index:-1 在 body 堆叠上下文里画在"正常流内容(#root)之后、body 背景之前"，
  // 故透过应用透明区域（页边/留白/或插件自行设透明的区域）可见——body 背景始终在其下兜底。
  background: { id: 'dt-overlay-back', zIndex: -1 },
  // content: 45 高于应用吸顶 header(~40)、低于模态/Toast(~50/60)，浮窗不盖住关键反馈。
  content: { id: 'dt-overlay-content', zIndex: 45 },
  // top: 9999 盖过一切（含模态/Toast），用于全屏接管。
  top: { id: 'dt-overlay-top', zIndex: 9999 },
}

const targetCache: Partial<Record<OverlayLayer, HTMLElement>> = {}
/** 取分层门户容器（页面级惰性单例：首次创建并 appendChild 到 body，之后复用） */
function getTarget(layer: OverlayLayer): HTMLElement {
  const cached = targetCache[layer]
  if (cached && document.body.contains(cached)) return cached
  const { id, zIndex } = LAYER_TARGETS[layer]
  let el = document.getElementById(id)
  if (!el) {
    el = document.createElement('div')
    el.id = id
    el.style.position = 'fixed'
    el.style.inset = '0'
    el.style.pointerEvents = 'none' // 层容器自身永不拦截（交互由各 overlay 包裹层按需开启）
    el.style.zIndex = String(zIndex)
    el.setAttribute('data-dt-overlay-layer', layer)
    document.body.appendChild(el)
  }
  targetCache[layer] = el
  return el
}

interface OverlayBoundaryProps {
  id: string
  /** 当前组件引用：插件重新注册同 id 的新组件时重置失败态（给修复后的组件第二次机会） */
  component: ComponentType
  children: ReactNode
}

// 单 overlay 错误边界（与 WidgetSlot 的 WidgetErrorBoundary 同范式）：渲染异常只隐藏该 overlay。
class OverlayErrorBoundary extends Component<OverlayBoundaryProps, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true }
  }

  componentDidCatch(err: unknown): void {
    console.error(`[DeepTutor] 覆盖层渲染异常已隔离 (${this.props.id})`, err)
  }

  componentDidUpdate(prev: OverlayBoundaryProps): void {
    if (this.state.failed && prev.component !== this.props.component) {
      this.setState({ failed: false })
    }
  }

  render(): ReactNode {
    return this.state.failed ? null : this.props.children
  }
}

export default function PluginOverlayHost() {
  // 注册表版本号作 snapshot；变化时重渲染读取各分层最新列表
  useSyncExternalStore(_overlays.subscribe, _overlays.getVersion)
  return (
    <>
      {OVERLAY_LAYERS.map((layer) => {
        const defs = _overlays.list(layer)
        if (defs.length === 0) return null
        const target = getTarget(layer)
        return createPortal(
          defs.map((d) => {
            const C = d.component as ComponentType
            const pointerEvents = d.pointerEvents === 'auto' ? 'auto' : 'none'
            return (
              <OverlayErrorBoundary key={d.id} id={d.id} component={C}>
                {/* 包裹层满视口定位 + 按 pointerEvents 决定是否拦截指针；插件组件在此之上自由绘制 */}
                <div
                  data-dt-overlay={d.id}
                  style={{ position: 'absolute', inset: '0', pointerEvents }}
                >
                  <C />
                </div>
              </OverlayErrorBoundary>
            )
          }),
          target,
        )
      })}
    </>
  )
}

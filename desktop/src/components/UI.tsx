import { useEffect, useId, useRef } from 'react'
import type { ButtonHTMLAttributes, ReactNode, RefObject } from 'react'
import { X, Inbox } from 'lucide-react'

export function Button({ children, variant = 'primary', size = 'md', className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'light'; size?: 'sm' | 'md' | 'lg' }) {
  return <button className={`btn btn-${variant} btn-${size} ${className}`} {...props}>{children}</button>
}

export function IconButton({ label, children, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; children: ReactNode }) {
  return <button className={`icon-btn ${className}`} aria-label={label} title={label} {...props}>{children}</button>
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'green' | 'amber' | 'red' | 'blue' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

export function ProgressBar({ value, label, tone = 'green' }: { value: number; label?: string; tone?: 'green' | 'amber' | 'blue' }) {
  // 读屏需要显式 role 与数值区间才会把 div 映射为进度部件（09#7）
  const clamped = Math.max(0, Math.min(100, Math.round(value)))
  return <div className="progress-wrap" role="progressbar" aria-label={label || `完成 ${clamped}%`} aria-valuenow={clamped} aria-valuemin={0} aria-valuemax={100}>
    <div className="progress-track"><div className={`progress-fill progress-${tone}`} style={{ width: `${clamped}%` }} /></div>
    {label && <span className="progress-label">{label}</span>}
  </div>
}

export function ProgressRing({ value, size = 112, children }: { value: number; size?: number; children?: ReactNode }) {
  const radius = 43
  const circumference = 2 * Math.PI * radius
  return <div className="progress-ring" style={{ width: size, height: size }}>
    <svg viewBox="0 0 100 100" aria-hidden="true">
      <circle className="ring-track" cx="50" cy="50" r={radius} />
      <circle className="ring-value" cx="50" cy="50" r={radius} strokeDasharray={circumference} strokeDashoffset={circumference * (1 - value / 100)} />
    </svg>
    <div className="ring-content">{children || <strong>{value}%</strong>}</div>
  </div>
}

export function SectionHeader({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return <div className="section-header"><div><h2>{title}</h2>{description && <p>{description}</p>}</div>{action}</div>
}

export function Toggle({ checked, onChange, label, description }: { checked: boolean; onChange: (value: boolean) => void; label: string; description?: string }) {
  const id = useId()
  return <div className="toggle-row"><div><label htmlFor={id}>{label}</label>{description && <p>{description}</p>}</div><button id={id} role="switch" aria-checked={checked} className={`switch ${checked ? 'is-on' : ''}`} onClick={() => onChange(!checked)}><span /></button></div>
}

// 焦点圈禁（09#5）：aria-modal 的弹层必须把 Tab/Shift+Tab 限制在容器内，
// 焦点逃逸到遮罩背后时拉回；卸载后把焦点还给打开前的元素。
const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function useFocusContainment(ref: RefObject<HTMLElement | null>, active: boolean, restore?: RefObject<HTMLElement | null>) {
  useEffect(() => {
    if (!active) return
    const container = ref.current
    // 15#8：autoFocus 弹窗在 effect 运行前已把焦点落进容器，此时捕获到的
    // activeElement 属于弹窗内部（卸载后还原无效）；调用方可经 restore
    // 传入渲染期（DOM 更新前）捕获的打开前焦点
    const initial = document.activeElement as HTMLElement | null
    const previous = (restore?.current ?? null) || (container?.contains(initial) ? null : initial)
    const focusables = () => Array.from(container?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).filter(item => item.offsetParent !== null)
    const focusFirst = () => {
      // 15#8：焦点已在容器内（如 React autoFocus 已落位输入框）则不抢夺，
      // 否则 20ms 后会把焦点搬到 DOM 序首个可聚焦元素（关闭按钮）
      if (container?.contains(document.activeElement)) return
      const items = focusables(); (items.length ? items[0] : container)?.focus()
    }
    const timer = window.setTimeout(focusFirst, 20)
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Tab' || !container) return
      const items = focusables()
      if (!items.length) { event.preventDefault(); container.focus(); return }
      const first = items[0]
      const last = items[items.length - 1]
      const current = document.activeElement
      if (!container.contains(current)) { event.preventDefault(); focusFirst(); return }
      if (event.shiftKey && (current === first || current === container)) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && current === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKey)
    return () => { window.clearTimeout(timer); document.removeEventListener('keydown', onKey); previous?.focus() }
  }, [active, ref, restore])
}

// 24#2：弹层栈与滚动锁计数——Esc 只关最顶层叠加弹层（原实现每个 Modal 各自
// 监听、一次 Esc 连锁关闭全部，焦点恢复链错乱）；body 滚动锁按打开数计数，
// 嵌套关闭时外层还开着，背景不得提前恢复滚动
const MODAL_STACK: { id: number; close: () => void }[] = []
let modalSequence = 0
let overflowLocks = 0

export function Modal({ open, onClose, title, children, wide = false }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  // onClose 多为每次渲染新建的内联函数：焦点 effect 只依赖 open，
  // 通过 ref 取最新回调，避免弹窗内每次输入都触发焦点被抢回容器
  const closeRef = useRef(onClose)
  useEffect(() => { closeRef.current = onClose })
  // 15#8：在渲染阶段（DOM 更新与 autoFocus 之前）捕获打开前的焦点，
  // 供关闭时还原——effect 运行时 autoFocus 已把 activeElement 移入弹窗
  const previousFocus = useRef<HTMLElement | null>(null)
  const wasOpen = useRef(false)
  if (open && !wasOpen.current) previousFocus.current = document.activeElement as HTMLElement | null
  wasOpen.current = open
  useFocusContainment(ref, open, previousFocus)
  useEffect(() => {
    if (!open) return
    const id = ++modalSequence
    MODAL_STACK.push({ id, close: () => closeRef.current() })
    overflowLocks += 1
    document.body.style.overflow = 'hidden'
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      // 24#2：非最顶层弹层不响应——一次 Esc 只关闭一个叠加层
      const top = MODAL_STACK[MODAL_STACK.length - 1]
      if (!top || top.id !== id) return
      closeRef.current()
    }
    document.addEventListener('keydown', onKey)
    return () => {
      const index = MODAL_STACK.findIndex(entry => entry.id === id)
      if (index >= 0) MODAL_STACK.splice(index, 1)
      overflowLocks = Math.max(0, overflowLocks - 1)
      if (overflowLocks === 0) document.body.style.overflow = ''
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  if (!open) return null
  return <div className="overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
    <div className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
      <div className="modal-head"><h2>{title}</h2><IconButton label="关闭" onClick={onClose}><X size={20} /></IconButton></div>
      <div className="modal-body">{children}</div>
    </div>
  </div>
}

// 20#5/28#11：破坏性操作确认对话框——对齐 Settings 清除数据的防误触标准
// （PRODUCT.md 原则 4：关键操作可见、可撤销或可恢复）
export function ConfirmDialog({ open, onClose, onConfirm, title, description, confirmLabel = '确认', busy = false }: {
  open: boolean
  onClose: () => void
  onConfirm: () => void
  title: string
  description: ReactNode
  confirmLabel?: string
  busy?: boolean
}) {
  return <Modal open={open} onClose={onClose} title={title}>
    <div className="confirm-dialog"><p>{description}</p><div><Button variant="secondary" onClick={onClose}>取消</Button><Button disabled={busy} onClick={onConfirm}>{confirmLabel}</Button></div></div>
  </Modal>
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <div className="empty-state"><Inbox size={28} /><h3>{title}</h3><p>{description}</p>{action}</div>
}

export function MiniBars({ values, labels }: { values: number[]; labels?: string[] }) {
  const max = Math.max(...values, 1)
  return <div className="mini-bars">{values.map((value, index) => <div className="mini-bar-item" key={index}><div className="mini-bar-track"><div className="mini-bar-value" style={{ height: `${Math.max(8, value / max * 100)}%` }} /></div><span>{labels?.[index] || value}</span></div>)}</div>
}

export function LineChart({ values, labels, color = 'currentColor' }: { values: number[]; labels: string[]; color?: string }) {
  // ≤1 个点时除以 0 会产生 NaN/Infinity 坐标，0 点还会越界（09#11）；
  // 单点场景由调用方用 MiniBars 分流，这里直接不渲染。
  if (values.length < 2) return null
  const width = 640
  const height = 220
  const pad = 28
  const max = Math.max(...values) * 1.15
  const min = Math.min(...values) * 0.8
  const points = values.map((v, i) => ({ x: pad + i * ((width - pad * 2) / (values.length - 1)), y: height - pad - ((v - min) / (max - min || 1)) * (height - pad * 2) }))
  const path = points.map((p, i) => `${i ? 'L' : 'M'} ${p.x} ${p.y}`).join(' ')
  const area = `${path} L ${points.at(-1)!.x} ${height - pad} L ${points[0].x} ${height - pad} Z`
  // 默认 currentColor：颜色交给 styles.css 的 .line-chart 规则，深色主题可覆盖（09#8）
  return <div className="line-chart"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="学习趋势折线图">
    {[0, 1, 2, 3].map(i => <line key={i} x1={pad} y1={pad + i * 48} x2={width - pad} y2={pad + i * 48} className="chart-grid" />)}
    <path d={area} fill={color} opacity="0.09" />
    <path d={path} fill="none" stroke={color} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    {points.map((p, i) => <g key={i}><circle cx={p.x} cy={p.y} r="4" fill={color} /><text x={p.x} y={height - 4} textAnchor="middle" className="chart-label">{labels[i]}</text></g>)}
  </svg></div>
}

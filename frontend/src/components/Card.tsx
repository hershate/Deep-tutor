import type { HTMLAttributes, ReactNode } from 'react'

// 共享卡片范式（iter 183）：统一全站「圆角+边框+白底+浅阴影」卡片外观。
// 圆角走 CSS 变量 --dt-card-radius（默认 0.75rem），皮肤插件可全局调整（见 API.md「皮肤令牌」）。

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode
  /** hover 上浮+阴影加深（用于可点击/链接卡） */
  interactive?: boolean
}

export default function Card({ children, interactive = false, className = '', ...rest }: CardProps) {
  return (
    <div
      className={`rounded-[var(--dt-card-radius,0.75rem)] border bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800 ${
        interactive
          ? 'transition-all duration-200 hover:-translate-y-0.5 hover:border-blue-200 hover:shadow-md dark:hover:border-blue-900'
          : ''
      } ${className}`}
      {...rest}
    >
      {children}
    </div>
  )
}

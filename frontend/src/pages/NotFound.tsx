import { Link } from 'react-router-dom'
import EmptyState from '../components/EmptyState'

// 404（iter 157）：未知路由此前静默重定向首页，用户输错/用过期链接毫无感知
export default function NotFound() {
  return (
    <div className="mx-auto max-w-md pt-16">
      <EmptyState
        icon="🧭"
        title="页面不存在"
        description="链接可能有误或页面已被移除"
        action={
          <Link
            to="/"
            className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98]"
          >
            返回首页
          </Link>
        }
      />
    </div>
  )
}

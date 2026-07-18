import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { login } from '../lib/api'
import { getToken } from '../lib/auth'
import { useSetupStatus } from '../lib/setup'

// 登录页（P4 多用户，iter 59）：用户名+密码 -> JWT 存 localStorage -> 回跳来源页。
// 已有有效 token（JWT 或 env AUTH_TOKEN）-> 直接回首页。
// iter 89：待首次设置 -> /setup；零配置开放模式（免登录）-> 回首页。
export default function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const { data: status } = useSetupStatus()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  if (status?.needs_setup) {
    return <Navigate to="/setup" replace />
  }
  if (status?.open_mode || getToken()) {
    return <Navigate to="/" replace />
  }

  const from = (location.state as { from?: string } | null)?.from ?? '/'

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (loading) return
    setError('')
    setLoading(true)
    try {
      await login(username.trim(), password)
      navigate(from, { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败，请重试')
      setLoading(false)
    }
  }

  return (
    // iter 95 美化： subtle 渐变底 + 卡片弹入 + 输入/按钮品牌化
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-50 via-slate-50 to-blue-100 dark:from-slate-900 dark:via-slate-900 dark:to-slate-800 px-4">
      <div className="w-full max-w-sm animate-scale-in rounded-2xl border bg-white p-8 shadow-lg dark:border-slate-700 dark:bg-slate-800">
        {/* iter 108：品牌标识（渐变圆角方块 + 🎓） */}
        <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-blue-600 to-indigo-500 text-2xl shadow-md">
          <span aria-hidden="true">🎓</span>
        </div>
        <h1 className="text-lg font-semibold tracking-tight mb-1">Deep-tutor</h1>
        <p className="text-sm text-slate-500 dark:text-slate-400 mb-5">请登录以继续</p>
        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label htmlFor="login-username" className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">
              用户名
            </label>
            <input
              id="login-username"
              type="text"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-900"
            />
          </div>
          <div>
            <label htmlFor="login-password" className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">
              密码
            </label>
            <input
              id="login-password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-900"
            />
          </div>
          {error && (
            <p role="alert" className="animate-shake rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-blue-600 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98] disabled:opacity-50 disabled:hover:shadow-sm disabled:active:scale-100"
          >
            {loading ? '登录中…' : '登录'}
          </button>
        </form>
      </div>
    </div>
  )
}

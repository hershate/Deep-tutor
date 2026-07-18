import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, dismissSetup, postSetup } from '../lib/api'
import { setAuth } from '../lib/auth'
import { SETUP_QUERY_KEY, useSetupStatus } from '../lib/setup'

// 首次设置向导（/setup，iter 89 零配置开箱即用）：
// 无 .env 部署首访进入；两个步骤均可选——可跳过，稍后在 设置 页补配。
// 完成：POST /setup（仅提交非空字段）；设了密码则用返回 JWT 立即登录。
export default function Setup() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data: status, isLoading, isError } = useSetupStatus()

  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  // 不需要向导（已完成/已跳过）-> 直接回首页；状态查询失败不阻塞（放行回首页按旧逻辑走）
  if (!isLoading && (isError || (status && !status.needs_setup))) {
    return <Navigate to="/" replace />
  }

  async function finish(e: FormEvent) {
    e.preventDefault()
    if (busy) return
    setError('')
    if (password || password2) {
      if (password.length < 4) {
        setError('密码至少 4 位')
        return
      }
      if (password !== password2) {
        setError('两次输入的密码不一致')
        return
      }
    }
    const body: Record<string, string> = {}
    if (password) body.password = password
    if (apiKey.trim()) body.api_key = apiKey.trim()
    if (baseUrl.trim()) body.base_url = baseUrl.trim()
    if (model.trim()) body.model = model.trim()
    setBusy(true)
    try {
      const r = await postSetup(body)
      if (r.token && r.user) setAuth(r.token, r.user) // 设了密码：立即登录，无感切换
      await qc.invalidateQueries({ queryKey: SETUP_QUERY_KEY })
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '保存失败，请重试')
      setBusy(false)
    }
  }

  async function skip() {
    if (busy) return
    setBusy(true)
    try {
      await dismissSetup()
    } catch {
      // dismiss 失败仅表现为向导复现，不阻塞进入
    }
    await qc.invalidateQueries({ queryKey: SETUP_QUERY_KEY })
    navigate('/', { replace: true })
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-50 via-slate-50 to-blue-100 dark:from-slate-900 dark:via-slate-900 dark:to-slate-800 px-4 py-8">
      <div className="w-full max-w-lg">
        <div className="animate-fade-in-up">
          <h1 className="text-2xl font-bold tracking-tight mb-1">欢迎使用 Deep-tutor</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mb-5">
            首次使用，花 30 秒完成初始设置（均可跳过，稍后在「设置」页配置）
          </p>
        </div>

        <form onSubmit={finish} className="space-y-4">
          {/* 步骤 1：访问密码（可选） */}
          <section className="animate-fade-in-up rounded-xl border bg-white p-5 shadow-sm dark:border-slate-700 dark:bg-slate-800" style={{ animationDelay: '80ms' }}>
            <h2 className="text-base font-semibold mb-1">
              1. 设置访问密码 <span className="text-xs font-normal text-slate-400">（可选）</span>
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">
              不设置则免登录直接使用；若本机处于局域网中，其他人也能访问本应用，建议设置。
            </p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="setup-pw" className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">
                  密码（≥4 位）
                </label>
                <input
                  id="setup-pw"
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-900"
                />
              </div>
              <div>
                <label htmlFor="setup-pw2" className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">
                  确认密码
                </label>
                <input
                  id="setup-pw2"
                  type="password"
                  autoComplete="new-password"
                  value={password2}
                  onChange={(e) => setPassword2(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-900"
                />
              </div>
            </div>
          </section>

          {/* 步骤 2：AI API Key（可选） */}
          <section className="animate-fade-in-up rounded-xl border bg-white p-5 shadow-sm dark:border-slate-700 dark:bg-slate-800" style={{ animationDelay: '160ms' }}>
            <h2 className="text-base font-semibold mb-1">
              2. 配置 AI API Key <span className="text-xs font-normal text-slate-400">（可选）</span>
            </h2>
            <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">
              生成学习方案需要 AI 服务密钥（如 Anthropic 的 sk-ant-…，或任何兼容 Anthropic 协议的服务）。
              暂时没有可跳过，之后在「设置 → 供应商档案」添加。
            </p>
            <label htmlFor="setup-key" className="block text-sm font-medium text-slate-700 dark:text-slate-200 mb-1">
              API Key
            </label>
            <input
              id="setup-key"
              type="password"
              autoComplete="off"
              placeholder="sk-ant-…"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-900"
            />
            <button
              type="button"
              onClick={() => setShowAdvanced((v) => !v)}
              aria-expanded={showAdvanced}
              className="mt-2 text-xs text-slate-500 transition-colors hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
            >
              {showAdvanced ? '▾ 收起高级选项' : '▸ 高级选项（自定义端点 / 模型）'}
            </button>
            {showAdvanced && (
              <div className="mt-2 grid animate-fade-in grid-cols-2 gap-3">
                <div>
                  <label htmlFor="setup-base-url" className="block text-xs font-medium text-slate-600 dark:text-slate-300 mb-1">
                    自定义端点 base_url
                  </label>
                  <input
                    id="setup-base-url"
                    type="text"
                    placeholder="https://…（留空为官方）"
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-900"
                  />
                </div>
                <div>
                  <label htmlFor="setup-model" className="block text-xs font-medium text-slate-600 dark:text-slate-300 mb-1">
                    模型
                  </label>
                  <input
                    id="setup-model"
                    type="text"
                    placeholder="留空为默认模型"
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-900"
                  />
                </div>
              </div>
            )}
          </section>

          {error && (
            <p role="alert" className="animate-shake rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              {error}
            </p>
          )}

          <div className="flex animate-fade-in-up items-center gap-3" style={{ animationDelay: '240ms' }}>
            <button
              type="submit"
              disabled={busy}
              className="flex-1 rounded-lg bg-blue-600 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98] disabled:opacity-50 disabled:hover:shadow-sm disabled:active:scale-100"
            >
              {busy ? '保存中…' : '完成并进入'}
            </button>
            <button
              type="button"
              onClick={skip}
              disabled={busy}
              className="px-4 py-2 text-sm text-slate-500 transition-colors hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200 disabled:opacity-50"
            >
              跳过，稍后配置
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

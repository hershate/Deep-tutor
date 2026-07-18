import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import ProviderManager from '../components/ProviderManager'
import UserManager from '../components/UserManager'
import { ApiError, getSettings, postSetup } from '../lib/api'
import { setAuth } from '../lib/auth'
import { SETUP_QUERY_KEY, useSetupStatus } from '../lib/setup'

/** 设置页（/settings）：用户管理（admin，iter 60）+ LLM 供应商档案管理（iter 52，可编辑，优先于 .env）+ 后端配置只读快照。
 *  iter 89：开放模式（零配置未设密码）时顶部显示「访问密码」卡——设置后用返回 JWT 无感切换为登录态。 */
export default function Settings() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
    staleTime: 60_000,
  })
  const { data: setupStatus } = useSetupStatus()

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">设置</h2>

      {setupStatus?.open_mode && <PasswordCard />}

      <UserManager />

      <ProviderManager />

      <p className="text-sm text-slate-500 dark:text-slate-400">
        以下为服务端 .env 配置快照（只读）；启用供应商档案后 LLM 调用以档案为准。
      </p>

      {isLoading && <p className="text-slate-500 dark:text-slate-400">加载中…</p>}
      {error && (
        <p role="alert" className="text-red-600 dark:text-red-400">
          {/* err.md D16：显示真实错误信息 */}
          加载失败：{error instanceof Error ? error.message : '未知错误'}
        </p>
      )}

      {data && (
        <dl className="divide-y rounded-lg border bg-white dark:border-slate-700 dark:bg-slate-800">
          <Row label="模型" value={data.anthropic_model} />
          <Row label="会话预算 (USD)" value={`$${data.budget_per_session_usd}`} />
          <Row label="每日预算 (USD)" value={`$${data.daily_budget_usd}`} />
          <Row label="工具调用上限 / 会话" value={String(data.max_tool_calls)} />
          <Row label="上传上限 (MB)" value={String(data.max_upload_mb)} />
          <Row label="搜索引擎" value={data.search_engine} />
          <Row
            label="Anthropic API Key"
            value={data.anthropic_api_key_set ? '已配置' : '未配置'}
            status={data.anthropic_api_key_set ? 'ok' : 'warn'}
          />
          <Row
            label="Bing API Key"
            value={data.bing_api_key_set ? '已配置' : '未配置'}
            status={data.bing_api_key_set ? 'ok' : 'warn'}
          />
          <Row
            label="认证 Token"
            value={data.auth_token_set ? '已配置' : '未配置'}
            status={data.auth_token_set ? 'ok' : 'warn'}
          />
          <Row
            label="图片 OCR（tesseract）"
            value={data.ocr_available ? '可用' : '不可用（需安装 tesseract + pytesseract）'}
            status={data.ocr_available ? 'ok' : 'warn'}
          />
          <Row
            label={`音视频转写（faster-whisper ${data.whisper_model}，≤${data.media_max_minutes} 分钟）`}
            value={data.whisper_available ? '可用' : '不可用（pip install faster-whisper）'}
            status={data.whisper_available ? 'ok' : 'warn'}
          />
        </dl>
      )}
    </div>
  )
}

function Row({
  label,
  value,
  status,
}: {
  label: string
  value: string
  status?: 'ok' | 'warn'
}) {  const color =
    status === 'ok'
      ? 'text-green-600 dark:text-green-400'
      : status === 'warn'
        ? 'text-amber-600 dark:text-amber-400'
        : 'text-slate-800 dark:text-slate-100' // err.md D11：默认色补 dark 变体（此前深色模式近黑底黑字）
  return (
    <div className="flex items-center justify-between px-4 py-3">
      <dt className="text-sm text-slate-600 dark:text-slate-300">{label}</dt>
      <dd className={`text-sm font-medium ${color}`}>{value}</dd>
    </div>
  )
}

/** 访问密码卡（iter 89）：仅开放模式（零配置未设密码）显示。
 *  走 POST /setup（admin 身份），返回 JWT 立即 setAuth——无感切换为登录态，
 *  避免「设完密码所有请求 401 卡死」。 */
function PasswordCard() {
  const qc = useQueryClient()
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function submit() {
    if (busy) return
    setErr('')
    if (pw.length < 4) {
      setErr('密码至少 4 位')
      return
    }
    if (pw !== pw2) {
      setErr('两次输入的密码不一致')
      return
    }
    setBusy(true)
    try {
      const r = await postSetup({ password: pw })
      if (r.token && r.user) setAuth(r.token, r.user)
      await qc.invalidateQueries({ queryKey: SETUP_QUERY_KEY })
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : '设置失败，请重试')
      setBusy(false)
    }
  }

  return (
    <section className="rounded-lg border border-amber-300 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950">
      <h3 className="text-sm font-semibold text-amber-900 dark:text-amber-100">设置访问密码</h3>
      <p className="mt-1 text-xs text-amber-800 dark:text-amber-200">
        当前免登录运行，局域网内任何人可访问。设置密码后需登录使用（账号 admin）。
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="pw-card-1" className="mb-1 block text-xs font-medium text-amber-900 dark:text-amber-100">
            密码（≥4 位）
          </label>
          <input
            id="pw-card-1"
            type="password"
            autoComplete="new-password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            className="w-40 rounded border px-3 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-800"
          />
        </div>
        <div>
          <label htmlFor="pw-card-2" className="mb-1 block text-xs font-medium text-amber-900 dark:text-amber-100">
            确认密码
          </label>
          <input
            id="pw-card-2"
            type="password"
            autoComplete="new-password"
            value={pw2}
            onChange={(e) => setPw2(e.target.value)}
            className="w-40 rounded border px-3 py-1.5 text-sm dark:border-slate-600 dark:bg-slate-800"
          />
        </div>
        <button
          type="button"
          onClick={submit}
          disabled={busy}
          className="rounded bg-amber-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-50"
        >
          {busy ? '设置中…' : '设置密码'}
        </button>
      </div>
      {err && (
        <p role="alert" className="mt-2 text-xs text-red-600 dark:text-red-400">
          {err}
        </p>
      )}
    </section>
  )
}

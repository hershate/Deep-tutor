import { useQuery } from '@tanstack/react-query'
import { getSettings } from '../lib/api'

/** 设置页（/settings）：只读展示后端配置（plan/06 §四.7）。
 * Key/模型/预算由服务端 .env 配置，前端不可修改（plan 假设 4）。 */
export default function Settings() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
    staleTime: 60_000,
  })

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">设置</h2>
      <p className="text-sm text-slate-500">
        当前后端配置（只读）。API Key / 模型 / 预算由服务端 .env 配置，前端不可修改。
      </p>

      {isLoading && <p className="text-slate-500">加载中…</p>}
      {error && <p className="text-red-600">加载失败（请确认已配置认证 token）</p>}

      {data && (
        <dl className="divide-y rounded-lg border bg-white">
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
}) {
  const color =
    status === 'ok' ? 'text-green-600' : status === 'warn' ? 'text-amber-600' : 'text-slate-800'
  return (
    <div className="flex items-center justify-between px-4 py-3">
      <dt className="text-sm text-slate-600">{label}</dt>
      <dd className={`text-sm font-medium ${color}`}>{value}</dd>
    </div>
  )
}

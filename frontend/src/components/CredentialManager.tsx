// API Key 凭证档案管理（iter 223：设置页可视化管理全部 Key，多档案卡片切换）。
// 三类：anthropic（Anthropic 回退 Key，单活）/ bing（Bing 搜索 Key，单活）/
// auth_token（个人访问令牌，多活——任一 active 令牌均以本人身份通过认证）。
// 卡片列表（掩码 key）+ 新增/编辑（key 留空保留原值）+ 启用/停用 + 停用全部 + 删除。
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  activateCredential,
  createCredential,
  deactivateAllCredentials,
  deactivateCredential,
  deleteCredential,
  listCredentials,
  updateCredential,
  type CredentialKind,
  type CredentialProfile,
} from '../lib/api'
import { SkeletonLines } from './Skeleton'

interface KindMeta {
  label: string
  hint: string
  keyPlaceholder: string
  keyHint?: string
  multi: boolean // 多活类（auth_token）：任一 active 均有效；单活类：启用互斥
  randomable?: boolean // 支持浏览器端随机生成令牌
}

const KIND_META: Record<CredentialKind, KindMeta> = {
  anthropic: {
    label: 'Anthropic 回退 Key',
    hint: 'LLM 供应商档案 > 此回退 Key 档案 > .env。启用后当无供应商档案生效时使用该 Key（官方端点 + 默认模型）；全部停用回退 .env。',
    keyPlaceholder: 'sk-ant-...',
    multi: false,
  },
  bing: {
    label: 'Bing 搜索 Key',
    hint: '启用后资源搜索走 Bing 付费 API（auto 模式优先于免 Key 网页版）；全部停用回退 .env BING_API_KEY（未配置则网页版兜底）。',
    keyPlaceholder: 'Bing Search API Key',
    multi: false,
  },
  auth_token: {
    label: '访问令牌（API / 浏览器扩展）',
    hint: '个人访问令牌：启用中的令牌可用于 API / 浏览器扩展认证（以你的身份）。令牌即凭证，请用强随机串；.env AUTH_TOKEN 始终有效作回退。',
    keyPlaceholder: '至少 8 位的强随机令牌',
    keyHint: '至少 8 位——它可直接用于登录你的账号',
    multi: true,
    randomable: true,
  },
}

interface FormState {
  id: number | null // null=新增；否则编辑该档案
  name: string
  api_key: string
  activate: boolean
}

const EMPTY_FORM: FormState = { id: null, name: '', api_key: '', activate: false }

function randomToken(): string {
  const buf = new Uint8Array(24)
  crypto.getRandomValues(buf)
  return Array.from(buf, (b) => b.toString(16).padStart(2, '0')).join('')
}

export default function CredentialManager() {
  return (
    <section aria-label="API Key 档案" className="rounded-xl border bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800">
      <h3 className="font-semibold">API Key 档案</h3>
      <p className="mb-3 mt-1 text-xs text-slate-500 dark:text-slate-400">
        每类 Key 可建多个档案卡片，点「启用」快速切换；Key 只写不读（仅显示掩码末 4 位），存本机数据库。
      </p>
      <div className="space-y-4">
        {(Object.keys(KIND_META) as CredentialKind[]).map((kind) => (
          <KindSection key={kind} kind={kind} />
        ))}
      </div>
    </section>
  )
}

function KindSection({ kind }: { kind: CredentialKind }) {
  const meta = KIND_META[kind]
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: ['credentials', kind],
    queryFn: () => listCredentials(kind),
  })
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [showForm, setShowForm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [opError, setOpError] = useState('')

  const credentials = data?.credentials ?? []
  const hasActive = credentials.some((c) => c.is_active)

  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    setOpError('')
    try {
      await action()
      await qc.invalidateQueries({ queryKey: ['credentials', kind] })
      await qc.invalidateQueries({ queryKey: ['settings'] }) // 快照行的 Key 生效来源随动
      setForm(EMPTY_FORM)
      setShowForm(false)
    } catch (e) {
      setOpError(e instanceof ApiError ? e.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  function submit() {
    const name = form.name.trim()
    const apiKey = form.api_key.trim()
    if (form.id === null) {
      if (!apiKey) {
        setOpError('新增档案必须填写 Key')
        return
      }
      void run(() => createCredential({ kind, name, api_key: apiKey, activate: form.activate }))
    } else {
      void run(() => updateCredential(form.id!, { name, ...(apiKey ? { api_key: apiKey } : {}) }))
    }
  }

  function startEdit(c: CredentialProfile) {
    setForm({ id: c.id, name: c.name, api_key: '', activate: c.is_active })
    setShowForm(true)
    setOpError('')
  }

  return (
    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-100">{meta.label}</h4>
        <div className="flex items-center gap-2">
          {hasActive && (
            <button
              onClick={() => void run(() => deactivateAllCredentials(kind))}
              disabled={busy}
              className="rounded-lg border px-2.5 py-1 text-xs transition-all hover:bg-slate-50 active:scale-95 disabled:opacity-50 disabled:active:scale-100 dark:border-slate-600 dark:hover:bg-slate-700"
            >
              停用全部{meta.multi ? '' : '（回退 .env）'}
            </button>
          )}
          <button
            onClick={() => {
              setForm(EMPTY_FORM)
              setShowForm(!showForm)
              setOpError('')
            }}
            className="rounded-lg bg-blue-600 px-2.5 py-1 text-xs text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98]"
          >
            {showForm && form.id === null ? '取消' : '新增'}
          </button>
        </div>
      </div>
      <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">{meta.hint}</p>

      {opError && (
        <div role="alert" className="mb-2 animate-fade-in rounded-lg border border-red-200 bg-red-50 p-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {opError}
        </div>
      )}

      {showForm && (
        <form
          className="mb-3 animate-fade-in space-y-2 rounded-lg border border-slate-200 p-3 dark:border-slate-700"
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <label className="text-sm">
              名称
              <input
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="mt-1 w-full rounded-lg border px-2 py-1 transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-900"
                placeholder="如：主力 Key"
              />
            </label>
            <label className="text-sm">
              Key{form.id !== null && '（留空保持不变）'}
              <div className="mt-1 flex gap-1">
                <input
                  value={form.api_key}
                  onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                  className="w-full rounded-lg border px-2 py-1 transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-900"
                  placeholder={form.id !== null ? '留空保持不变' : meta.keyPlaceholder}
                  type="password"
                  autoComplete="new-password"
                  required={form.id === null}
                />
                {meta.randomable && (
                  <button
                    type="button"
                    onClick={() => setForm({ ...form, api_key: randomToken() })}
                    className="shrink-0 rounded-lg border px-2 py-1 text-xs transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
                  >
                    随机生成
                  </button>
                )}
              </div>
              {meta.keyHint && form.id === null && (
                <span className="mt-0.5 block text-xs text-slate-400">{meta.keyHint}</span>
              )}
            </label>
          </div>
          {form.id === null && (
            <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
              <input
                type="checkbox"
                checked={form.activate}
                onChange={(e) => setForm({ ...form, activate: e.target.checked })}
                className="accent-blue-600"
              />
              创建后立即启用{!meta.multi && '（停用同类其他档案）'}
            </label>
          )}
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={busy}
              className="rounded-lg bg-blue-600 px-3 py-1 text-sm text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[.98] disabled:opacity-50 disabled:active:scale-100"
            >
              {form.id === null ? '创建' : '保存'}
            </button>
            <button
              type="button"
              onClick={() => {
                setShowForm(false)
                setForm(EMPTY_FORM)
                setOpError('')
              }}
              className="rounded-lg border px-3 py-1 text-sm transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
            >
              取消
            </button>
          </div>
        </form>
      )}

      {isLoading && <SkeletonLines lines={2} />}
      {error && (
        <p role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          档案列表加载失败
        </p>
      )}
      {!isLoading && !error && credentials.length === 0 && (
        <p className="text-sm text-slate-500 dark:text-slate-400">暂无档案。</p>
      )}
      <ul className="space-y-2">
        {credentials.map((c) => (
          <li
            key={c.id}
            className={`flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 transition-colors duration-200 ${
              c.is_active
                ? 'border-green-300 bg-green-50 dark:border-green-800 dark:bg-green-950'
                : 'border-slate-200 hover:border-slate-300 dark:border-slate-700 dark:hover:border-slate-600'
            }`}
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium">{c.name}</span>
                {c.is_active && (
                  <span className="rounded bg-green-600 px-1.5 py-0.5 text-xs text-white">
                    {meta.multi ? '已启用' : '使用中'}
                  </span>
                )}
              </div>
              <div className="mt-0.5 truncate text-xs text-slate-500 dark:text-slate-400">
                Key {c.api_key_masked}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {c.is_active ? (
                <button
                  onClick={() => void run(() => deactivateCredential(c.id))}
                  disabled={busy}
                  className="rounded-lg border px-2.5 py-1 text-sm transition-all hover:bg-slate-50 active:scale-95 disabled:opacity-50 disabled:active:scale-100 dark:border-slate-600 dark:hover:bg-slate-700"
                >
                  停用
                </button>
              ) : (
                <button
                  onClick={() => void run(() => activateCredential(c.id))}
                  disabled={busy}
                  className="rounded-lg border px-2.5 py-1 text-sm transition-all hover:bg-slate-50 active:scale-95 disabled:opacity-50 disabled:active:scale-100 dark:border-slate-600 dark:hover:bg-slate-700"
                >
                  启用
                </button>
              )}
              <button
                onClick={() => startEdit(c)}
                disabled={busy}
                className="rounded-lg border px-2.5 py-1 text-sm transition-all hover:bg-slate-50 active:scale-95 disabled:opacity-50 disabled:active:scale-100 dark:border-slate-600 dark:hover:bg-slate-700"
              >
                编辑
              </button>
              <button
                onClick={() => {
                  if (window.confirm(`确定删除${meta.label}档案「${c.name}」？`))
                    void run(() => deleteCredential(c.id))
                }}
                disabled={busy}
                className="rounded-lg border border-red-200 px-2.5 py-1 text-sm text-red-600 transition-all hover:bg-red-50 active:scale-95 disabled:opacity-50 disabled:active:scale-100 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
              >
                删除
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

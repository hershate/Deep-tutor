// LLM 供应商档案管理（自定义供应商多档案切换，类 CC-switch，iter 52）。
// 列表（掩码 key）+ 新增/编辑（key 留空保留原值）+ 启用（至多一个）+ 停用全部（回退 .env）+ 删除。
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  activateProvider,
  createProvider,
  deactivateProviders,
  deleteProvider,
  listProviders,
  updateProvider,
  type ProviderProfile,
} from '../lib/api'

interface FormState {
  id: number | null // null=新增；否则编辑该档案
  name: string
  base_url: string
  api_key: string
  model: string
}

const EMPTY_FORM: FormState = { id: null, name: '', base_url: '', api_key: '', model: '' }

export default function ProviderManager() {
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({ queryKey: ['providers'], queryFn: listProviders })
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [showForm, setShowForm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [opError, setOpError] = useState('')

  const providers = data?.providers ?? []
  const hasActive = providers.some((p) => p.is_active)

  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    setOpError('')
    try {
      await action()
      await qc.invalidateQueries({ queryKey: ['providers'] })
      setForm(EMPTY_FORM)
      setShowForm(false)
    } catch (e) {
      setOpError(e instanceof ApiError ? e.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  function submit() {
    const body = {
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      model: form.model.trim(),
      ...(form.api_key.trim() ? { api_key: form.api_key.trim() } : {}),
    }
    if (form.id === null) {
      if (!form.api_key.trim()) {
        setOpError('新增供应商必须填写 API Key')
        return
      }
      void run(() => createProvider({ ...body, api_key: form.api_key.trim() }))
    } else {
      void run(() => updateProvider(form.id!, body))
    }
  }

  function startEdit(p: ProviderProfile) {
    setForm({ id: p.id, name: p.name, base_url: p.base_url, api_key: '', model: p.model })
    setShowForm(true)
    setOpError('')
  }

  return (
    <section aria-label="LLM 供应商" className="rounded-lg border bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-semibold">LLM 供应商</h3>
        <div className="flex items-center gap-2">
          {hasActive && (
            <button
              onClick={() => void run(deactivateProviders)}
              disabled={busy}
              className="rounded-lg border px-3 py-1 text-sm hover:bg-slate-50 disabled:opacity-50"
            >
              停用全部（回退 .env）
            </button>
          )}
          <button
            onClick={() => {
              setForm(EMPTY_FORM)
              setShowForm(!showForm)
              setOpError('')
            }}
            className="rounded-lg bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700"
          >
            {showForm && form.id === null ? '取消' : '新增供应商'}
          </button>
        </div>
      </div>
      <p className="mb-3 text-xs text-slate-500">
        启用任一档案后，所有 LLM 调用走该供应商（Anthropic 兼容端点）；全部停用则回退服务端 .env 配置。
      </p>

      {opError && (
        <div role="alert" className="mb-3 rounded-lg bg-red-50 p-2 text-sm text-red-700">
          {opError}
        </div>
      )}

      {showForm && (
        <form
          className="mb-4 space-y-2 rounded-lg border border-slate-200 p-3"
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
                className="mt-1 w-full rounded border px-2 py-1"
                placeholder="如：官方中转"
              />
            </label>
            <label className="text-sm">
              模型（留空用默认）
              <input
                value={form.model}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
                className="mt-1 w-full rounded border px-2 py-1"
                placeholder="如：claude-sonnet-5"
              />
            </label>
          </div>
          <label className="block text-sm">
            Base URL（留空用 Anthropic 官方端点）
            <input
              value={form.base_url}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              className="mt-1 w-full rounded border px-2 py-1"
              placeholder="https://relay.example.com"
              type="url"
            />
          </label>
          <label className="block text-sm">
            API Key{form.id !== null && '（留空保持不变）'}
            <input
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              className="mt-1 w-full rounded border px-2 py-1"
              placeholder={form.id !== null ? '留空保持不变' : 'sk-...'}
              type="password"
              autoComplete="new-password"
              required={form.id === null}
            />
          </label>
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={busy}
              className="rounded-lg bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
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
              className="rounded-lg border px-3 py-1 text-sm hover:bg-slate-50"
            >
              取消
            </button>
          </div>
        </form>
      )}

      {isLoading && <p className="text-sm text-slate-500">加载中…</p>}
      {error && (
        <p role="alert" className="text-sm text-red-600">
          供应商列表加载失败
        </p>
      )}
      {!isLoading && !error && providers.length === 0 && (
        <p className="text-sm text-slate-500">暂无自定义供应商，当前使用服务端 .env 配置。</p>
      )}
      <ul className="space-y-2">
        {providers.map((p) => (
          <li
            key={p.id}
            className={`flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 ${
              p.is_active ? 'border-green-300 bg-green-50' : 'border-slate-200'
            }`}
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium">{p.name}</span>
                {p.is_active && (
                  <span className="rounded bg-green-600 px-1.5 py-0.5 text-xs text-white">使用中</span>
                )}
              </div>
              <div className="mt-0.5 truncate text-xs text-slate-500">
                {p.base_url || 'Anthropic 官方端点'} · {p.model || '默认模型'} · Key {p.api_key_masked}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {!p.is_active && (
                <button
                  onClick={() => void run(() => activateProvider(p.id))}
                  disabled={busy}
                  className="rounded-lg border px-2.5 py-1 text-sm hover:bg-slate-50 disabled:opacity-50"
                >
                  启用
                </button>
              )}
              <button
                onClick={() => startEdit(p)}
                disabled={busy}
                className="rounded-lg border px-2.5 py-1 text-sm hover:bg-slate-50 disabled:opacity-50"
              >
                编辑
              </button>
              <button
                onClick={() => {
                  if (window.confirm(`确定删除供应商「${p.name}」？`))
                    void run(() => deleteProvider(p.id))
                }}
                disabled={busy}
                className="rounded-lg border border-red-200 px-2.5 py-1 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50"
              >
                删除
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}

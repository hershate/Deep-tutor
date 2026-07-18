// 用户管理（P4 多用户，iter 60；仅管理员可见/可操作）。
// 列表 + 新增（用户名/密码/显示名/管理员）+ 改名 + 重置密码 + 授予/撤销管理员 + 删除（级联其数据）。
// 非管理员（403）-> 整段隐藏（env token 单用户部署无用户体系时同样隐藏）。
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ApiError,
  createUser,
  deleteUser,
  listUsers,
  updateUser,
  type AdminUser,
} from '../lib/api'
import { getStoredUser } from '../lib/auth'

interface CreateForm {
  username: string
  password: string
  name: string
  is_admin: boolean
}

const EMPTY_CREATE: CreateForm = { username: '', password: '', name: '', is_admin: false }

export default function UserManager() {
  const qc = useQueryClient()
  const me = getStoredUser()
  const { data, isLoading, error } = useQuery({
    queryKey: ['users'],
    queryFn: listUsers,
    retry: false, // 403（非管理员）立即定态，不重试
  })
  const [form, setForm] = useState<CreateForm>(EMPTY_CREATE)
  const [showForm, setShowForm] = useState(false)
  const [resetId, setResetId] = useState<number | null>(null) // 重置密码目标用户
  const [resetPw, setResetPw] = useState('')
  const [busy, setBusy] = useState(false)
  const [opError, setOpError] = useState('')

  // 非管理员/无权限 -> 不渲染该板块（403 属预期，非故障）
  if (error instanceof ApiError && error.status === 403) return null

  const users = data?.users ?? []

  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    setOpError('')
    try {
      await action()
      await qc.invalidateQueries({ queryKey: ['users'] })
      setForm(EMPTY_CREATE)
      setShowForm(false)
      setResetId(null)
      setResetPw('')
    } catch (e) {
      setOpError(e instanceof ApiError ? e.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  function submitCreate() {
    if (form.password.length < 4) {
      setOpError('密码至少 4 位')
      return
    }
    void run(() =>
      createUser({
        username: form.username.trim(),
        password: form.password,
        name: form.name.trim(),
        is_admin: form.is_admin,
      }),
    )
  }

  function submitReset() {
    if (resetId === null) return
    if (resetPw.length < 4) {
      setOpError('密码至少 4 位')
      return
    }
    void run(() => updateUser(resetId, { password: resetPw }))
  }

  function toggleAdmin(u: AdminUser) {
    if (u.is_admin) {
      if (!window.confirm(`确定撤销「${u.username}」的管理员权限？`)) return
      void run(() => updateUser(u.id, { is_admin: false }))
    } else {
      void run(() => updateUser(u.id, { is_admin: true }))
    }
  }

  function rename(u: AdminUser) {
    const name = window.prompt(`修改「${u.username}」的显示名：`, u.name)
    if (name === null || !name.trim() || name.trim() === u.name) return
    void run(() => updateUser(u.id, { name: name.trim() }))
  }

  function remove(u: AdminUser) {
    if (
      window.confirm(
        `确定删除用户「${u.username}」？\n其会话、方案、供应商档案、用量记录将一并删除，不可恢复。`,
      )
    ) {
      void run(() => deleteUser(u.id))
    }
  }

  return (
    <section aria-label="用户管理" className="rounded-lg border bg-white dark:border-slate-700 dark:bg-slate-800 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-semibold">用户管理</h3>
        <button
          onClick={() => {
            setForm(EMPTY_CREATE)
            setShowForm(!showForm)
            setOpError('')
          }}
          className="rounded-lg bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700"
        >
          {showForm ? '取消' : '新增用户'}
        </button>
      </div>
      <p className="mb-3 text-xs text-slate-500 dark:text-slate-400">仅管理员可见。删除用户将级联删除其全部数据（不可恢复）。</p>

      {opError && (
        <div role="alert" className="mb-3 rounded-lg bg-red-50 dark:bg-red-950 p-2 text-sm text-red-700 dark:text-red-300">
          {opError}
        </div>
      )}

      {showForm && (
        <form
          className="mb-4 space-y-2 rounded-lg border border-slate-200 dark:border-slate-700 p-3"
          onSubmit={(e) => {
            e.preventDefault()
            submitCreate()
          }}
        >
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <label className="text-sm">
              用户名（登录用）
              <input
                required
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                className="mt-1 w-full rounded border px-2 py-1"
                autoComplete="off"
              />
            </label>
            <label className="text-sm">
              显示名（留空同用户名）
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="mt-1 w-full rounded border px-2 py-1"
                autoComplete="off"
              />
            </label>
          </div>
          <label className="block text-sm">
            初始密码（至少 4 位）
            <input
              required
              minLength={4}
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              className="mt-1 w-full rounded border px-2 py-1"
              autoComplete="new-password"
            />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.is_admin}
              onChange={(e) => setForm({ ...form, is_admin: e.target.checked })}
            />
            设为管理员
          </label>
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={busy}
              className="rounded-lg bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            >
              创建
            </button>
            <button
              type="button"
              onClick={() => {
                setShowForm(false)
                setForm(EMPTY_CREATE)
                setOpError('')
              }}
              className="rounded-lg border px-3 py-1 text-sm hover:bg-slate-50 dark:hover:bg-slate-700"
            >
              取消
            </button>
          </div>
        </form>
      )}

      {isLoading && <p className="text-sm text-slate-500 dark:text-slate-400">加载中…</p>}
      {error && !(error instanceof ApiError && error.status === 403) && (
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          用户列表加载失败
        </p>
      )}
      <ul className="space-y-2">
        {users.map((u) => (
          <li
            key={u.id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 dark:border-slate-700 p-3"
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium">{u.name}</span>
                <span className="text-xs text-slate-500 dark:text-slate-400">@{u.username}</span>
                {u.is_admin && <span className="rounded bg-amber-50 dark:bg-amber-9500 px-1.5 py-0.5 text-xs text-white">管理员</span>}
                {me?.id === u.id && <span className="rounded bg-slate-200 px-1.5 py-0.5 text-xs">当前登录</span>}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => rename(u)}
                disabled={busy}
                className="rounded-lg border px-2.5 py-1 text-sm hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50"
              >
                改名
              </button>
              <button
                onClick={() => {
                  setResetId(resetId === u.id ? null : u.id)
                  setResetPw('')
                  setOpError('')
                }}
                disabled={busy}
                className="rounded-lg border px-2.5 py-1 text-sm hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50"
              >
                重置密码
              </button>
              {me?.id !== u.id && (
                <>
                  <button
                    onClick={() => toggleAdmin(u)}
                    disabled={busy}
                    className="rounded-lg border px-2.5 py-1 text-sm hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50"
                  >
                    {u.is_admin ? '撤销管理员' : '设为管理员'}
                  </button>
                  <button
                    onClick={() => remove(u)}
                    disabled={busy}
                    className="rounded-lg border border-red-200 px-2.5 py-1 text-sm text-red-600 dark:text-red-400 hover:bg-red-50 dark:bg-red-950 disabled:opacity-50"
                  >
                    删除
                  </button>
                </>
              )}
            </div>
            {resetId === u.id && (
              <form
                className="mt-2 flex w-full items-center gap-2"
                onSubmit={(e) => {
                  e.preventDefault()
                  submitReset()
                }}
              >
                <input
                  required
                  minLength={4}
                  type="password"
                  placeholder="新密码（至少 4 位）"
                  value={resetPw}
                  onChange={(e) => setResetPw(e.target.value)}
                  className="rounded border px-2 py-1 text-sm"
                  autoComplete="new-password"
                  aria-label={`为 ${u.username} 设置新密码`}
                />
                <button
                  type="submit"
                  disabled={busy}
                  className="rounded-lg bg-blue-600 px-2.5 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  确定
                </button>
              </form>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

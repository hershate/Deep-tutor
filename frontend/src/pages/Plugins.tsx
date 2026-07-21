import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import Card from '../components/Card'
import { toast } from '../components/Toast'
import { ApiError, getBackendPlugins } from '../lib/api'
import { API_VERSION, plugins as pluginMgr, skins, widgets } from '../lib/plugins'

// 插件管理页（iter 204）：前端 JS 插件的可视化加载/移除 + 皮肤/小组件生效概览
// + 后端插件状态（管理员只读）。前端插件契约见 API.md「前端插件 API」；
// 后端插件装载（plugins/ 目录 + 重启）见 API.md「后端插件 API」。

const SLOT_LABEL: Record<string, string> = {
  'home.dashboard': '首页仪表盘',
  'sidebar.footer': '侧栏底部',
  'plan.detail': '方案详情页',
}

export default function Plugins() {
  // --- 前端插件：URL 加载/移除（localStorage 持久化，刷新自动加载）---
  const [urls, setUrls] = useState<string[]>(() => pluginMgr.list())
  const [newUrl, setNewUrl] = useState('')
  const [busy, setBusy] = useState(false)

  async function addPlugin() {
    const url = newUrl.trim()
    if (!url || busy) return
    setBusy(true)
    try {
      await pluginMgr.load(url)
      setUrls(pluginMgr.list())
      setNewUrl('')
      toast.success('插件已加载并持久化（刷新后自动加载）')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '插件加载失败')
    } finally {
      setBusy(false)
    }
  }

  function removePlugin(url: string) {
    pluginMgr.unload(url)
    setUrls(pluginMgr.list())
    toast.info('已从持久化列表移除；其已注册的小组件/皮肤需刷新页面后彻底清除')
  }

  // --- 生效概览：皮肤与小组件（订阅注册表变化，插件加载后即时反映）---
  const [activeSkin, setActiveSkin] = useState(() => skins.active())
  const [slotWidgets, setSlotWidgets] = useState(() =>
    widgets.slots().map((slot) => ({ slot, items: widgets.list(slot) })),
  )
  useEffect(() => {
    const offSkins = skins.subscribe(() => setActiveSkin(skins.active()))
    const offWidgets = widgets.subscribe(() =>
      setSlotWidgets(widgets.slots().map((slot) => ({ slot, items: widgets.list(slot) }))),
    )
    return () => {
      offSkins()
      offWidgets()
    }
  }, [])

  function applySkin(name: string) {
    if (skins.apply(name)) {
      toast.success(name === 'default' ? '已恢复默认外观' : `已应用皮肤：${name}`)
    }
  }

  // --- 后端插件状态（管理员只读；403 非管理员 -> 提示而非报错）---
  const backendQ = useQuery({ queryKey: ['backend-plugins'], queryFn: getBackendPlugins, retry: false })
  const backendForbidden = backendQ.error instanceof ApiError && backendQ.error.status === 403

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold">插件</h2>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          前端插件 API v{API_VERSION} · 契约与开发规范见仓库 API.md
        </p>
      </div>

      {/* 前端插件管理 */}
      <Card aria-label="前端插件">
        <h3 className="mb-2 font-semibold">前端插件（浏览器端）</h3>
        <p className="mb-3 text-sm text-slate-500 dark:text-slate-400">
          通过 URL 加载 JS 插件并持久化（刷新后自动按序加载）。⚠️
          插件以页面完整权限运行（可读登录态、可发请求）——只加载你信任的 URL。
        </p>
        <div className="flex gap-2">
          <input
            type="url"
            value={newUrl}
            onChange={(e) => setNewUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void addPlugin()
            }}
            placeholder="https://example.com/my-plugin.js"
            aria-label="插件 URL"
            className="min-w-0 flex-1 rounded-lg border px-3 py-2 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-900"
          />
          <button
            onClick={() => void addPlugin()}
            disabled={busy || !newUrl.trim()}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[.98] disabled:opacity-50"
          >
            {busy ? '加载中…' : '加载插件'}
          </button>
        </div>
        {urls.length === 0 ? (
          <p className="mt-3 text-sm text-slate-400 dark:text-slate-500">尚未加载任何前端插件。</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {urls.map((url) => (
              <li
                key={url}
                className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm dark:border-slate-700"
              >
                <span className="min-w-0 flex-1 truncate break-all text-slate-600 dark:text-slate-300" title={url}>
                  {url}
                </span>
                <button
                  onClick={() => removePlugin(url)}
                  className="shrink-0 rounded-lg border border-red-200 px-3 py-1 text-xs text-red-600 transition-all hover:bg-red-50 active:scale-95 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
                >
                  移除
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* 当前生效概览 */}
      <Card aria-label="当前生效">
        <h3 className="mb-3 font-semibold">当前生效</h3>
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <span className="text-sm text-slate-600 dark:text-slate-300">外观皮肤：</span>
          <select
            aria-label="选择皮肤"
            value={activeSkin}
            onChange={(e) => applySkin(e.target.value)}
            className="rounded-lg border px-2 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-800"
          >
            {skins.list().map((s) => (
              <option key={s.name} value={s.name}>
                {s.label}（{s.name}）
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-2">
          {slotWidgets.map(({ slot, items }) => (
            <div key={slot} className="text-sm">
              <span className="text-slate-600 dark:text-slate-300">
                {SLOT_LABEL[slot] ?? slot}
                <span className="ml-1 text-xs text-slate-500 dark:text-slate-400">({slot})</span>：
              </span>
              {items.length === 0 ? (
                <span className="text-slate-400 dark:text-slate-500">无小组件</span>
              ) : (
                <span className="text-slate-500 dark:text-slate-400">
                  {items.length} 个：{items.map((w) => w.id).join('、')}
                </span>
              )}
            </div>
          ))}
        </div>
      </Card>

      {/* 后端插件状态（管理员） */}
      <Card aria-label="后端插件">
        <h3 className="mb-2 font-semibold">后端插件（服务器端）</h3>
        <p className="mb-3 text-sm text-slate-500 dark:text-slate-400">
          安装方式：把 <code className="rounded bg-slate-100 px-1 dark:bg-slate-700">.py</code> 文件放入
          <code className="rounded bg-slate-100 px-1 dark:bg-slate-700">plugins/</code> 目录并重启后端
          （文件名加 <code className="rounded bg-slate-100 px-1 dark:bg-slate-700">_</code> 前缀即停用；
          详见仓库 plugins/README.md 与 API.md）。
        </p>
        {backendQ.isLoading && <p className="text-sm text-slate-400">加载中…</p>}
        {backendForbidden && (
          <p className="text-sm text-slate-400 dark:text-slate-500">后端插件状态仅管理员可见。</p>
        )}
        {backendQ.isError && !backendForbidden && (
          <p className="text-sm text-red-500">
            状态加载失败{backendQ.error instanceof ApiError ? `：${backendQ.error.message}` : ''}
          </p>
        )}
        {backendQ.data && (
          <div className="space-y-3">
            <p className="text-xs text-slate-500 dark:text-slate-500">
              后端插件 API v{backendQ.data.api_version} · 已订阅事件 {backendQ.data.subscribed_events.length} 个
            </p>
            {backendQ.data.plugins.length === 0 ? (
              <p className="text-sm text-slate-400 dark:text-slate-500">未安装任何后端插件。</p>
            ) : (
              <ul className="space-y-2">
                {backendQ.data.plugins.map((p) => (
                  <li key={p.id} className="rounded-lg border px-3 py-2 text-sm dark:border-slate-700">
                    <div className="flex flex-wrap items-center gap-2">
                      <span aria-hidden="true">{p.state === 'loaded' ? '✅' : '❌'}</span>
                      <span className="font-medium">{p.name}</span>
                      <span className="text-xs text-slate-500 dark:text-slate-400">
                        {p.id}
                        {p.version ? ` v${p.version}` : ''}
                      </span>
                    </div>
                    {p.state === 'failed' && (
                      <p className="mt-1 break-all text-xs text-red-500">{p.error}</p>
                    )}
                    {p.state === 'loaded' && (
                      <p className="mt-1 text-xs text-slate-500 dark:text-slate-500">
                        路由 {p.routes.length} · 事件 {p.events.length} · 工具 {p.tools.length}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Card>
    </div>
  )
}

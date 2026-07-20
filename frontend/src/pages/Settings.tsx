import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import ProviderManager from '../components/ProviderManager'
import CredentialManager from '../components/CredentialManager'
import UserManager from '../components/UserManager'
import { SkeletonLines } from '../components/Skeleton'
import { toast } from '../components/Toast'
import { ApiError, downloadBackup, getSettings, importBackup, postSetup, previewBackup, updatePrefs, type AppSettings, type BackupImportResult, type BackupPreview } from '../lib/api'
import { setAuth } from '../lib/auth'
import { SETUP_QUERY_KEY, useSetupStatus } from '../lib/setup'

/** 设置页（/settings）：用户管理（admin，iter 60）+ LLM 供应商档案管理（iter 52，可编辑，优先于 .env）
 *  + API Key 档案管理（iter 223：Anthropic 回退 / Bing / 访问令牌三类卡片切换）+ 生效配置只读快照。
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

      {/* iter 96 美化：各卡片错峰入场 */}
      {setupStatus?.open_mode && (
        <div className="animate-fade-in-up">
          <PasswordCard />
        </div>
      )}

      <div className="animate-fade-in-up" style={{ animationDelay: '60ms' }}>
        <UserManager />
      </div>

      <div className="animate-fade-in-up" style={{ animationDelay: '120ms' }}>
        <ProviderManager />
      </div>

      {/* iter 223：全部 Key（Anthropic 回退 / Bing / 访问令牌）档案卡片管理 */}
      <div className="animate-fade-in-up" style={{ animationDelay: '180ms' }}>
        <CredentialManager />
      </div>

      <div className="animate-fade-in-up" style={{ animationDelay: '240ms' }}>
        <BackupCard />
      </div>

      <p className="text-sm text-slate-500 dark:text-slate-400">
        以下为生效配置快照（只读）；供应商档案与 Key 档案在上方卡片管理，启用后优先于 .env。
      </p>

      {isLoading && <SkeletonLines lines={4} />}
      {error && (
        <p role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          {/* err.md D16：显示真实错误信息 */}
          加载失败：{error instanceof Error ? error.message : '未知错误'}
        </p>
      )}

      {data && (
        <dl className="animate-fade-in divide-y overflow-hidden rounded-xl border bg-white shadow-sm dark:border-slate-700 dark:bg-slate-800">
          <Row label="模型" value={data.anthropic_model} />
          <Row label="会话预算 (USD)" value={`$${data.budget_per_session_usd}`} />
          {/* 每日预算可编辑（iter 176 参数配置）：用户覆盖优先于 .env 默认 */}
          <BudgetRow data={data} />
          <Row label="工具调用上限 / 会话" value={String(data.max_tool_calls)} />
          <Row label="上传上限 (MB)" value={String(data.max_upload_mb)} />
          <Row label="搜索引擎" value={searchEngineLabel(data)} />
          <Row
            label="Anthropic API Key"
            value={anthropicKeyLabel(data)}
            status={data.anthropic_api_key_set ? 'ok' : 'warn'}
          />
          <Row
            label="Bing API Key"
            value={
              data.bing_api_key_set
                ? bingKeyLabel(data)
                : data.search_engine_effective === 'bing_web'
                  ? '未配置（使用网页版兜底）'
                  : '未配置'
            }
            // iter 222：auto/bing_web 未配置 key 是正常状态（网页版兜底），仅显式 bing 无 key 时告警
            status={data.bing_api_key_set ? 'ok' : data.search_engine === 'bing' ? 'warn' : undefined}
          />
          <Row
            label="认证 Token"
            value={authTokenLabel(data)}
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

/** iter 222：搜索引擎行展示——配置值 + auto 语义解析后的实际生效引擎。 */
function searchEngineLabel(d: AppSettings): string {
  const effText =
    d.search_engine_effective === 'bing_api'
      ? 'Bing API（付费，质量更高）'
      : d.search_engine_effective === 'bing_web'
        ? 'Bing 网页版（免 Key）'
        : '不可用'
  return d.search_engine === 'auto'
    ? `auto（当前：${effText}）`
    : `${d.search_engine}（${effText}）`
}

/** iter 223：Key 快照行标注生效来源（Key 档案可管理，.env 为回退）。 */
function anthropicKeyLabel(d: AppSettings): string {
  if (!d.anthropic_api_key_set) return '未配置'
  if (d.anthropic_key_source === 'provider_profile') return '已配置（供应商档案）'
  if (d.anthropic_key_source === 'credential') return '已配置（Key 档案）'
  if (d.anthropic_key_source === 'env') return '已配置（.env）'
  return '已配置'
}

function bingKeyLabel(d: AppSettings): string {
  if (d.bing_key_source === 'credential') return '已配置（Key 档案）'
  if (d.bing_key_source === 'env') return '已配置（.env）'
  return '已配置'
}

function authTokenLabel(d: AppSettings): string {
  if (!d.auth_token_set) return '未配置'
  if (d.auth_token_source === 'credential') return '已配置（个人令牌）'
  if (d.auth_token_source === 'env') return '已配置（.env）'
  return '已配置'
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
    status === 'ok'
      ? 'text-green-600 dark:text-green-400'
      : status === 'warn'
        ? 'text-amber-600 dark:text-amber-400'
        : 'text-slate-800 dark:text-slate-100' // err.md D11：默认色补 dark 变体（此前深色模式近黑底黑字）
  return (
    <div className="flex items-center justify-between px-4 py-3 transition-colors hover:bg-slate-50 dark:hover:bg-slate-700/50">
      <dt className="text-sm text-slate-600 dark:text-slate-300">{label}</dt>
      <dd className={`text-sm font-medium ${color}`}>{value}</dd>
    </div>
  )
}

/** 每日预算可编辑行（iter 176 参数配置）：显示有效值，覆盖时标注 .env 默认；
 *  编辑保存后同步刷新 settings/usage（用量页预算条读同一有效值）。0 = 不限。 */
function BudgetRow({ data }: { data: AppSettings }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)

  async function save(reset: boolean) {
    const n = reset ? null : Number(value)
    if (!reset && (!Number.isFinite(n) || (n as number) < 0 || (n as number) > 100000)) {
      toast.error('预算须在 0-100000 之间（0 = 不限）')
      return
    }
    setBusy(true)
    try {
      await updatePrefs(n)
      toast.success(reset ? '已恢复默认日预算' : '日预算已更新')
      setEditing(false)
      qc.invalidateQueries({ queryKey: ['settings'] })
      qc.invalidateQueries({ queryKey: ['usage'] })
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '保存失败，请重试')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex items-center justify-between gap-2 px-4 py-3 transition-colors hover:bg-slate-50 dark:hover:bg-slate-700/50">
      <dt className="text-sm text-slate-600 dark:text-slate-300">
        每日预算 (USD)
        {data.daily_budget_is_override && (
          <span className="ml-1 text-xs text-slate-400">（默认 ${data.daily_budget_usd}）</span>
        )}
      </dt>
      <dd className="flex items-center gap-2 text-sm font-medium text-slate-800 dark:text-slate-100">
        {editing ? (
          <>
            <input
              type="number"
              min={0}
              max={100000}
              step="0.5"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              aria-label="每日预算（美元）"
              className="w-24 rounded-lg border px-2 py-1 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-800"
            />
            <button
              onClick={() => save(false)}
              disabled={busy}
              className="rounded-lg bg-blue-600 px-2 py-1 text-xs text-white transition-all hover:bg-blue-700 active:scale-95 disabled:opacity-50"
            >
              保存
            </button>
            {data.daily_budget_is_override && (
              <button
                onClick={() => save(true)}
                disabled={busy}
                className="rounded-lg border px-2 py-1 text-xs transition-all hover:bg-slate-50 active:scale-95 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-700"
              >
                恢复默认
              </button>
            )}
            <button
              onClick={() => setEditing(false)}
              disabled={busy}
              className="rounded-lg px-1 py-1 text-xs text-slate-400 transition-colors hover:text-slate-600 dark:hover:text-slate-300"
            >
              取消
            </button>
          </>
        ) : (
          <>
            <span className="tabular-nums">${data.daily_budget_effective}</span>
            <button
              onClick={() => {
                setValue(String(data.daily_budget_effective))
                setEditing(true)
              }}
              className="rounded-lg border px-2 py-1 text-xs transition-all hover:bg-slate-50 active:scale-95 dark:border-slate-600 dark:hover:bg-slate-700"
            >
              编辑
            </button>
          </>
        )}
      </dd>
    </div>
  )
}

/** 数据备份卡（iter 93）：导出整库 JSON / 追加式导入还原。
 *  凭证（api_key 等）不随备份走——导入后需在供应商档案补填并激活。 */
function BackupCard() {
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [exporting, setExporting] = useState(false)
  const [importing, setImporting] = useState(false)
  const [result, setResult] = useState<BackupImportResult | null>(null)
  // iter 159：导入预检——选文件后先预览计数，确认后才真正导入
  const [pending, setPending] = useState<{ file: File; preview: BackupPreview } | null>(null)
  const [previewing, setPreviewing] = useState(false)

  async function doExport() {
    if (exporting) return
    setExporting(true)
    try {
      await downloadBackup()
      toast.success('备份已开始下载')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '导出失败，请重试')
    } finally {
      setExporting(false)
    }
  }

  // 选文件 -> 预检（不落库），用户确认后 doImport 才真正导入（iter 159）
  async function doPreview(file: File) {
    setPreviewing(true)
    setResult(null)
    setPending(null)
    try {
      const preview = await previewBackup(file)
      setPending({ file, preview })
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '预检失败，请确认文件为本应用导出的备份')
    } finally {
      setPreviewing(false)
      if (fileRef.current) fileRef.current.value = '' // 允许重复选同一文件
    }
  }

  async function doImport(file: File) {
    setImporting(true)
    setResult(null)
    try {
      const r = await importBackup(file)
      setResult(r)
      setPending(null)
      toast.success(`导入完成：${r.sessions} 个会话 / ${r.plans} 个方案`)
      await qc.invalidateQueries() // 列表页等展示新数据
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : '导入失败，请确认文件为本应用导出的备份')
    } finally {
      setImporting(false)
    }
  }

  return (
    <section className="rounded-xl border bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800">
      <h3 className="text-sm font-semibold text-slate-800 dark:text-slate-100">数据备份</h3>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        导出全部学习数据（会话/方案/知识节点/进度/打卡/自学材料）为 JSON 文件，可用于迁移或存档；
        导入为<strong>追加还原</strong>（不覆盖现有数据，重复导入会产生副本）。
        API Key 等凭证不随备份导出，导入后请在上方供应商档案中补填并激活。
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={doExport}
          disabled={exporting}
          className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98] disabled:opacity-50 disabled:hover:shadow-sm disabled:active:scale-100"
        >
          {exporting ? '导出中…' : '导出全部数据'}
        </button>
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={importing || previewing}
          className="rounded-lg border px-3 py-1.5 text-sm font-medium transition-all hover:bg-slate-50 active:scale-95 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-700"
        >
          {previewing ? '预检中…' : importing ? '导入中…' : '选择备份文件导入'}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          aria-label="选择备份文件"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) void doPreview(f)
          }}
        />
      </div>
      {/* 导入预检（iter 159）：确认前看清备份内容 */}
      {pending && (
        <div className="mt-3 animate-fade-in rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800 dark:border-blue-800 dark:bg-blue-950 dark:text-blue-200">
          <p className="tabular-nums">
            备份内容：{pending.preview.sessions} 会话 / {pending.preview.materials} 资料 /{' '}
            {pending.preview.plans} 方案 / {pending.preview.knowledge_nodes} 知识节点 /{' '}
            {pending.preview.node_notes} 笔记 / {pending.preview.study_units} 自学单元 /{' '}
            {pending.preview.quizzes} 测验 / {pending.preview.learning_logs} 打卡 /{' '}
            {pending.preview.provider_profiles} 供应商档案 / {pending.preview.prefs} 偏好
          </p>
          <p className="mt-1 text-blue-600 dark:text-blue-300">
            {pending.preview.username && <>导出人：{pending.preview.username} · </>}
            {pending.preview.exported_at && <>导出时间：{pending.preview.exported_at.replace('T', ' ')}</>}
          </p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={() => void doImport(pending.file)}
              disabled={importing}
              className="rounded-lg bg-blue-600 px-3 py-1 font-medium text-white transition-all hover:bg-blue-700 active:scale-95 disabled:opacity-50"
            >
              {importing ? '导入中…' : '确认导入（追加还原）'}
            </button>
            <button
              type="button"
              onClick={() => setPending(null)}
              disabled={importing}
              className="rounded-lg border px-3 py-1 transition-all hover:bg-white active:scale-95 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-800"
            >
              取消
            </button>
          </div>
        </div>
      )}
      {result && (
        <div className="mt-3 animate-fade-in rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200">
          <p className="tabular-nums">
            本次导入：{result.sessions} 会话 / {result.materials} 资料 / {result.plans} 方案 /{' '}
            {result.knowledge_nodes} 知识节点 / {result.study_units} 自学单元 / {result.quizzes} 测验 /{' '}
            {result.progress} 进度 / {result.learning_logs} 打卡 / {result.provider_profiles} 供应商档案 /{' '}
            {result.prefs} 偏好
          </p>
          {result.skipped.length > 0 && (
            <ul className="mt-1 list-inside list-disc">
              {result.skipped.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
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
    <section className="rounded-xl border border-amber-300 bg-amber-50 p-4 shadow-sm dark:border-amber-800 dark:bg-amber-950">
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
            className="w-40 rounded-lg border px-3 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-amber-500 dark:border-slate-600 dark:bg-slate-800"
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
            className="w-40 rounded-lg border px-3 py-1.5 text-sm transition-shadow focus:outline-none focus:ring-2 focus:ring-amber-500 dark:border-slate-600 dark:bg-slate-800"
          />
        </div>
        <button
          type="button"
          onClick={submit}
          disabled={busy}
          className="rounded-lg bg-amber-700 px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-amber-600 hover:shadow-md active:scale-[.98] disabled:opacity-50 disabled:hover:shadow-sm disabled:active:scale-100"
        >
          {busy ? '设置中…' : '设置密码'}
        </button>
      </div>
      {err && (
        <p role="alert" className="mt-2 animate-shake text-xs text-red-600 dark:text-red-400">
          {err}
        </p>
      )}
    </section>
  )
}

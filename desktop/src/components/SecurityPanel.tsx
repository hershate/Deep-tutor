import { useCallback, useEffect, useState } from 'react'
import { Check, KeyRound, LoaderCircle, Plus, ShieldCheck, Trash2, UserRound, Users } from 'lucide-react'
import { Badge, Button, Modal } from './UI'
import { backendErrorMessage, onBackendEvent, reportBackendError } from '../backend/client'
import { securityBackend } from '../backend/security'
import type { CreatedToken, LocalUser, SecurityOverview } from '../backend/security'

const quotaNames: Record<string, string> = { sessions: 'Agent 会话', notes: '笔记', materials: 'KB 文档', threads: '社区讨论' }

export default function SecurityPanel({ notify }: { notify: (message: string) => void }) {
  const [data, setData] = useState<SecurityOverview | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [userOpen, setUserOpen] = useState(false)
  const [tokenOpen, setTokenOpen] = useState(false)
  const [name, setName] = useState('')
  const [role, setRole] = useState('成员')
  const [tokenName, setTokenName] = useState('')
  const [newToken, setNewToken] = useState<CreatedToken | null>(null)
  const [removeUser, setRemoveUser] = useState<LocalUser | null>(null)
  const [quotaDrafts, setQuotaDrafts] = useState<Record<string, string>>({})

  const apply = useCallback((next: SecurityOverview) => {
    setData(next)
    setQuotaDrafts(Object.fromEntries(next.quotas.map(quota => [quota.resource, String(quota.limit)])))
  }, [])
  const refresh = useCallback(async () => {
    try {
      apply(await securityBackend.overview())
      setError('')
    } catch (cause) { setError(backendErrorMessage(cause)); reportBackendError(cause) }
  }, [apply])
  useEffect(() => {
    let active = true
    let stop: (() => void) | undefined
    void refresh()
    void onBackendEvent(event => {
      if (active && event.kind.startsWith('security.')) void refresh()
    }).then(unlisten => { if (active) stop = unlisten; else unlisten() }).catch(reportBackendError)
    return () => { active = false; stop?.() }
  }, [refresh])
  useEffect(() => { setNewToken(null) }, [data?.currentUser?.id])

  const run = async (action: string, operation: () => Promise<SecurityOverview>, message: string) => {
    if (busy) return false
    setBusy(action)
    try {
      apply(await operation())
      notify(message)
      return true
    } catch (error) { reportBackendError(error); return false }
    finally { setBusy('') }
  }
  const createUser = async () => {
    if (await run('create-user', () => securityBackend.createUser(name, role), `已创建本地用户「${name.trim()}」`)) {
      setName(''); setUserOpen(false)
    }
  }
  const createToken = async () => {
    if (busy || !tokenName.trim()) return
    setBusy('create-token')
    try {
      setNewToken(await securityBackend.createToken(tokenName))
      setTokenName(''); setTokenOpen(false)
      await refresh()
      notify('真实 PAT 已创建；明文只显示这一次')
    } catch (error) { reportBackendError(error) }
    finally { setBusy('') }
  }
  const copyToken = async () => {
    if (!newToken) return
    try { await navigator.clipboard.writeText(newToken.token); notify('PAT 已复制，请保存到安全位置') }
    catch (error) { reportBackendError(error) }
  }

  if (!data) return <div className="security-panel">{error ? <><p className="form-error" role="alert">{error}</p><p>未能读取本地用户与配额，请重试。</p></> : <><LoaderCircle size={22} className="spin" /><p>正在验证本地会话并读取用户、令牌和配额…</p></>}<Button variant="secondary" onClick={() => { setError(''); void refresh() }}>重试</Button></div>
  const current = data.currentUser
  const administrator = current?.role === '管理员' && !data.requiresLogin
  return <div className="security-panel">
    <div className="auth-chain">{data.modes.map(mode => <div key={mode.id}><span>{mode.id === 'jwt' ? '7d' : mode.id === 'pat' ? 'SHA' : '—'}</span><div><strong>{mode.label}</strong><small>{mode.detail}</small></div><Badge tone={mode.enabled ? 'green' : 'neutral'}>{mode.enabled ? '原生支持' : '已停用'}</Badge></div>)}</div>
    <div className="security-disclosure"><ShieldCheck size={18} /><p>本地身份选择不代替 Windows 账户认证。JWT 的有效期、签名与撤销由后端验证；PAT 明文不进入 SQLite 或学习备份。</p></div>
    {data.requiresLogin && <div className="setting-note">会话已过期、撤销或不可用。请重新选择一个可用的本地用户；下方不会显示访问令牌或管理操作。</div>}
    <section className="security-subsection"><div><Users size={18} /><h3>{data.requiresLogin ? '重新选择本地用户' : '本地用户'}</h3>{administrator && <Button variant="secondary" size="sm" disabled={Boolean(busy)} onClick={() => setUserOpen(true)}><Plus size={15} />管理员建用户</Button>}</div>
      {data.users.map(user => <div className="user-row" key={user.id}><span><UserRound size={17} /></span><div><strong>{user.name}{user.current && <Badge tone="green">当前</Badge>}</strong><small>{user.role} · {user.active ? '正常' : '已停用'}{user.lastLoginAt && ` · 最近登录 ${new Date(user.lastLoginAt).toLocaleString()}`}</small></div>
        {user.active && !user.current && <Button variant="ghost" size="sm" disabled={Boolean(busy)} onClick={() => void run(`login-${user.id}`, () => securityBackend.login(user.id), `已切换到「${user.name}」`)}>{busy === `login-${user.id}` ? <LoaderCircle size={14} className="spin" /> : '切换'}</Button>}
        {administrator && !user.current && <Button variant="ghost" size="sm" disabled={Boolean(busy)} onClick={() => void run(`active-${user.id}`, () => securityBackend.setActive(user.id, !user.active), user.active ? '用户已停用，其会话与 PAT 已撤销' : '用户已启用')}>{user.active ? '停用' : '启用'}</Button>}
        {administrator && !user.current && <Button variant="ghost" size="sm" disabled={Boolean(busy)} onClick={() => setRemoveUser(user)} aria-label={`移除用户 ${user.name}`}><Trash2 size={14} /></Button>}
      </div>)}
      {!data.users.some(user => user.active) && <p className="form-error">没有可用的本地用户，请先通过管理员恢复账户。</p>}
    </section>
    {current && !data.requiresLogin && <>
      <section className="security-subsection"><div><KeyRound size={18} /><h3>个人访问令牌</h3><Button variant="secondary" size="sm" disabled={Boolean(busy)} onClick={() => setTokenOpen(true)}><Plus size={15} />创建 PAT</Button></div>
        <p className="form-help">当前用户：{current.name} · 会话有效期至 {data.session ? new Date(data.session.expiresAt).toLocaleString() : '未知'}。</p>
        <div className="data-actions"><Button variant="ghost" size="sm" disabled={Boolean(busy)} onClick={() => { setBusy('logout'); securityBackend.logout().then(() => notify('已退出当前会话，请重新选择本地用户')).catch(reportBackendError).finally(() => { setBusy(''); void refresh() }) }}>退出当前会话</Button><span className="form-help">撤销当前 JWT；退出后需重新选择本地用户，重启应用不会自动恢复。</span></div>
        {newToken && <div className="token-once"><strong>请立即复制，明文只显示这一次</strong><code>{newToken.token}</code><div className="data-actions"><Button variant="secondary" size="sm" onClick={() => void copyToken()}>复制令牌</Button><Button variant="ghost" size="sm" onClick={() => setNewToken(null)}>我已保存</Button></div></div>}
        {data.tokens.map(token => <div className="token-row" key={token.id}><div><strong>{token.name}</strong><code>{token.mask}</code><small>SHA-256 摘要 · 创建于 {new Date(token.createdAt).toLocaleString()} · {token.lastUsedAt ? `最近认证 ${new Date(token.lastUsedAt).toLocaleString()}` : '尚未认证'}</small></div><Button variant="ghost" size="sm" disabled={Boolean(busy)} onClick={() => void run(`revoke-${token.id}`, () => securityBackend.revokeToken(token.id), 'PAT 已吊销，后续认证将失败')}>吊销</Button></div>)}
        {!data.tokens.length && <p className="form-help">当前用户没有可用的 PAT。创建后只会显示一次完整令牌。</p>}
      </section>
      <section className="security-subsection"><div><Check size={18} /><h3>资源用量与配额上限</h3></div><p className="form-help">用量与上限均来自当前用户的独立工作区；超过上限的写入会被原生后端拒绝，不影响其他用户。</p><div className="quota-grid">{data.quotas.map(quota => <div key={quota.resource}><span>{quotaNames[quota.resource] || quota.resource} · {quota.used} / {quota.limit}</span>{administrator ? <label>上限<input type="number" min={1} max={100000} step={1} value={quotaDrafts[quota.resource] ?? quota.limit} disabled={Boolean(busy)} onChange={event => setQuotaDrafts({ ...quotaDrafts, [quota.resource]: event.target.value })} /><Button variant="ghost" size="sm" disabled={Boolean(busy) || !/^\d+$/.test(quotaDrafts[quota.resource] || '') || Number(quotaDrafts[quota.resource]) < 1 || Number(quotaDrafts[quota.resource]) > 100000 || Number(quotaDrafts[quota.resource]) === quota.limit} onClick={() => void run(`quota-${quota.resource}`, () => securityBackend.setQuota(current.id, quota.resource, Number(quotaDrafts[quota.resource])), `${quotaNames[quota.resource]}上限已保存`)}>保存</Button></label> : <strong>{quota.used} / {quota.limit}</strong>}</div>)}</div></section>
    </>}
    <Modal open={userOpen} onClose={() => setUserOpen(false)} title="管理员创建本地用户"><div className="form-stack"><label>显示名称<input value={name} maxLength={80} onChange={event => setName(event.target.value)} /></label><label>角色<select value={role} onChange={event => setRole(event.target.value)}><option>成员</option><option>管理员</option></select></label><div className="modal-callout"><ShieldCheck size={17} />新用户将获得独立的本地身份与默认配额；创建操作会记录到原生事件日志。</div><Button disabled={!name.trim() || Boolean(busy)} onClick={() => void createUser()}>{busy === 'create-user' ? '正在创建…' : '创建用户'}</Button></div></Modal>
    <Modal open={tokenOpen} onClose={() => setTokenOpen(false)} title="创建个人访问令牌"><div className="form-stack"><label>令牌用途<input value={tokenName} maxLength={60} onChange={event => setTokenName(event.target.value)} placeholder="例如：原生扩展访问" /></label><div className="modal-callout"><KeyRound size={17} />令牌由系统安全随机数生成，数据库只保存 SHA-256 摘要。完整明文仅在下一步显示一次。</div><Button disabled={!tokenName.trim() || Boolean(busy)} onClick={() => void createToken()}>{busy === 'create-token' ? '正在生成…' : '生成真实 PAT'}</Button></div></Modal>
    <Modal open={Boolean(removeUser)} onClose={() => setRemoveUser(null)} title="移除本地用户？">{removeUser && <div className="confirm-dialog"><p>将停用「{removeUser.name}」并撤销其全部会话与 PAT，不再显示在用户列表中。学习数据不会随用户移除而删除。</p><div><Button variant="secondary" onClick={() => setRemoveUser(null)}>取消</Button><Button disabled={Boolean(busy)} onClick={() => { const id = removeUser.id; void run(`remove-${id}`, () => securityBackend.removeUser(id), '用户已移除，学习数据保留'); setRemoveUser(null) }}>确认移除</Button></div></div>}</Modal>
  </div>
}

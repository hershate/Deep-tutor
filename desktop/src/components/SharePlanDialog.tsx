import { useCallback, useEffect, useState } from 'react'
import { Copy, Download, Eye, Link2, LoaderCircle, Share2, Trash2 } from 'lucide-react'
import { Badge, Button, ConfirmDialog, Modal } from './UI'
import { backend, backendErrorMessage, onBackendEvent } from '../backend/client'
import { localShareUrl, sharingBackend } from '../backend/sharing'
import type { CreatedShare, ShareMetadata } from '../backend/sharing'
import type { LearningPlan } from '../backend/client'

const statusNames = { active: '有效', expired: '已过期', revoked: '已撤销' }

export default function SharePlanDialog({ open, onClose, notify, initialPlanId, onPreview }: {
  open: boolean
  onClose: () => void
  notify: (message: string) => void
  initialPlanId?: string
  onPreview: (token: string) => void
}) {
  const [plans, setPlans] = useState<LearningPlan[]>([])
  const [shares, setShares] = useState<ShareMetadata[]>([])
  const [selectedId, setSelectedId] = useState(initialPlanId || '')
  const [days, setDays] = useState(90)
  const [created, setCreated] = useState<CreatedShare | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const refreshShares = useCallback(async () => {
    const items = await sharingBackend.list()
    setShares(items)
  }, [])

  useEffect(() => {
    if (!open) return
    let active = true
    setLoading(true)
    setError('')
    Promise.all([backend.listLearningPlans(), sharingBackend.list()]).then(([plans, shares]) => {
      if (!active) return
      const ready = plans.filter(plan => plan.result && ['done', 'degraded'].includes(plan.status))
      setPlans(ready)
      setShares(shares)
      setSelectedId(current => ready.some(plan => plan.id === current) ? current : ready.find(plan => plan.id === initialPlanId)?.id || ready[0]?.id || '')
      setCreated(current => current && ready.some(plan => plan.id === current.share.planId) ? current : null)
    }).catch(cause => { if (active) setError(backendErrorMessage(cause)) }).finally(() => { if (active) setLoading(false) })
    let remove: (() => void) | undefined
    void onBackendEvent(event => {
      if (event.kind.startsWith('share.')) void refreshShares().catch(cause => setError(backendErrorMessage(cause)))
    }).then(unlisten => { if (active) remove = unlisten; else unlisten() })
    return () => { active = false; remove?.() }
  }, [open, initialPlanId, refreshShares])

  const create = async () => {
    if (!selectedId) return
    setBusy('create')
    setError('')
    try {
      const next = await sharingBackend.create(selectedId, days)
      setCreated(next)
      setShares(current => [next.share, ...current.filter(item => item.id !== next.share.id)])
      notify('只读分享已创建；原始令牌仅在此对话框显示，请立即复制或下载分享包')
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }

  const download = async () => {
    if (!created) return
    setBusy('download')
    setError('')
    try {
      const path = await sharingBackend.saveFile(created.share.planId)
      if (!path) return
      const result = await sharingBackend.exportPackage(created.package, path)
      notify(`已保存真实分享包（${result.bytes.toLocaleString()} 字节）`)
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }

  // 20#5：撤销分享令牌立即失效，不可恢复——确认后再执行
  const [revokeId, setRevokeId] = useState<number | null>(null)
  const revoke = async (id: number) => {
    setBusy(`revoke-${id}`)
    setError('')
    try {
      const revoked = await sharingBackend.revoke(id)
      setShares(current => current.map(item => item.id === id ? revoked : item))
      if (created?.share.id === id) setCreated(null)
      notify('本机分享已撤销，使用原令牌读取将被拒绝')
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }

  const copy = async () => {
    if (!created) return
    try {
      await navigator.clipboard.writeText(localShareUrl(created.token))
      notify('本机只读地址已复制；它不是公共互联网链接')
    } catch { notify('无法访问剪贴板，请手动复制地址') }
  }

  const matchingCreated = created?.share.planId === selectedId ? created : null
  return <Modal open={open} onClose={onClose} title="分享只读学习方案" wide>
    <div className="form-stack">
      <div className="share-status"><Link2 size={19} /><div><strong>不可变的只读方案快照</strong><p>不包含私人笔记、Agent 会话、学习进度或模型密钥。本机地址不能通过互联网访问；请将分享包发给接收者。</p></div></div>
      {error && <div className="form-error" role="alert">{error}</div>}
      <div className="form-grid">
        <label>选择已生成方案<select value={selectedId} onChange={event => setSelectedId(event.target.value)} disabled={loading || !plans.length}><option value="">请选择方案</option>{plans.map(plan => <option key={plan.id} value={plan.id}>{plan.goal} · v{plan.version}</option>)}</select></label>
        <label>有效期<select value={days} onChange={event => setDays(Number(event.target.value))}><option value={7}>7 天</option><option value={30}>30 天</option><option value={90}>90 天（默认）</option><option value={365}>365 天</option></select></label>
      </div>
      <Button onClick={() => void create()} disabled={!selectedId || Boolean(busy)}>{busy === 'create' ? <LoaderCircle size={16} className="spin" /> : <Share2 size={16} />}创建新分享</Button>
      {matchingCreated && <section className="share-created">
        <h3>新分享已保存 · 有效期至 {new Date(matchingCreated.share.expiresAt).toLocaleString()}</h3>
        <label>本机只读地址<div className="copy-field"><input readOnly value={localShareUrl(matchingCreated.token)} onFocus={event => event.target.select()} /><Button variant="secondary" onClick={() => void copy()}><Copy size={16} />复制</Button></div></label>
        <p className="form-help">令牌只返回一次；此后只能查看元数据。再次下载请保留当前对话框，或创建新分享。</p>
        <div className="share-actions"><Button variant="secondary" onClick={() => onPreview(matchingCreated.token)}><Eye size={16} />只读预览</Button><Button onClick={() => void download()} disabled={Boolean(busy)}><Download size={16} />{busy === 'download' ? '等待选择保存位置' : '下载 .dtshare 分享包'}</Button></div>
      </section>}
      <section className="share-history"><h3>本机分享记录</h3>{shares.length ? shares.map(share => <div className="share-record" key={share.id}><div><strong>{share.title}</strong><p>{share.planId} · 快照 v{share.planVersion} · 创建于 {new Date(share.createdAt).toLocaleString()}</p><small>有效期至 {new Date(share.expiresAt).toLocaleString()}</small></div><Badge tone={share.status === 'active' ? 'green' : share.status === 'expired' ? 'amber' : 'red'}>{statusNames[share.status]}</Badge>{share.status !== 'revoked' && <Button variant="ghost" size="sm" onClick={() => setRevokeId(share.id)} disabled={Boolean(busy)}><Trash2 size={15} />撤销</Button>}</div>) : <p>{loading ? '正在读取真实分享记录…' : '还没有分享记录。'}</p>}</section>
      <p className="form-help">已导出的离线包不包含本机数据库引用，无法接收后续撤销。SHA-256 校验值只检测损坏或修改，不证明发送者身份。</p>
    </div>
    <ConfirmDialog open={revokeId !== null} onClose={() => setRevokeId(null)} onConfirm={() => { const id = revokeId; setRevokeId(null); if (id !== null) void revoke(id) }} title="撤销这个分享？" description="撤销后分享令牌立即失效，已导出的离线包不受影响；此操作不可恢复。" confirmLabel="确认撤销" busy={Boolean(busy)} />
  </Modal>
}

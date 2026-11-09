import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, ShieldCheck } from 'lucide-react'
import { Badge, Button, EmptyState, MiniBars, ProgressBar, SectionHeader } from './UI'
import { backend, backendErrorMessage, onBackendEvent, reportBackendError, type ProviderProfile } from '../backend/client'
import { usageBackend, type UsageOverview } from '../backend/usage'

export default function UsagePanel({ notify }: { notify: (message: string) => void }) {
  const [data, setData] = useState<UsageOverview | null>(null)
  const [profiles, setProfiles] = useState<ProviderProfile[]>([])
  const [error, setError] = useState('')
  const [rateProvider, setRateProvider] = useState('')
  const [rateModel, setRateModel] = useState('')
  const [inputRate, setInputRate] = useState('1')
  const [outputRate, setOutputRate] = useState('2')
  const [sessionUsd, setSessionUsd] = useState(1)
  const [dailyUsd, setDailyUsd] = useState(10)
  const [regenerationLimit, setRegenerationLimit] = useState(2)
  const [busy, setBusy] = useState(false)
  // 32#1：预算草稿不随事件刷新被覆盖——provider.chat_completed 等任意模型
  // 请求完成都会触发 refresh，正在编辑的输入框只有在持久值确实变化时才
  // 被替换（对齐 SecurityPanel 28#10 的草稿守卫）
  const previousSettings = useRef({ sessionUsd: 1, dailyUsd: 10, regenerationLimit: 2 })

  const refresh = useCallback(async () => {
    try {
      const [usage, providers] = await Promise.all([usageBackend.overview(), backend.listProviderProfiles()])
      setData(usage)
      setProfiles(providers)
      const previous = previousSettings.current
      setSessionUsd(current => (usage.settings.sessionUsd === previous.sessionUsd ? current : usage.settings.sessionUsd))
      setDailyUsd(current => (usage.settings.dailyUsd === previous.dailyUsd ? current : usage.settings.dailyUsd))
      setRegenerationLimit(current => (usage.settings.regenerationLimit === previous.regenerationLimit ? current : usage.settings.regenerationLimit))
      previousSettings.current = { sessionUsd: usage.settings.sessionUsd, dailyUsd: usage.settings.dailyUsd, regenerationLimit: usage.settings.regenerationLimit }
      setError('')
    } catch (cause) { setError(backendErrorMessage(cause)); reportBackendError(cause) }
  }, [])
  useEffect(() => {
    void refresh()
    let mounted = true
    let unlisten: (() => void) | undefined
    const listening = onBackendEvent(event => {
      // 32#1：过滤收窄到用量域与模型请求终态。35#1：`provider.chat_` 前缀会
      // 命中 chat_delta——流式输出每分块触发两次 IPC 全量刷新；改为精确终态
      const terminal = ['provider.chat_completed', 'provider.chat_failed', 'provider.chat_cancelled', 'provider.chat_interrupted', 'provider.route_completed', 'provider.route_failed', 'provider.route_cancelled', 'provider.route_interrupted']
      if (event.kind.startsWith('usage.') || terminal.includes(event.kind)) void refresh()
    })
    listening.then(stop => { if (mounted) unlisten = stop; else stop() }).catch(reportBackendError)
    // 28#5：卸载早于 listen() promise resolve 时立即注销，不再空转刷新
    return () => { mounted = false; unlisten?.(); void listening.then(stop => { if (!mounted) stop() }) }
  }, [refresh])
  useEffect(() => {
    if (!profiles.length) return
    const provider = profiles.find(profile => profile.id === rateProvider) ?? profiles[0]
    setRateProvider(provider.id)
    if (!provider.models.includes(rateModel)) setRateModel(provider.defaultModel || provider.models[0] || '')
  }, [profiles, rateProvider, rateModel])

  const saveRate = async () => {
    if (busy || !rateProvider || !rateModel) return
    setBusy(true)
    try {
      // 后端限制费率有限、非负且 ≤1,000,000：提交前按同一区间钳制
      const clampRate = (value: string) => Math.min(1_000_000, Math.max(0, Number(value) || 0))
      await usageBackend.upsertRate({ providerId: rateProvider, model: rateModel, inputPerMillion: clampRate(inputRate), outputPerMillion: clampRate(outputRate) })
      await refresh()
      notify('实际模型费率已保存，后续台账按该费率计算')
    } catch (error) { reportBackendError(error, 'write') } finally { setBusy(false) }
  }
  const saveBudget = async () => {
    if (busy) return
    setBusy(true)
    try {
      const clampBudget = (value: number) => Math.max(0, Number.isFinite(value) ? value : 0)
      const settings = await usageBackend.saveSettings({ sessionUsd: clampBudget(sessionUsd), dailyUsd: clampBudget(dailyUsd), regenerationLimit })
      setData(current => current ? { ...current, settings } : current)
      await refresh()
      notify('预算与方案重生成上限已保存，并用于后续原生请求')
    } catch (error) { reportBackendError(error, 'write') } finally { setBusy(false) }
  }
  // 43#2：写盘期间禁用导出按钮，防止连点叠出多个保存对话框
  const [exporting, setExporting] = useState(false)
  const exportCsv = async () => {
    if (exporting) return
    setExporting(true)
    try {
      // 37#5：与其他导出一致——原生保存对话框选位置，由后端落盘
      const destination = await backend.saveMaterialFile('DeepTutor-usage', 'csv')
      if (!destination) return
      const result = await usageBackend.exportCsvFile(destination)
      notify(`已导出真实用量 CSV（${result.bytes.toLocaleString()} 字节），不包含提示词、回答或密钥`)
    } catch (error) { reportBackendError(error) } finally { setExporting(false) }
  }
  const profile = profiles.find(item => item.id === rateProvider)
  const budgetPercent = data && data.settings.dailyUsd > 0 ? data.summary.todayCost / data.settings.dailyUsd * 100 : 0

  if (!data && error) return <div className="form-error" role="alert"><p>{error}</p><Button variant="secondary" size="sm" onClick={() => { setError(''); void refresh() }}>重新读取用量</Button></div>
  if (!data) return <p>正在读取真实用量台账…</p>
  return <div className="usage-page"><div className="usage-metrics"><div><span>今日已计价成本</span><strong>${data.summary.todayCost.toFixed(6)}</strong><small>每日预算使用 {budgetPercent.toFixed(2)}%</small></div><div><span>今日已上报输入 tokens</span><strong>{data.summary.inputTokens}</strong><small>{data.summary.missingUsage} 条记录未完整上报用量</small></div><div><span>今日已上报输出 tokens</span><strong>{data.summary.outputTokens}</strong><small>{data.summary.unpricedRequests} 条记录未计价</small></div><div><span>取消／异常已计价</span><strong>${(data.summary.cancelledCost + data.summary.failedCost).toFixed(6)}</strong><small>仅计算实际返回且已配置费率的用量</small></div></div>
    {data.summary.unpricedRequests > 0 && <p className="setting-note">有 {data.summary.unpricedRequests} 条记录因缺少完整用量或费率而未计价；预算只依据已记录费用，不估算未知成本。</p>}
    <div className="usage-grid"><section className="surface usage-trend"><SectionHeader title={`近 ${data.days.length} 日已计价成本`} description="按真实请求日期与本机时区聚合；无用量时不伪造趋势。" /><MiniBars values={data.days.map(day => day.cost)} labels={data.days.map(day => day.date)} /></section><section className="surface usage-by-model"><SectionHeader title="按真实模型明细" />{data.models.map(model => <div key={`${model.providerId}-${model.model}`}><div><strong>{model.model}</strong><span>${model.cost.toFixed(6)}</span></div><ProgressBar value={model.share} /><small>{model.providerName} · {model.tokens} tokens · {model.share.toFixed(1)}% 已上报用量{model.unpricedRequests ? ` · ${model.unpricedRequests} 条未计价` : ''}</small></div>)}{!data.models.length && <p>尚无真实模型用量。</p>}</section></div>
    <div className="usage-grid"><section className="surface"><SectionHeader title="实际模型费率" description="按每百万 tokens 计价（USD）；这里保存的是你配置的费率，不冒充供应商报价。" /><div className="form-stack"><label>供应商档案<select value={rateProvider} onChange={event => { setRateProvider(event.target.value); setRateModel('') }}><option value="" disabled>选择真实档案</option>{profiles.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>模型<select value={rateModel} onChange={event => setRateModel(event.target.value)}>{profile?.models.map(model => <option key={model} value={model}>{model}</option>)}</select></label><div className="form-grid"><label>输入 / 百万 tokens<input type="number" min="0" step="any" value={inputRate} onChange={event => setInputRate(event.target.value)} /></label><label>输出 / 百万 tokens<input type="number" min="0" step="any" value={outputRate} onChange={event => setOutputRate(event.target.value)} /></label></div><Button onClick={() => void saveRate()} disabled={busy || !rateProvider || !rateModel}>保存真实费率</Button></div>{data.rates.map(rate => <p className="side-copy" key={rate.id}>{rate.providerName} · {rate.model}：输入 {rate.inputPerMillion}，输出 {rate.outputPerMillion} / 百万 tokens</p>)}</section>
      <section className="surface budget-settings"><SectionHeader title="原生预算防线" description="达到已记录预算后不再发起新请求；已有结果仍可读取。" /><label>Agent 会话预算（USD）<input type="number" min="0" step="any" value={sessionUsd} onChange={event => setSessionUsd(Number(event.target.value))} /></label><label>每日预算（USD）<input type="number" min="0" step="any" value={dailyUsd} onChange={event => setDailyUsd(Number(event.target.value))} /></label><label>方案局部重生成上限<select value={regenerationLimit} onChange={event => setRegenerationLimit(Number(event.target.value))}><option value={1}>1 次</option><option value={2}>2 次</option><option value={3}>3 次</option></select></label><div className="budget-rule"><ShieldCheck size={18} /><p>未配置费率的成本无法计入金额预算；请在左侧配置实际费率。Agent 的 Token 预算仍然独立生效。预算只统计供应商实际上报的用量：不上报 usage 的兼容服务产生的真实计费不会被预算拦截，仅受 25 步工具循环上限约束。</p></div><Button onClick={() => void saveBudget()} disabled={busy}>保存原生预算</Button></section></div>
    <section className="surface usage-ledger"><SectionHeader title="真实用量台账" description="包括完成、取消、异常与中断状态；最多显示最近 200 条，聚合计量不截断。" action={<Button variant="secondary" size="sm" disabled={exporting} onClick={() => void exportCsv()}>{exporting ? '正在导出…' : <><Download size={15} />导出真实 CSV</>}</Button>} />{data.ledger.length ? <div className="table-wrap"><table><thead><tr><th scope="col">时间</th><th scope="col">供应商 / 任务</th><th scope="col">模型</th><th scope="col">输入 / 输出 tokens</th><th scope="col">成本（USD）</th><th scope="col">状态</th></tr></thead><tbody>{data.ledger.map(entry => <tr key={entry.requestId}><td>{new Date(entry.createdAt).toLocaleString()}</td><td>{entry.providerName} / {entry.task}</td><td>{entry.model}</td><td>{entry.inputTokens ?? '未知'} / {entry.outputTokens ?? '未知'}</td><td>{entry.cost === null ? '未计价' : `$${entry.cost.toFixed(8)}`}</td><td><Badge tone={['cancelled', 'failed', 'interrupted'].includes(entry.status) ? 'amber' : 'green'}>{{ done: '完成', cancelled: '取消', failed: '异常', interrupted: '中断', running: '进行中' }[entry.status] ?? entry.status}</Badge></td></tr>)}</tbody></table></div> : <EmptyState title="尚无真实用量" description="模型请求完成后，这里会显示供应商实际返回的用量与可计价成本。" />}</section>
  </div>
}

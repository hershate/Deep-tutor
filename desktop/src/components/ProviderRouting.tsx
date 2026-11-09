import { useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, Check, Network, Route, ShieldCheck, X, Zap } from 'lucide-react'
import { Badge, Button, IconButton, SectionHeader } from './UI'
import type { CircuitSettings, CircuitSnapshot, ProviderProfile, ProviderRouteLog, RoutingSettings, TaskRouteId } from '../backend/client'

export const taskRoutes: { id: TaskRouteId; name: string; hint: string }[] = [
  { id: 'agent', name: 'Agent 对话', hint: '支持当前学习上下文' },
  { id: 'plan', name: '方案生成', hint: '生成与校验学习方案' },
  { id: 'lecture', name: '自学讲义', hint: '基于资料生成逐单元讲义' },
  { id: 'practice', name: '测验与判分', hint: '需要稳定结构化输出' },
  { id: 'wiki', name: 'Wiki 引用问答', hint: '依据引用范围回答' },
  { id: 'resources', name: '资源检索', hint: '检索与筛选学习资源' },
  { id: 'embedding', name: '向量嵌入', hint: '本地知识库索引与召回' },
]
export const emptyRouting: RoutingSettings = { mode: 'direct', routes: {}, aggregateProviderIds: [], aggregateDefault: null, failover: [], circuit: { failures: 3, cooldown: 60, timeout: 45, retries: 2 } }
type Save = (settings: RoutingSettings) => Promise<boolean>
type CommonProps = { settings: RoutingSettings; profiles: ProviderProfile[]; onSave: Save; saving: boolean }

export function AggregateModels({ settings, profiles, onSave, saving }: CommonProps) {
  const enabled = profiles.filter(profile => profile.enabled && settings.aggregateProviderIds.includes(profile.id))
  const models = enabled.flatMap(profile => profile.models.map(model => ({ profile, model })))
  return <section className="aggregate-models"><div><Network size={20} /><h2>统一模型列表</h2><Badge tone="green">{models.length} 个上游模型</Badge></div><p>请求按模型名选择实际供应商；未指定时使用聚合默认项。聚合模式不自动故障转移。</p><div>{models.map(({ profile, model }) => <button key={`${profile.id}-${model}`} disabled={saving} onClick={() => void onSave({ ...settings, routes: { ...settings.routes, agent: { providerId: profile.id, model } } })}><strong>{model}</strong><span>{profile.name}</span></button>)}</div>{!models.length && <p>请在供应商卡片中至少加入一个已启用的档案。</p>}</section>
}

export default function ProviderRouting({ settings, profiles, activeId, onSave, saving }: CommonProps & { activeId: string | null }) {
  const [saved, setSaved] = useState(false)
  const enabled = profiles.filter(profile => profile.enabled)
  const save = async (next: RoutingSettings) => { if (await onSave(next)) setSaved(true) }
  const assignAll = (providerId: string | null) => {
    const routes = Object.fromEntries(taskRoutes.map(task => [task.id, { providerId, model: null }]))
    void save({ ...settings, routes })
  }
  const agent = settings.routes.agent
  const agentProvider = profiles.find(profile => profile.id === agent?.providerId) ?? profiles.find(profile => profile.id === activeId)
  const aggregateProvider = agent?.model ? profiles.find(profile => settings.aggregateProviderIds.includes(profile.id) && profile.models.includes(agent.model ?? '')) : profiles.find(profile => profile.id === settings.aggregateDefault)
  const previewProvider = settings.mode === 'aggregate' ? aggregateProvider : agentProvider
  const strategies: [string, string | null][] = [
    ['均衡', activeId ?? enabled[0]?.id ?? null],
    ['质量优先', enabled.find(profile => profile.kind === '官方')?.id ?? activeId],
    ['成本优先', enabled.find(profile => profile.kind === '本地模型')?.id ?? activeId],
    ['隐私优先', enabled.find(profile => profile.kind === '本地模型')?.id ?? null],
  ]
  return <div className="model-routing-layout"><main className="surface task-routing-panel"><SectionHeader title="按学习任务分配模型" description="配置直接写入原生后端。默认跟随当前供应商；新会话与后续请求使用已保存规则。" action={<Badge tone="green">{saving ? '正在保存' : saved ? '已保存到后端' : '原生配置'}</Badge>} /><div className="routing-strategy"><span>快速策略</span>{strategies.map(([label, id]) => <button key={label} disabled={!id || saving} title={label === '质量优先' ? '仅快捷选择官方档案，不代表质量测评结果' : label === '成本优先' ? '仅快捷选择本地档案，不代表实际价格' : label === '隐私优先' ? '优先本地档案，仍需核查服务的数据边界' : '跟随当前供应商'} onClick={() => assignAll(id)}>{label}</button>)}</div><div className="task-route-list">{taskRoutes.map((task, index) => {
    const route = settings.routes[task.id] ?? { providerId: null, model: null }
    const provider = profiles.find(profile => profile.id === route.providerId && profile.enabled)
    return <div className="task-route-row" key={task.id}><span className="task-route-index">0{index + 1}</span><div className="task-route-name"><strong>{task.name}</strong><span>{task.hint}</span></div><label>供应商<select aria-label={`${task.name}供应商`} value={route.providerId ?? 'default'} disabled={saving} onChange={event => void save({ ...settings, routes: { ...settings.routes, [task.id]: { providerId: event.target.value === 'default' ? null : event.target.value, model: null } } })}><option value="default">跟随当前供应商</option>{enabled.map(profile => <option value={profile.id} key={profile.id}>{profile.name}</option>)}</select></label><label>模型<select aria-label={`${task.name}模型`} value={route.model ?? ''} disabled={!provider || saving} onChange={event => void save({ ...settings, routes: { ...settings.routes, [task.id]: { ...route, model: event.target.value || null } } })}><option value="">{provider ? `默认 · ${provider.defaultModel}` : '使用供应商默认模型'}</option>{provider?.models.map(model => <option value={model} key={model}>{model}</option>)}</select></label></div>
  })}</div><div className="routing-footer"><span><Check size={15} />配置只影响后续请求，不改写历史会话与已完成内容。</span><Button disabled={saving} onClick={() => void save(settings)}>保存并应用任务路由</Button></div></main><aside className="routing-side"><section className="surface"><SectionHeader title="当前调用预览" /><div className="call-preview"><span>Agent 对话</span><strong>{previewProvider?.name ?? '尚未配置供应商'}</strong><code>{agent?.model ?? previewProvider?.defaultModel ?? '未选择模型'}</code><small>{settings.mode === 'aggregate' ? '按聚合模型选择上游，不自动故障转移' : settings.mode === 'routing' ? '由原生任务路由选择供应商' : '使用当前供应商直连'}</small></div></section><section className="surface routing-note"><Route size={19} /><h3>真实调用边界</h3><p>模型覆盖会校验所选档案的模型列表，并记录实际上游供应商与模型。质量与成本快捷策略只是配置模板，不代表测评或价格结论。</p></section></aside></div>
}

export function ProviderRouteLogs({ logs, settings, profiles, circuits, onSave, saving }: { logs: ProviderRouteLog[]; settings: RoutingSettings; profiles: ProviderProfile[]; circuits: CircuitSnapshot[]; onSave: Save; saving: boolean }) {
  const latest = useMemo(() => logs.slice(0, 20), [logs])
  const enabled = profiles.filter(profile => profile.enabled)
  const move = (index: number, offset: number) => {
    const target = index + offset
    if (target < 0 || target >= settings.failover.length) return
    const failover = [...settings.failover]
    ;[failover[index], failover[target]] = [failover[target], failover[index]]
    void onSave({ ...settings, failover })
  }
  const updateCircuit = (key: keyof CircuitSettings, value: number) => {
    if (Number.isFinite(value)) void onSave({ ...settings, circuit: { ...settings.circuit, [key]: value } })
  }
  // 48#5：blur 即钳制到后端合法区间——清空/手输越界不再触发整单 invalid_circuit_settings
  const clampCircuit = (value: number, min: number, max: number, fallback: number) => Number.isFinite(value) ? Math.min(max, Math.max(min, Math.round(value))) : fallback
  return <div className="failover-layout"><main>
    <section className="surface failover-queue"><SectionHeader title="自动故障转移队列" description="主上游未产生文本的可重试失败，按此顺序尝试备份。取消与部分输出不会切换。" action={<Badge tone={settings.mode === 'routing' ? 'green' : 'amber'}>{settings.mode === 'routing' ? '路由已启用' : '仅保存配置'}</Badge>} />
      <div className="failover-list">{settings.failover.map((id, index) => {
        const profile = profiles.find(item => item.id === id)
        const circuit = circuits.find(item => item.providerId === id)
        return <div key={id}><span className="queue-position">{index + 1}</span><div><strong>{profile?.name ?? id}</strong><code>{profile?.defaultModel ?? '未知模型'}</code><small>{circuit ? `${circuit.state === 'open' ? '熔断冷却' : circuit.state === 'half_open' ? '半开探测' : '闭合'} · 连续失败 ${circuit.failures}` : '尚无失败记录'}</small></div><Badge tone={circuit?.state === 'open' ? 'amber' : circuit?.state === 'half_open' ? 'blue' : 'green'}>{circuit ? circuit.state === 'open' ? '熔断冷却' : circuit.state === 'half_open' ? '半开探测' : '闭合' : '闭合'}</Badge><div><IconButton label={`上移 ${profile?.name ?? id}`} disabled={index === 0 || saving} onClick={() => move(index, -1)}><ArrowUp size={16} /></IconButton><IconButton label={`下移 ${profile?.name ?? id}`} disabled={index === settings.failover.length - 1 || saving} onClick={() => move(index, 1)}><ArrowDown size={16} /></IconButton><IconButton label={`移出队列 ${profile?.name ?? id}`} disabled={saving} onClick={() => void onSave({ ...settings, failover: settings.failover.filter(item => item !== id) })}><X size={16} /></IconButton></div></div>
      })}</div>
      {!settings.failover.length && <div className="provider-empty"><Network size={24} /><p>尚未配置备份供应商。主上游失败时不会自动切换。</p></div>}
      <div className="queue-add"><select aria-label="加入故障转移队列" value="" disabled={saving || !enabled.some(profile => !settings.failover.includes(profile.id))} onChange={event => { if (event.target.value) void onSave({ ...settings, failover: [...settings.failover, event.target.value] }) }}><option value="">选择要加入队列的供应商…</option>{enabled.filter(profile => !settings.failover.includes(profile.id)).map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select><Button disabled={saving || !enabled.length} onClick={() => void onSave({ ...settings, mode: 'routing' })}>保存并启用路由</Button></div>
    </section>
    <section className="surface route-request-log"><SectionHeader title="请求路由日志" description="持久化的逻辑请求与每次真实上游调用；不包含 API Key 或提示词。" />{latest.length ? latest.map(log => <article className="route-log-entry" key={log.requestId}><code>{log.requestId}</code><span>{taskRoutes.find(task => task.id === log.task)?.name ?? log.task} · {log.mode}</span><strong>{profiles.find(profile => profile.id === log.providerId)?.name ?? log.providerId ?? '未选择'} · {log.model ?? '未知模型'}</strong><Badge tone={log.status === 'done' ? 'green' : log.status === 'failed' ? 'red' : 'amber'}>{log.status}</Badge>{log.error && <p>{log.error.code}：{log.error.message}</p>}<div className="route-attempt-list">{log.attempts.map(attempt => <div className="route-attempt" key={attempt.attempt}><span>#{attempt.attempt} {attempt.status}</span><strong>{profiles.find(profile => profile.id === attempt.providerId)?.name ?? attempt.providerId} · {attempt.model}</strong><code>{attempt.upstreamRequestId ?? '未发起上游请求'}</code><small>{attempt.latencyMs === null ? '无上游计时' : `${attempt.latencyMs} ms`}{attempt.error ? ` · ${attempt.error.code}：${attempt.error.message}` : ''}</small></div>)}</div></article>) : <p>尚无原生路由请求。发送一次真实模型请求后，这里会显示实际结果。</p>}</section>
  </main><aside className="surface circuit-settings"><SectionHeader title="熔断与重试" description="保存后直接作用于后续路由请求；取消不计为上游失败。" />
    <label>连续失败阈值<input key={`failures-${settings.circuit.failures}`} type="number" min={1} max={10} defaultValue={settings.circuit.failures} disabled={saving} onBlur={event => { const clamped = clampCircuit(Number(event.target.value), 1, 10, settings.circuit.failures); if (clamped !== settings.circuit.failures) updateCircuit('failures', clamped) }} /></label>
    <label>熔断冷却（秒）<input key={`cooldown-${settings.circuit.cooldown}`} type="number" min={10} max={600} defaultValue={settings.circuit.cooldown} disabled={saving} onBlur={event => { const clamped = clampCircuit(Number(event.target.value), 10, 600, settings.circuit.cooldown); if (clamped !== settings.circuit.cooldown) updateCircuit('cooldown', clamped) }} /></label>
    <label>请求超时（秒）<input key={`timeout-${settings.circuit.timeout}`} type="number" min={5} max={300} defaultValue={settings.circuit.timeout} disabled={saving} onBlur={event => { const clamped = clampCircuit(Number(event.target.value), 5, 300, settings.circuit.timeout); if (clamped !== settings.circuit.timeout) updateCircuit('timeout', clamped) }} /></label>
    <label>单上游重试次数<select value={settings.circuit.retries} disabled={saving} onChange={event => updateCircuit('retries', Number(event.target.value))}>{[0, 1, 2, 3].map(value => <option key={value} value={value}>{value} 次</option>)}</select></label>
    <div className="circuit-formula"><Zap size={17} /><p>连续 {settings.circuit.failures} 次失败 → 熔断 {settings.circuit.cooldown} 秒 → 半开探测 → 恢复或继续冷却。</p></div>
    <div className="security-note"><ShieldCheck size={16} />仅路由模式使用故障转移队列；直连与聚合不进行切换。冷却期与半开并行探测由后端持久化控制。</div>
  </aside></div>
}

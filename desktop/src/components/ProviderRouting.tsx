import { useMemo, useState } from 'react'
import { Check, Network, Route, ShieldCheck } from 'lucide-react'
import { Badge, Button, SectionHeader } from './UI'
import type { ProviderProfile, ProviderRouteLog, RoutingSettings, TaskRouteId } from '../backend/client'

export const taskRoutes: { id: TaskRouteId; name: string; hint: string }[] = [
  { id: 'agent', name: 'Agent 对话', hint: '支持当前学习上下文' },
  { id: 'plan', name: '方案生成', hint: '生成与校验学习方案' },
  { id: 'lecture', name: '自学讲义', hint: '基于资料生成逐单元讲义' },
  { id: 'practice', name: '测验与判分', hint: '需要稳定结构化输出' },
  { id: 'wiki', name: 'Wiki 引用问答', hint: '依据引用范围回答' },
  { id: 'resources', name: '资源检索', hint: '检索与筛选学习资源' },
  { id: 'embedding', name: '向量嵌入', hint: '本地知识库索引与召回' },
]
export const emptyRouting: RoutingSettings = { mode: 'direct', routes: {}, aggregateProviderIds: [], aggregateDefault: null }
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

export function ProviderRouteLogs({ logs, settings, profiles }: { logs: ProviderRouteLog[]; settings: RoutingSettings; profiles: ProviderProfile[] }) {
  const latest = useMemo(() => logs.slice(0, 20), [logs])
  return <div className="failover-layout"><main><section className="surface failover-queue"><SectionHeader title="自动故障转移队列" description="故障转移与熔断将在下一切片接入；当前不会伪造队列健康状态或模拟切换。" action={<Badge tone="amber">尚未启用</Badge>} /><div className="provider-empty"><Network size={25} /><h2>故障转移尚未接入</h2><p>当前{settings.mode === 'aggregate' ? '聚合按模型选择上游，不自动切换。' : settings.mode === 'routing' ? '任务路由按保存的分配调用上游。' : '请求直连当前供应商。'}真实请求结果仍保存在下方日志中。</p></div></section><section className="surface route-request-log"><SectionHeader title="请求路由日志" description="来自原生 route_runs；不包含 API Key 或提示词。" />{latest.length ? latest.map(log => <div className="route-log-entry" key={log.requestId}><code>{log.requestId}</code><span>{taskRoutes.find(task => task.id === log.task)?.name ?? log.task} · {log.mode}</span><strong>{profiles.find(profile => profile.id === log.providerId)?.name ?? log.providerId ?? '未选择'} · {log.model ?? '未知模型'}</strong><Badge tone={log.status === 'done' ? 'green' : log.status === 'failed' ? 'red' : 'amber'}>{log.status}</Badge>{log.error && <p>{log.error.code}：{log.error.message}</p>}</div>) : <p>尚无原生路由请求。发送一次真实模型请求后，这里会显示实际结果。</p>}</section></main><aside className="surface circuit-settings"><SectionHeader title="熔断与重试" description="连续失败阈值、冷却、超时与单上游重试尚未启用。" /><div className="security-note"><ShieldCheck size={17} />当前仅记录原生路由与实际请求，不提供模拟故障转移或虚假熔断状态。</div></aside></div>
}

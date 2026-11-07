import { useCallback, useEffect, useState } from 'react'
import { Check, CircleAlert, LoaderCircle, Square } from 'lucide-react'
import { Badge, Button, EmptyState, ProgressBar } from './UI'
import { backend, backendErrorMessage, onBackendEvent } from '../backend/client'

type TaskEntry = {
  id: string
  title: string
  detail: string
  status: string
  progress: number
  updatedAt: string
  cancel?: () => Promise<unknown>
}
const labels: Record<string, string> = { running: '运行中', cancelling: '正在取消', done: '已完成', degraded: '部分完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' }

export default function AgentTaskCenter({ notify }: { notify: (message: string) => void }) {
  const [tasks, setTasks] = useState<TaskEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const refresh = useCallback(async () => {
    try {
      const [plans, sessions] = await Promise.all([backend.listLearningPlans(), backend.listAgentSessions()])
      const lectures = (await Promise.all(plans.filter(plan => plan.adoptedProjectId !== null).flatMap(plan => plan.result?.stages.map(async stage => ({ plan, stage, lecture: await backend.getLecture(plan.id, stage.id) })) ?? []))).filter(item => item.lecture.version > 0)
      const entries: TaskEntry[] = [
        ...plans.map(plan => ({ id: `plan-${plan.id}`, title: plan.goal, detail: `方案生成 · 第 ${plan.currentStep} / 8 步${plan.error ? ` · ${plan.error.message}` : ''}`, status: plan.status, progress: plan.progress, updatedAt: plan.updatedAt, cancel: plan.status === 'running' ? () => backend.cancelLearningPlan(plan.id) : undefined })),
        ...sessions.flatMap(session => session.runs[0] ? [{ id: `agent-${session.runs[0].id}`, title: session.title, detail: `Agent · 第 ${session.runs[0].steps} / 25 步 · 输出 ${session.runs[0].outputTokens} tokens${session.runs[0].error ? ` · ${session.runs[0].error.message}` : ''}`, status: session.runs[0].status, progress: session.runs[0].status === 'done' ? 100 : session.runs[0].steps / 25 * 100, updatedAt: session.runs[0].updatedAt, cancel: session.runs[0].status === 'running' ? () => backend.cancelAgentRun(String(session.runs[0].id)) : undefined }] : []),
        ...lectures.map(({ plan, stage, lecture }) => ({ id: `lecture-${plan.id}-${stage.id}`, title: `${plan.goal} · ${stage.title}`, detail: `讲义 v${lecture.version} · ${lecture.completedUnits} / ${lecture.totalUnits} 单元${lecture.error ? ` · ${lecture.error.message}` : ''}`, status: lecture.status, progress: lecture.totalUnits ? lecture.completedUnits / lecture.totalUnits * 100 : 0, updatedAt: lecture.updatedAt ?? plan.updatedAt, cancel: lecture.status === 'running' ? () => backend.cancelLecture(plan.id, stage.id) : undefined })),
      ]
      entries.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
      setTasks(entries.slice(0, 40))
      setError('')
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setLoading(false) }
  }, [])
  useEffect(() => {
    void refresh()
    let unlisten: (() => void) | undefined
    let disposed = false
    // 方案事件前缀是 plan.*（无 learning.* 事件）；事件密集时合并为一次刷新，避免逐步扇出
    let timer: number | undefined
    void onBackendEvent(event => {
      if (['plan.', 'agent.', 'lecture.'].some(prefix => event.kind.startsWith(prefix)) && ['started', 'completed', 'failed', 'cancelled', 'interrupted', 'degraded'].some(status => event.kind.endsWith(status))) {
        if (timer !== undefined) return
        timer = window.setTimeout(() => { timer = undefined; void refresh() }, 300)
      }
    }).then(remove => { if (disposed) remove(); else unlisten = remove })
    return () => { disposed = true; unlisten?.(); if (timer !== undefined) window.clearTimeout(timer) }
  }, [refresh])
  const cancel = async (task: TaskEntry) => {
    if (!task.cancel) return
    setBusy(task.id)
    try { await task.cancel(); notify('取消请求已提交，最终状态由原生后端保存'); await refresh() }
    catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  if (loading && !error) return <div className="page-loading"><LoaderCircle size={22} className="spin" />正在读取真实运行记录…</div>
  if (error && !tasks.length) return <div className="page-loading" role="alert"><CircleAlert size={25} /><h2>运行记录读取失败</h2><p>{error}</p><Button onClick={() => { setError(''); setLoading(true); void refresh() }}>重新读取</Button></div>
  return <>
    {error && <div className="form-error" role="alert">{error}</div>}
    {!tasks.length ? <EmptyState title="尚无运行记录" description="方案生成、讲义生成和 Agent 运行后，会在这里显示真实状态。" /> : <div className="agent-task-list">{tasks.map(task => <div key={task.id}><span className={`task-status ${task.status === 'running' ? 'running' : ''}`}>{task.status === 'running' || task.status === 'cancelling' ? <LoaderCircle size={16} className="spin" /> : task.status === 'done' ? <Check size={16} /> : <CircleAlert size={16} />}</span><div><strong>{task.title}</strong><p>{task.detail}</p><ProgressBar value={task.progress} /><small>{new Date(task.updatedAt).toLocaleString('zh-CN')}</small></div><Badge tone={task.status === 'done' ? 'green' : task.status === 'failed' ? 'red' : task.status === 'running' ? 'blue' : 'amber'}>{labels[task.status] ?? task.status}</Badge>{task.cancel && <Button variant="ghost" size="sm" aria-label={`取消 ${task.title}`} disabled={busy === task.id} onClick={() => void cancel(task)}><Square size={13} />取消</Button>}</div>)}</div>}
    <div className="setting-note"><Check size={16} />这里只展示原生后端持久化的方案、讲义和 Agent 运行，不生成演示任务。</div>
  </>
}

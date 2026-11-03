import { useEffect, useMemo, useState } from 'react'
import { ArrowRight, CalendarDays, Check, CheckCircle2, ChevronRight, Circle, FileUp, FolderKanban, GitBranch, Lightbulb, Plus, Rocket, Sparkles, Trophy } from 'lucide-react'
import type { Project } from '../data'
import { Badge, Button, EmptyState, Modal, ProgressBar, SectionHeader } from '../components/UI'
import { backend, backendErrorMessage, onBackendEvent, reportBackendError } from '../backend/client'
import type { AppSnapshot, LearningPlan, Material } from '../backend/client'
import { projectsBackend } from '../backend/projects'
import type { ProjectVersion } from '../backend/projects'

const statusNames: Record<string, string> = { running: '正在评价', cancelling: '正在取消', done: '已完成', failed: '评价失败', cancelled: '已取消', interrupted: '已中断' }
function deadline() {
  const date = new Date()
  date.setDate(date.getDate() + 30)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
function withProgress(project: Project): Project {
  return { ...project, progress: project.milestones.length ? Math.round(project.milestones.filter(item => item.done).length / project.milestones.length * 100) : 0 }
}

export default function Projects({ projects, setProjects, onNavigateMap, notify }: { projects: Project[]; setProjects: (projects: Project[]) => Promise<AppSnapshot>; onNavigateMap: (planId: string) => void; notify: (message: string) => void }) {
  const [selected, setSelected] = useState(projects[0]?.id || 0)
  const [versions, setVersions] = useState<ProjectVersion[]>([])
  const [plans, setPlans] = useState<LearningPlan[]>([])
  const [materials, setMaterials] = useState<Material[]>([])
  const [selectedVersionId, setSelectedVersionId] = useState<number | null>(null)
  const [liveOutput, setLiveOutput] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [submitOpen, setSubmitOpen] = useState(false)
  const [milestoneOpen, setMilestoneOpen] = useState(false)
  const [noteOpen, setNoteOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [due, setDue] = useState(deadline)
  const [tag, setTag] = useState('个人项目')
  const [planId, setPlanId] = useState('')
  const [milestoneText, setMilestoneText] = useState('定义项目成果与评价标准\n完成第一个可验证版本\n撰写项目复盘')
  const [milestoneTitle, setMilestoneTitle] = useState('')
  const [submission, setSubmission] = useState('')
  const [submissionMilestone, setSubmissionMilestone] = useState('')
  const [materialId, setMaterialId] = useState('')
  const [note, setNote] = useState('')
  const project = projects.find(item => item.id === selected) || projects[0]
  const projectVersions = useMemo(() => versions.filter(item => item.projectId === project?.id), [versions, project?.id])
  const currentVersion = projectVersions.find(item => item.id === selectedVersionId) || projectVersions[0]
  const running = projectVersions.find(item => ['running', 'cancelling'].includes(item.status))
  const feedback = currentVersion?.feedback
  const linkedPlan = plans.find(item => item.id === project?.planId)
  const completedMilestones = projects.reduce((total, item) => total + item.milestones.filter(milestone => milestone.done).length, 0)
  const evaluatedVersions = versions.filter(item => item.status === 'done').length

  useEffect(() => {
    let active = true
    let stop: (() => void) | undefined
    const load = async () => {
      try {
        const [workspace, allPlans, allMaterials] = await Promise.all([projectsBackend.workspace(), backend.listLearningPlans(), backend.listMaterials()])
        if (!active) return
        setVersions(workspace.versions)
        setPlans(allPlans.filter(item => item.adoptedProjectId !== null && item.result))
        setMaterials(allMaterials.filter(item => ['parsed', 'degraded'].includes(item.status) && item.textLength > 0))
        setError('')
      } catch (cause) { if (active) setError(backendErrorMessage(cause)) }
    }
    void load()
    void onBackendEvent(event => {
      if (active && (event.kind.startsWith('project.') || event.kind === 'security.session_started' || (event.kind === 'state.changed' && event.payload.namespace === 'projects'))) void load()
      const requestId = running?.requestId
      if (active && requestId && event.kind === 'provider.chat_delta' && event.payload.requestId === requestId) {
        void backend.getProviderChat(requestId).then(request => { if (active) setLiveOutput(request.output) }).catch(reportBackendError)
      }
    }).then(unlisten => { if (active) stop = unlisten; else unlisten() }).catch(reportBackendError)
    return () => { active = false; stop?.() }
  }, [running?.requestId])
  useEffect(() => { setSelectedVersionId(null); setLiveOutput('') }, [project?.id])
  useEffect(() => {
    const requestId = running?.requestId
    setLiveOutput('')
    if (requestId) void backend.getProviderChat(requestId).then(request => setLiveOutput(request.output)).catch(reportBackendError)
  }, [running?.requestId])

  const toggleMilestone = (id: number) => {
    if (!project) return
    const next = projects.map(item => item.id === project.id ? withProgress({ ...item, milestones: item.milestones.map(milestone => milestone.id === id ? { ...milestone, done: !milestone.done } : milestone) }) : item)
    void setProjects(next).catch(reportBackendError)
  }
  const createProject = async () => {
    const milestones = milestoneText.split('\n').map(item => item.trim()).filter(Boolean).map((title, index) => ({ id: index + 1, title, done: false }))
    if (!title.trim() || !tag.trim() || !due || !milestones.length || busy) return
    setBusy('create')
    try {
      const item: Project = { id: Date.now(), title: title.trim(), description: description.trim(), due, progress: 0, tag: tag.trim(), planId: planId || null, milestones }
      await setProjects([...projects, item])
      setSelected(item.id)
      setTitle(''); setDescription(''); setTag('个人项目'); setPlanId(''); setCreateOpen(false)
      notify(`项目及 ${milestones.length} 个里程碑已保存`)
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const addMilestone = async () => {
    if (!project || !milestoneTitle.trim() || busy) return
    setBusy('milestone')
    try {
      await setProjects(projects.map(item => item.id === project.id ? withProgress({ ...item, milestones: [...item.milestones, { id: Date.now(), title: milestoneTitle.trim(), done: false }] }) : item))
      setMilestoneTitle(''); setMilestoneOpen(false)
      notify('里程碑已保存')
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const submit = async () => {
    if (!project || !submission.trim() || busy) return
    setBusy('submit'); setError('')
    try {
      const result = await projectsBackend.submit({ projectId: project.id, content: submission, milestoneId: submissionMilestone ? Number(submissionMilestone) : null, materialId: materialId ? Number(materialId) : null })
      setVersions(result.versions)
      setSelectedVersionId(result.versions.find(item => item.projectId === project.id)?.id ?? null)
      setSubmission(''); setSubmitOpen(false)
      notify('真实成果已保存，模型评价任务已开始')
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const cancel = async () => {
    if (!running || busy) return
    setBusy('cancel')
    try {
      setVersions((await projectsBackend.cancel(running.id)).versions)
      notify('已请求取消，最终状态由原生后端保存')
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const resume = async () => {
    if (!currentVersion || busy) return
    setBusy('resume')
    try {
      setVersions((await projectsBackend.resume(currentVersion.id)).versions)
      notify('已从原提交恢复真实评价，不重复已完成请求')
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const saveNote = async () => {
    if (!currentVersion || !note.trim() || busy) return
    setBusy('note')
    try {
      await projectsBackend.saveNote(currentVersion.id, note)
      setNote(''); setNoteOpen(false)
      notify('迭代笔记已保存到真实笔记库')
    } catch (cause) { setError(backendErrorMessage(cause)) } finally { setBusy('') }
  }
  const score = (value: number | undefined) => value === undefined ? '未评价' : `${value}%`

  return <div className="page projects-page">
    <div className="page-heading"><div><h1>项目与作品</h1><p>把“我学过”变成“我能做出”，留下可验证的能力证据。</p></div><Button onClick={() => setCreateOpen(true)}><Plus size={18} />新建项目</Button></div>
    {error && <div className="form-error" role="alert">{error}<Button variant="ghost" size="sm" onClick={() => setError('')}>关闭</Button></div>}
    <div className="project-stats"><div><FolderKanban size={21} /><strong>{projects.filter(item => item.progress < 100).length}</strong><span>进行中项目</span></div><div><CheckCircle2 size={21} /><strong>{completedMilestones}</strong><span>已完成里程碑</span></div><div><Trophy size={21} /><strong>{evaluatedVersions}</strong><span>已评价成果</span></div><p><Sparkles size={17} />量规只评估真实提交内容，不代替你完成作品或声称运行代码。</p></div>
    {project ? <div className="projects-layout">
      <aside className="project-list"><div className="project-list-head"><span>我的项目</span><Badge tone="neutral">{projects.length}</Badge></div>{projects.map(item => <button key={item.id} className={project.id === item.id ? 'active' : ''} onClick={() => setSelected(item.id)}><div><Badge tone="neutral">{item.tag}</Badge><span>{item.due}</span></div><h2>{item.title}</h2><p>{item.description || '尚未填写项目说明。'}</p><ProgressBar value={item.progress} /><small>{item.progress}% · {item.milestones.filter(milestone => milestone.done).length}/{item.milestones.length} 个里程碑</small></button>)}</aside>
      <main className="project-detail">
        <div className="project-cover"><div className="cover-grid" aria-hidden="true" /><div><Badge tone="green">{project.tag} · 实践项目</Badge><h2>{project.title}</h2><p>{project.description || '尚未填写项目说明。'}</p></div><div className="project-cover-progress"><strong>{project.progress}%</strong><span>里程碑进度</span></div></div>
        <div className="project-meta"><span><CalendarDays size={17} />截止 {project.due}</span><span><GitBranch size={17} />{currentVersion ? `版本 v${currentVersion.versionNumber} · 第 ${currentVersion.attempt} 次评价` : '尚未提交版本'}</span><Button variant="secondary" size="sm" disabled={Boolean(running) || busy === 'submit'} onClick={() => { setSubmissionMilestone(''); setMaterialId(''); setSubmitOpen(true) }}><FileUp size={16} />提交阶段成果</Button></div>
        {running && <section className="surface"><SectionHeader title="实时原生评价" description={`版本 v${running.versionNumber} · ${statusNames[running.status]} · 请求 ${running.requestId}`} action={<Button variant="secondary" size="sm" disabled={running.status === 'cancelling' || busy === 'cancel'} onClick={() => void cancel()}>请求取消</Button>} /><pre className="streaming-output">{liveOutput || '等待模型返回真实评价…'}</pre></section>}
        <div className="project-detail-grid"><section className="surface milestone-checklist"><SectionHeader title="项目里程碑" description="每完成一步，项目进度按真实勾选重新计算。" /><div>{project.milestones.map(milestone => <button key={milestone.id} onClick={() => toggleMilestone(milestone.id)} className={milestone.done ? 'done' : ''}><span>{milestone.done ? <Check size={16} /> : <Circle size={16} />}</span><strong>{milestone.title}</strong><ChevronRight size={16} /></button>)}</div><button className="text-action" onClick={() => setMilestoneOpen(true)}>＋ 添加里程碑</button></section><aside className="surface project-rubric"><SectionHeader title="评价量规" description={currentVersion ? `来自版本 v${currentVersion.versionNumber} 的真实模型评价` : '提交成果后才会生成评价'} /><div><span>概念应用</span><strong>{score(feedback?.concept)}</strong><ProgressBar value={feedback?.concept ?? 0} /></div><div><span>实现质量</span><strong>{score(feedback?.implementation)}</strong><ProgressBar value={feedback?.implementation ?? 0} /></div><div><span>验证与反思</span><strong>{score(feedback?.verification)}</strong><ProgressBar value={feedback?.verification ?? 0} tone="amber" /></div><p><Lightbulb size={16} />{feedback?.suggestions[0] || (currentVersion?.error?.message ?? '暂无真实改进建议，不显示预设结论。')}</p></aside></div>
        <section className="surface project-feedback"><div><span className="agent-mark"><Sparkles size={18} /></span><div><h3>知舟的项目反馈</h3><p>{currentVersion ? `版本 v${currentVersion.versionNumber} · ${statusNames[currentVersion.status]} · ${currentVersion.providerId}` : '尚未提交阶段成果'}</p></div><Badge tone={feedback ? 'green' : 'neutral'}>{feedback ? `${feedback.suggestions.length} 条建议` : '无预设评价'}</Badge></div>{feedback ? <><blockquote>{feedback.summary}</blockquote><ul>{feedback.suggestions.map(suggestion => <li key={suggestion}>{suggestion}</li>)}</ul></> : <p>{currentVersion?.error?.message ?? '提交真实成果后，后端会保存版本并返回模型评价；失败不会生成替代反馈。'}</p>}<div><Button variant="secondary" disabled={!linkedPlan} onClick={() => { if (project.planId) onNavigateMap(project.planId) }}>学习相关概念<ArrowRight size={16} /></Button><Button variant="ghost" disabled={!feedback} onClick={() => { setNote(feedback?.suggestions.join('\n') || ''); setNoteOpen(true) }}>记录迭代想法</Button>{currentVersion && ['failed', 'cancelled', 'interrupted'].includes(currentVersion.status) && <Button variant="secondary" disabled={busy === 'resume'} onClick={() => void resume()}>恢复真实评价</Button>}</div>{currentVersion && <details><summary>查看本次提交与历史版本</summary><pre style={{ whiteSpace: 'pre-wrap' }}>{currentVersion.content}</pre><div className="practice-history-list">{projectVersions.map(item => <button key={item.id} onClick={() => setSelectedVersionId(item.id)}><span>版本 v{item.versionNumber}<small>{item.milestoneTitle || '阶段成果'} · {new Date(item.createdAt).toLocaleString()}</small></span><Badge tone={item.status === 'done' ? 'green' : item.status === 'failed' ? 'red' : 'neutral'}>{statusNames[item.status]}</Badge></button>)}</div></details>}{!linkedPlan && <p className="form-help">未关联已采用的学习方案；可在新建项目时选择真实关联目标。</p>}</section>
      </main>
    </div> : <EmptyState title="还没有实践项目" description="创建真实项目与里程碑，提交成果后才会出现版本和评价。" action={<Button onClick={() => setCreateOpen(true)}>创建第一个项目</Button>} />}

    <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="创建实践项目"><div className="form-stack"><label>项目名称<input value={title} onChange={event => setTitle(event.target.value)} placeholder="例如：做一个递归算法可视化工具" /></label><label>想验证的能力<textarea rows={4} value={description} onChange={event => setDescription(event.target.value)} placeholder="我想通过这个项目学会……" /></label><div className="form-grid"><label>目标日期<input type="date" value={due} onChange={event => setDue(event.target.value)} /></label><label>项目分类<input value={tag} maxLength={100} onChange={event => setTag(event.target.value)} /></label></div><label>关联学习目标<select value={planId} onChange={event => setPlanId(event.target.value)}><option value="">不关联学习方案</option>{plans.map(item => <option value={item.id} key={item.id}>{item.goal}</option>)}</select></label><label>初始里程碑（每行一个，可编辑）<textarea rows={5} value={milestoneText} onChange={event => setMilestoneText(event.target.value)} /></label><Button onClick={() => void createProject()} disabled={!title.trim() || !tag.trim() || !due || !milestoneText.trim() || busy === 'create'}><Rocket size={17} />{busy === 'create' ? '正在保存…' : '创建并保存里程碑'}</Button></div></Modal>
    <Modal open={milestoneOpen} onClose={() => setMilestoneOpen(false)} title="添加项目里程碑"><div className="form-stack"><label>里程碑名称<input autoFocus value={milestoneTitle} onChange={event => setMilestoneTitle(event.target.value)} placeholder="输入可检查的实际成果" /></label><Button disabled={!milestoneTitle.trim() || busy === 'milestone'} onClick={() => void addMilestone()}>保存里程碑</Button></div></Modal>
    <Modal open={submitOpen} onClose={() => setSubmitOpen(false)} title="提交真实阶段成果" wide><div className="form-stack"><label>成果正文<textarea rows={10} value={submission} onChange={event => setSubmission(event.target.value)} placeholder="描述实际完成的内容、验证过程、结果与遇到的问题。不要粘贴演示文本。" /></label><div className="form-grid"><label>关联里程碑<select value={submissionMilestone} onChange={event => setSubmissionMilestone(event.target.value)}><option value="">不指定里程碑</option>{project?.milestones.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label><label>关联已解析资料<select value={materialId} onChange={event => setMaterialId(event.target.value)}><option value="">不附加资料</option>{materials.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label></div><p className="form-help">提交后保存独立版本与原文。模型只评价所提交的内容，不声称执行代码；评价过程可通过原生事件实时查看、取消与恢复。</p><Button disabled={!submission.trim() || busy === 'submit'} onClick={() => void submit()}>{busy === 'submit' ? '正在保存并启动…' : '保存版本并开始真实评价'}</Button></div></Modal>
    <Modal open={noteOpen} onClose={() => setNoteOpen(false)} title="记录真实迭代想法"><div className="form-stack"><label>笔记内容<textarea rows={7} value={note} onChange={event => setNote(event.target.value)} /></label><p className="form-help">保存到当前用户的真实笔记库，并关联项目「{project?.title}」与版本 v{currentVersion?.versionNumber}。</p><Button disabled={!note.trim() || busy === 'note'} onClick={() => void saveNote()}>保存迭代笔记</Button></div></Modal>
  </div>
}

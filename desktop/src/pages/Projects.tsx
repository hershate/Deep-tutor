import { useState } from 'react'
import { ArrowRight, CalendarDays, Check, CheckCircle2, ChevronRight, Circle, Code2, ExternalLink, FileUp, FolderKanban, GitBranch, Lightbulb, Plus, Rocket, Sparkles, Trophy } from 'lucide-react'
import type { Project } from '../data'
import { Badge, Button, Modal, ProgressBar, SectionHeader } from '../components/UI'

export default function Projects({ projects, setProjects, notify }: { projects: Project[]; setProjects: (projects: Project[]) => void; notify: (message: string) => void }) {
  const [selected, setSelected] = useState(projects[0]?.id || 0)
  const [createOpen, setCreateOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const project = projects.find(p => p.id === selected) || projects[0]
  const toggleMilestone = (id: number) => {
    if (!project) return
    const updated = projects.map(p => p.id === project.id ? { ...p, milestones: p.milestones.map(m => m.id === id ? { ...m, done: !m.done } : m) } : p)
    setProjects(updated.map(p => ({ ...p, progress: Math.round(p.milestones.filter(m => m.done).length / p.milestones.length * 100) })))
  }
  const createProject = () => {
    if (!title.trim()) return
    const item: Project = { id: Date.now(), title: title.trim(), description: description.trim() || '一个新的学习实践项目。', due: '12 月 01 日', progress: 0, tag: '个人项目', milestones: [{ id: 1, title: '定义项目成果与评价标准', done: false }, { id: 2, title: '完成第一个可验证版本', done: false }, { id: 3, title: '撰写项目复盘', done: false }] }
    setProjects([...projects, item]); setSelected(item.id); setTitle(''); setDescription(''); setCreateOpen(false); notify('项目已创建，并生成 3 个初始里程碑')
  }
  return <div className="page projects-page">
    <div className="page-heading"><div><h1>项目与作品</h1><p>把“我学过”变成“我能做出”，留下可验证的能力证据。</p></div><Button onClick={() => setCreateOpen(true)}><Plus size={18} />新建项目</Button></div>
    <div className="project-stats"><div><FolderKanban size={21} /><strong>{projects.length}</strong><span>进行中项目</span></div><div><CheckCircle2 size={21} /><strong>7</strong><span>已完成里程碑</span></div><div><Trophy size={21} /><strong>4</strong><span>作品集成果</span></div><p><Sparkles size={17} />项目反馈关注思路与迭代，不代替你完成作品。</p></div>
    {project ? <div className="projects-layout">
      <aside className="project-list"><div className="project-list-head"><span>我的项目</span><Badge tone="neutral">{projects.length}</Badge></div>{projects.map(item => <button key={item.id} className={project.id === item.id ? 'active' : ''} onClick={() => setSelected(item.id)}><div><Badge tone="neutral">{item.tag}</Badge><span>{item.due}</span></div><h2>{item.title}</h2><p>{item.description}</p><ProgressBar value={item.progress} /><small>{item.progress}% · {item.milestones.filter(m => m.done).length}/{item.milestones.length} 个里程碑</small></button>)}</aside>
      <main className="project-detail">
        <div className="project-cover"><div className="cover-grid" aria-hidden="true" /><div><Badge tone="green">{project.tag} · 实践项目</Badge><h2>{project.title}</h2><p>{project.description}</p></div><div className="project-cover-progress"><strong>{project.progress}%</strong><span>项目进度</span></div></div>
        <div className="project-meta"><span><CalendarDays size={17} />截止 {project.due}</span><span><GitBranch size={17} />版本 v0.{Math.max(1, Math.round(project.progress / 20))}</span><Button variant="secondary" size="sm" onClick={() => notify('已提交当前版本，知舟将按项目量规给出演示反馈')}><FileUp size={16} />提交阶段成果</Button></div>
        <div className="project-detail-grid"><section className="surface milestone-checklist"><SectionHeader title="项目里程碑" description="每完成一步，进度和作品集证据都会更新。" /><div>{project.milestones.map(milestone => <button key={milestone.id} onClick={() => toggleMilestone(milestone.id)} className={milestone.done ? 'done' : ''}><span>{milestone.done ? <Check size={16} /> : <Circle size={16} />}</span><strong>{milestone.title}</strong><ChevronRight size={16} /></button>)}</div><button className="text-action" onClick={() => { const updated = projects.map(p => p.id === project.id ? { ...p, milestones: [...p.milestones, { id: Date.now(), title: '新的项目里程碑', done: false }] } : p); setProjects(updated); notify('已添加里程碑，可在后续版本中编辑') }}>＋ 添加里程碑</button></section><aside className="surface project-rubric"><SectionHeader title="评价量规" /><div><span>概念应用</span><strong>优秀</strong><ProgressBar value={88} /></div><div><span>实现质量</span><strong>良好</strong><ProgressBar value={74} /></div><div><span>验证与反思</span><strong>待补充</strong><ProgressBar value={35} tone="amber" /></div><p><Lightbulb size={16} />下一步最值得补充：比较精确率与召回率，而不仅报告准确率。</p></aside></div>
        <section className="surface project-feedback"><div><span className="agent-mark"><Sparkles size={18} /></span><div><h3>知舟的项目反馈</h3><p>基于最近提交的演示版本</p></div><Badge tone="green">2 条新建议</Badge></div><blockquote>“模型已经能运行，但评估部分只给出了整体准确率。垃圾邮件场景中，把正常邮件误判为垃圾邮件的代价很高，建议补充混淆矩阵，并解释你更关注哪个指标。”</blockquote><div><Button variant="secondary" onClick={() => notify('已打开指标评估学习资料')}>学习相关概念<ArrowRight size={16} /></Button><Button variant="ghost" onClick={() => notify('已记录一条项目迭代笔记')}>记录迭代想法</Button></div></section>
      </main>
    </div> : <div>暂无项目</div>}
    <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="创建实践项目"><div className="form-stack"><label>项目名称<input value={title} onChange={e => setTitle(e.target.value)} placeholder="例如：做一个递归算法可视化工具" /></label><label>想验证的能力<textarea rows={4} value={description} onChange={e => setDescription(e.target.value)} placeholder="我想通过这个项目学会……" /></label><label>关联目标<select><option>数据科学与概率应用</option><option>编程与数据结构</option><option>英语表达与写作</option></select></label><Button onClick={createProject} disabled={!title.trim()}><Rocket size={17} />创建并生成里程碑</Button></div></Modal>
  </div>
}

import { useState } from 'react'
import { ArrowDownToLine, ArrowLeft, ArrowRight, CalendarRange, Check, ChevronLeft, ChevronRight, Clock3, Copy, Link2, Plus, RefreshCw, Share2, SlidersHorizontal, Sparkles, Target, Trash2 } from 'lucide-react'
import type { PageId, Task } from '../data'
import { Badge, Button, Modal, ProgressBar, SectionHeader } from '../components/UI'
import ExportCenter from '../components/ExportCenter'

const days = ['周一 02', '周二 03', '周三 04', '周四 05', '周五 06', '周六 07', '周日 08']
const schedule = [
  [{ title: '贝叶斯定理', time: '09:00', kind: '学习' }, { title: '英语长难句', time: '19:30', kind: '学习' }],
  [{ title: '条件概率练习', time: '09:00', kind: '练习' }, { title: '递归错题', time: '20:00', kind: '复习' }],
  [{ title: '概率论阶段测', time: '19:00', kind: '测评' }],
  [{ title: '朴素贝叶斯', time: '09:30', kind: '学习' }, { title: '项目：模型评估', time: '20:00', kind: '项目' }],
  [{ title: '英语阅读精练', time: '19:30', kind: '练习' }],
  [{ title: '项目深度工作', time: '10:00', kind: '项目' }, { title: '周复习', time: '16:00', kind: '复习' }],
  [{ title: '轻量复盘', time: '20:00', kind: '复盘' }],
]

export default function Plan({ tasks, setTasks, onNavigate, notify }: { tasks: Task[]; setTasks: (tasks: Task[]) => void; onNavigate: (page: PageId) => void; notify: (message: string) => void }) {
  const [intensity, setIntensity] = useState('标准')
  const [week, setWeek] = useState(0)
  const [addOpen, setAddOpen] = useState(false)
  const [goalOpen, setGoalOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [subject, setSubject] = useState('概率论')
  const [duration, setDuration] = useState(30)
  const [exportOpen, setExportOpen] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)
  const [shareUrl, setShareUrl] = useState('')
  const openShare = () => {
    const token = localStorage.getItem('deep-tutor-share-token') || `dt_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`
    localStorage.setItem('deep-tutor-share-token', token); localStorage.setItem('deep-tutor-share-revoked', 'false')
    setShareUrl(`${window.location.origin}${window.location.pathname}?share=${token}#shared`); setShareOpen(true)
  }
  const addTask = () => {
    if (!title.trim()) return
    setTasks([...tasks, { id: Date.now(), title: title.trim(), subject, duration, done: false, kind: '学习' }])
    setTitle(''); setAddOpen(false); notify('学习任务已加入今日计划')
  }
  return <div className="page plan-page">
    <div className="page-heading"><div><h1>学习计划</h1><p>让目标适配你的生活，而不是让生活追赶计划。</p></div><div className="heading-actions"><Button variant="secondary" onClick={openShare}><Share2 size={17} />分享</Button><Button variant="secondary" onClick={() => setExportOpen(true)}><ArrowDownToLine size={17} />导出</Button><Button onClick={() => onNavigate('generator')}><Sparkles size={17} />AI 生成方案</Button><Button variant="secondary" onClick={() => setAddOpen(true)}><Plus size={17} />添加任务</Button></div></div>

    <section className="goal-strip">
      <div className="goal-main"><span className="goal-icon"><Target size={25} /></span><div><span>当前主目标</span><h2>2026 计算机考研 · 数据结构专项</h2><p>目标日 12 月 20 日 · 每周可投入 15 小时</p></div></div>
      <div className="goal-progress"><div><strong>68%</strong><span>整体进度</span></div><ProgressBar value={68} /></div>
      <Button variant="secondary" onClick={() => setGoalOpen(true)}><SlidersHorizontal size={17} />调整目标</Button>
    </section>

    <div className="plan-toolbar">
      <div className="segmented" aria-label="学习强度">{['稳妥', '标准', '冲刺'].map(item => <button key={item} className={intensity === item ? 'active' : ''} onClick={() => { setIntensity(item); notify(`已切换为${item}强度，任务量将按此调整`) }}>{item}</button>)}</div>
      <div className="plan-toolbar-actions"><span><Clock3 size={16} />本周计划 {intensity === '冲刺' ? '18' : intensity === '稳妥' ? '12' : '15'} 小时</span><Button variant="secondary" size="sm" onClick={() => { setTasks([...tasks.filter(t => t.done), ...tasks.filter(t => !t.done)].map((t, i) => ({ ...t, duration: Math.max(15, t.duration + (intensity === '冲刺' ? 5 : intensity === '稳妥' ? -5 : 0)) }))); notify('已根据掌握度与可用时间重新安排') }}><RefreshCw size={16} />智能重排</Button></div>
    </div>

    <section className="calendar-surface">
      <SectionHeader title={week === 0 ? '11 月 2 日—11 月 8 日' : week < 0 ? '10 月 26 日—11 月 1 日' : '11 月 9 日—11 月 15 日'} description="学习、练习与复习交错安排，降低连续同类任务的疲劳。" action={<div className="calendar-nav"><button aria-label="上一周" onClick={() => setWeek(week - 1)}><ChevronLeft size={18} /></button><button className="today-button" onClick={() => setWeek(0)}>本周</button><button aria-label="下一周" onClick={() => setWeek(week + 1)}><ChevronRight size={18} /></button></div>} />
      <div className="week-calendar">{days.map((day, index) => <div className={`day-column ${index === 0 && week === 0 ? 'is-today' : ''}`} key={day}><div className="day-head"><strong>{day.split(' ')[0]}</strong><span>{day.split(' ')[1]}</span></div><div className="day-events">{(week === 0 ? schedule[index] : schedule[(index + Math.abs(week)) % 7]).map((event, i) => <button className={`calendar-event event-${event.kind}`} key={i} onClick={() => notify(`已打开「${event.title}」任务详情`)}><span>{event.time}</span><strong>{event.title}</strong><small>{event.kind}</small></button>)}</div></div>)}</div>
    </section>

    <div className="plan-bottom-grid">
      <section className="surface milestone-panel"><SectionHeader title="阶段里程碑" description="把远期目标变成可检查的短期成果。" /><div className="milestone-list">
        {[['概率基础与条件概率', '已完成', 100], ['贝叶斯定理与模型应用', '进行中', 64], ['随机变量与数字特征', '待开始', 0], ['概率论综合模拟', '待开始', 0]].map(([name, status, value], index) => <div className="milestone-row" key={String(name)}><span className={`milestone-marker ${value === 100 ? 'done' : ''}`}>{value === 100 ? <Check size={15} /> : index + 1}</span><div><strong>{name}</strong><ProgressBar value={Number(value)} /></div><Badge tone={value === 100 ? 'green' : value === 64 ? 'amber' : 'neutral'}>{status}</Badge></div>)}
      </div></section>
      <section className="surface workload-panel"><SectionHeader title="学习负荷" description="根据任务预计耗时计算。" /><div className="workload-number"><strong>72<small>%</small></strong><span>当前负荷适中</span></div><ProgressBar value={72} tone="blue" /><p>周六安排了 3 小时项目深度工作。若临时有事，可将其中 1 小时迁移到周日，不影响里程碑。</p><Button variant="ghost" onClick={() => notify('已生成 3 种本周时间调整方案')}>查看替代安排<ArrowRight size={16} /></Button></section>
    </div>

    <Modal open={addOpen} onClose={() => setAddOpen(false)} title="添加学习任务">
      <div className="form-stack"><label>任务名称<input autoFocus value={title} onChange={e => setTitle(e.target.value)} placeholder="例如：完成贝叶斯定理变式练习" /></label><div className="form-grid"><label>科目<select value={subject} onChange={e => setSubject(e.target.value)}><option>概率论</option><option>数据结构</option><option>英语</option><option>项目实践</option></select></label><label>预计时长（分钟）<input type="number" min={5} max={240} value={duration} onChange={e => setDuration(Number(e.target.value))} /></label></div><Button onClick={addTask} disabled={!title.trim()}>加入今日计划</Button></div>
    </Modal>
    <Modal open={goalOpen} onClose={() => setGoalOpen(false)} title="调整学习目标">
      <div className="form-stack"><label>目标名称<input defaultValue="2026 计算机考研 · 数据结构专项" /></label><div className="form-grid"><label>目标日期<input type="date" defaultValue="2026-12-20" /></label><label>每周可投入（小时）<input type="number" defaultValue={15} min={1} max={80} /></label></div><label>当前掌握情况<select defaultValue="部分掌握"><option>零基础</option><option>部分掌握</option><option>系统学过，需要冲刺</option></select></label><div className="modal-callout"><CalendarRange size={18} /><span>保存后，知舟会保留已完成记录，并给出新的计划建议。</span></div><Button onClick={() => { setGoalOpen(false); notify('目标已更新，学习路径已重新校准') }}>保存并重新规划</Button></div>
    </Modal>
    <ExportCenter open={exportOpen} onClose={() => setExportOpen(false)} notify={notify} />
    <Modal open={shareOpen} onClose={() => setShareOpen(false)} title="分享只读学习方案"><div className="form-stack"><div className="share-status"><Link2 size={19} /><div><strong>免登录只读链接</strong><p>默认 90 天过期，不包含私人笔记、会话或学习记录。当前演示链接仅在本浏览器有效。</p></div><Badge tone="green">有效</Badge></div><label>分享地址<div className="copy-field"><input readOnly value={shareUrl} onFocus={e => e.target.select()} /><Button variant="secondary" onClick={async () => { try { await navigator.clipboard.writeText(shareUrl); notify('只读链接已复制') } catch { notify('请手动复制输入框中的链接') } }}><Copy size={16} />复制</Button></div></label><div className="share-actions"><Button variant="secondary" onClick={() => { window.location.hash = 'shared'; window.open(shareUrl, '_blank', 'noopener') }}>打开只读预览</Button><Button variant="ghost" onClick={() => { localStorage.setItem('deep-tutor-share-revoked', 'true'); setShareOpen(false); notify('分享已撤销；链接现在与不存在一样返回 404') }}><Trash2 size={16} />撤销分享</Button></div></div></Modal>
  </div>
}

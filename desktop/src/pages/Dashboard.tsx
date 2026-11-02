import { ArrowRight, BookOpen, Brain, CalendarDays, Check, ChevronRight, CircleAlert, Clock3, Flame, Lightbulb, Play, RotateCcw, Sparkles, Target, TrendingUp } from 'lucide-react'
import type { PageId, Task } from '../data'
import { Badge, Button, MiniBars, ProgressBar, ProgressRing, SectionHeader } from '../components/UI'

export default function Dashboard({ tasks, setTasks, onNavigate, onFocus, notify }: { tasks: Task[]; setTasks: (tasks: Task[]) => void; onNavigate: (page: PageId) => void; onFocus: () => void; notify: (message: string) => void }) {
  const done = tasks.filter(task => task.done).length
  const completion = Math.round(done / tasks.length * 100)
  const toggleTask = (id: number) => setTasks(tasks.map(task => task.id === id ? { ...task, done: !task.done } : task))
  return <div className="page dashboard-page">
    <div className="page-heading">
      <div><p className="date-line">11 月 2 日 · 星期一</p><h1>早上好，陈默。</h1><p>今天不必学很多，只需完成最关键的三件事。</p></div>
      <Button variant="secondary" onClick={onFocus}><Play size={17} />进入专注</Button>
    </div>

    <section className="today-hero">
      <div className="hero-orbit" aria-hidden="true"><span /><span /><span /></div>
      <div className="today-hero-copy">
        <Badge tone="green">当前目标 · 计算机考研</Badge>
        <h2>把「不知道学什么」<br />变成清晰的下一步。</h2>
        <p>下一站：贝叶斯定理 · 条件概率如何更新我们的判断</p>
        <div className="hero-actions"><Button variant="light" size="lg" onClick={() => onNavigate('study')}>继续上次学习<ArrowRight size={18} /></Button><span><Clock3 size={16} />预计 35 分钟</span></div>
      </div>
      <div className="hero-progress"><ProgressRing value={68} size={148}><div><strong>68%</strong><span>目标进度</span></div></ProgressRing><p>距离目标日还有 <strong>48 天</strong></p></div>
    </section>

    <div className="dashboard-grid">
      <section className="surface task-surface">
        <SectionHeader title="今日学习航线" description={`${done}/${tasks.length} 项已完成 · 约 ${tasks.filter(t => !t.done).reduce((sum, t) => sum + t.duration, 0)} 分钟剩余`} action={<Button variant="ghost" size="sm" onClick={() => onNavigate('plan')}>查看计划<ChevronRight size={16} /></Button>} />
        <div className="task-progress"><ProgressBar value={completion} /><span>{completion}%</span></div>
        <div className="task-list">
          {tasks.map((task, index) => <div className={`task-row ${task.done ? 'is-done' : ''}`} key={task.id}>
            <button className="task-check" aria-label={task.done ? `将${task.title}标为未完成` : `完成${task.title}`} onClick={() => toggleTask(task.id)}>{task.done ? <Check size={16} /> : <span>{index + 1}</span>}</button>
            <div className="task-copy"><strong>{task.title}</strong><span>{task.subject} · {task.kind}</span></div>
            <span className="task-time">{task.duration} 分钟</span>
            {!task.done && <button className="task-go" aria-label={`开始${task.title}`} onClick={() => onNavigate(task.kind === '复习' ? 'review' : task.kind === '练习' ? 'practice' : task.kind === '项目' ? 'projects' : 'study')}><Play size={16} /></button>}
          </div>)}
        </div>
        <button className="text-action" onClick={() => { setTasks([...tasks, { id: Date.now(), title: '新的学习任务', subject: '待分类', duration: 20, done: false, kind: '学习' }]); notify('已添加任务，可在学习计划中编辑') }}>＋ 添加今日任务</button>
      </section>

      <aside className="dashboard-side">
        <section className="surface review-callout">
          <div className="review-callout-head"><span className="soft-icon"><RotateCcw size={19} /></span><Badge tone="amber">记忆到期</Badge></div>
          <h3>12 张卡片等待复习</h3><p>现在回忆，比重新阅读一遍更有效。</p>
          <div className="review-meta"><span>概率论 7</span><span>数据结构 5</span></div>
          <Button onClick={() => onNavigate('review')}>开始 8 分钟复习<ArrowRight size={17} /></Button>
        </section>
        <section className="surface streak-panel">
          <div><Flame size={20} /><strong>连续学习 16 天</strong></div><p>保持节奏，但休息也算计划的一部分。</p>
          <div className="week-dots">{['一', '二', '三', '四', '五', '六', '日'].map((day, i) => <div key={day}><span className={i < 6 ? 'active' : ''}>{i < 6 ? <Check size={13} /> : ''}</span><small>{day}</small></div>)}</div>
        </section>
      </aside>
    </div>

    <div className="dashboard-lower">
      <section className="surface rhythm-panel">
        <SectionHeader title="本周学习节奏" description="总计 12 小时 40 分钟，比上周多 8%" action={<Badge tone="green"><TrendingUp size={13} />稳定上升</Badge>} />
        <MiniBars values={[70, 95, 55, 110, 82, 125, 45]} labels={['一', '二', '三', '四', '五', '六', '日']} />
        <div className="rhythm-stats"><div><span>专注学习</span><strong>9.6<small>小时</small></strong></div><div><span>主动练习</span><strong>126<small>题</small></strong></div><div><span>记忆保持率</span><strong>84<small>%</small></strong></div></div>
      </section>
      <section className="surface insight-panel">
        <div className="insight-title"><Sparkles size={19} /><h2>知舟的学习观察</h2></div>
        <p>你在<strong>条件概率</strong>的练习中表现稳定，但遇到“低基线率”情境时容易忽略先验概率。</p>
        <div className="insight-evidence"><CircleAlert size={17} /><span>依据：最近 12 道题中的 3 次同类错误</span></div>
        <Button variant="secondary" onClick={() => { notify('已生成 3 道低基线率变式题'); onNavigate('practice') }}><Lightbulb size={17} />生成针对练习</Button>
      </section>
      <section className="surface upcoming-panel">
        <SectionHeader title="接下来" action={<CalendarDays size={19} />} />
        <div className="upcoming-item"><span className="upcoming-date">04<small>11 月</small></span><div><strong>阶段测验 · 概率论</strong><p>20 题 · 限时 45 分钟</p></div></div>
        <div className="upcoming-item"><span className="upcoming-date">12<small>11 月</small></span><div><strong>分类器项目截止</strong><p>还剩 2 个里程碑</p></div></div>
        <button className="text-action" onClick={() => onNavigate('projects')}>查看项目与里程碑<ArrowRight size={15} /></button>
      </section>
    </div>
    <div className="demo-note"><Brain size={15} />当前展示为合成学习数据，操作会保存在此浏览器中。</div>
  </div>
}

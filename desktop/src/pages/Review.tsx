import { useEffect, useState } from 'react'
import { ArrowRight, Brain, CalendarClock, Check, ChevronLeft, ChevronRight, CircleHelp, Clock3, Layers3, Plus, RotateCcw, Sparkles } from 'lucide-react'
import { reviewCards } from '../data'
import { Badge, Button, Modal, ProgressBar, SectionHeader } from '../components/UI'

type Card = typeof reviewCards[number]

export default function Review({ notify }: { notify: (message: string) => void }) {
  const [cards, setCards] = useState<Card[]>(reviewCards)
  const [index, setIndex] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const [reviewed, setReviewed] = useState(0)
  const [createOpen, setCreateOpen] = useState(false)
  const [front, setFront] = useState('')
  const [back, setBack] = useState('')
  const finished = index >= cards.length
  const grade = (level: string) => {
    setReviewed(reviewed + 1); setIndex(index + 1); setFlipped(false)
    notify(level === '再来一次' ? '这张卡片会在今天稍后再出现' : `已记录为「${level}」，下次复习时间已更新`)
  }
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (createOpen || finished) return
      if (event.code === 'Space') { event.preventDefault(); setFlipped(value => !value) }
      if (flipped && ['Digit1', 'Digit2', 'Digit3', 'Digit4'].includes(event.code)) {
        event.preventDefault(); grade({ Digit1: '再来一次', Digit2: '困难', Digit3: '良好', Digit4: '轻松' }[event.code] || '良好')
      }
    }
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler)
  }, [flipped, finished, createOpen, index, reviewed])
  const createCard = () => {
    if (!front.trim() || !back.trim()) return
    setCards([...cards, { id: Date.now(), front: front.trim(), back: back.trim(), tag: '手动创建', due: '现在' }])
    setFront(''); setBack(''); setCreateOpen(false); notify('记忆卡已加入复习队列')
  }
  return <div className="page review-page">
    <div className="page-heading"><div><h1>记忆复习</h1><p>先努力回忆，再查看答案；每次反馈都会改变下次出现的时间。</p></div><Button variant="secondary" onClick={() => setCreateOpen(true)}><Plus size={17} />创建记忆卡</Button></div>
    <div className="review-stats"><div><Layers3 size={20} /><span>今日待复习</span><strong>{Math.max(0, cards.length - index)}<small>张</small></strong></div><div><Brain size={20} /><span>记忆保持率</span><strong>84<small>%</small></strong></div><div><Clock3 size={20} /><span>预计用时</span><strong>8<small>分钟</small></strong></div><div><CalendarClock size={20} /><span>明日到期</span><strong>18<small>张</small></strong></div></div>
    <div className="review-layout">
      <section className="review-workspace">
        {!finished ? <>
          <div className="review-progress"><span>今日复习 · {index + 1} / {cards.length}</span><ProgressBar value={reviewed / cards.length * 100} /><Badge tone="neutral">{cards[index].tag}</Badge></div>
          <button className={`flashcard ${flipped ? 'is-flipped' : ''}`} onClick={() => setFlipped(!flipped)} aria-label={flipped ? '查看问题' : '查看答案'}>
            <div className="flashcard-face flashcard-front"><span>问题</span><h2>{cards[index].front}</h2><small><CircleHelp size={15} />先在心里作答，再点击查看答案</small></div>
            <div className="flashcard-face flashcard-back"><span>参考答案</span><h2>{cards[index].back}</h2><small>对照你的回答，再诚实选择记忆程度</small></div>
          </button>
          <div className="review-keyboard">Space 翻卡 · 1 再来一次 · 2 困难 · 3 良好 · 4 轻松</div>
          {!flipped ? <Button size="lg" onClick={() => setFlipped(true)}>显示答案<ChevronRight size={18} /></Button> : <div className="review-grade"><button className="grade-again" onClick={() => grade('再来一次')}><span>再来一次</span><small>&lt; 10 分钟</small></button><button className="grade-hard" onClick={() => grade('困难')}><span>困难</span><small>2 天后</small></button><button className="grade-good" onClick={() => grade('良好')}><span>良好</span><small>5 天后</small></button><button className="grade-easy" onClick={() => grade('轻松')}><span>轻松</span><small>12 天后</small></button></div>}
        </> : <div className="review-complete"><span><Check size={32} /></span><h2>今天的复习完成了。</h2><p>你完成了 {reviewed} 次主动回忆。明天会有 18 张新到期卡片。</p><div><Button onClick={() => { setIndex(0); setReviewed(0); setFlipped(false) }}><RotateCcw size={17} />再复习一轮</Button><Button variant="secondary" onClick={() => notify('已打开未来 7 天复习日历')}>查看复习日历</Button></div></div>}
      </section>
      <aside className="review-side"><section className="surface forgetting-panel"><SectionHeader title="记忆节奏" description="未来 7 天到期卡片" /><div className="review-calendar">{[['今天', 12], ['周二', 18], ['周三', 9], ['周四', 22], ['周五', 14], ['周六', 7], ['周日', 16]].map(([day, count], i) => <div key={String(day)}><span style={{ height: `${Number(count) * 3}px` }} className={i === 0 ? 'active' : ''} /><small>{day}</small><strong>{count}</strong></div>)}</div></section><section className="surface review-method"><Sparkles size={20} /><h3>为什么先显示问题？</h3><p>主动回忆会暴露“看得熟但想不起”的假性掌握。答错不代表退步，它告诉系统下一次该何时出现。</p><button onClick={() => notify('复习算法：根据回忆难度、间隔和历史表现动态安排')}>了解复习安排逻辑<ArrowRight size={15} /></button></section></aside>
    </div>
    <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="创建记忆卡"><div className="form-stack"><label>正面 · 问题<textarea rows={3} value={front} onChange={e => setFront(e.target.value)} placeholder="尽量一次只检查一个知识点" /></label><label>背面 · 参考答案<textarea rows={4} value={back} onChange={e => setBack(e.target.value)} placeholder="保留关键解释，避免整段复制" /></label><label>关联知识点<select><option>概率论 · 贝叶斯定理</option><option>数据结构 · 递归</option><option>英语 · 长难句</option></select></label><Button onClick={createCard} disabled={!front.trim() || !back.trim()}>加入复习队列</Button></div></Modal>
  </div>
}

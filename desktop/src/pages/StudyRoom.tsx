import { useState } from 'react'
import { ArrowLeft, ArrowRight, BookMarked, BookOpen, Check, ChevronDown, ChevronRight, FileText, Highlighter, ListTree, LoaderCircle, Maximize2, Minus, NotebookPen, Pause, Play, Plus, Quote, Sparkles, Video } from 'lucide-react'
import type { Note } from '../data'
import TutorPanel from '../components/TutorPanel'
import { Badge, Button, IconButton, Modal, ProgressBar } from '../components/UI'

const lessons = [
  { id: 1, title: '从事件到概率', duration: '22 分钟', done: true },
  { id: 2, title: '条件概率与乘法公式', duration: '31 分钟', done: true },
  { id: 3, title: '贝叶斯定理', duration: '35 分钟', done: false },
  { id: 4, title: '全概率公式', duration: '28 分钟', done: false },
  { id: 5, title: '朴素贝叶斯应用', duration: '42 分钟', done: false },
]

export default function StudyRoom({ onAddNote, onFocus, notify }: { onAddNote: (note: Note) => void; onFocus: () => void; notify: (message: string) => void }) {
  const [lesson, setLesson] = useState(3)
  const [tab, setTab] = useState('阅读')
  const [fontScale, setFontScale] = useState(1)
  const [bookmarked, setBookmarked] = useState(false)
  const [noteOpen, setNoteOpen] = useState(false)
  const [note, setNote] = useState('')
  const [playing, setPlaying] = useState(false)
  const [courseOpen, setCourseOpen] = useState(true)
  const [lectureOpen, setLectureOpen] = useState(false)
  const [lectureRunning, setLectureRunning] = useState(false)
  const [lectureDone, setLectureDone] = useState(false)
  const saveNote = () => {
    if (!note.trim()) return
    onAddNote({ id: Date.now(), title: '贝叶斯定理 · 学习批注', excerpt: note.trim().slice(0, 90), tag: '概率论', updated: '刚刚', content: note.trim() })
    setNote(''); setNoteOpen(false); notify('笔记已保存，并关联到当前章节')
  }
  return <div className="study-page">
    <div className="study-topbar">
      <div><button className="back-course" onClick={() => notify('已返回课程概览')}><ArrowLeft size={17} />概率论与数理统计</button><span>/</span><strong>{lessons.find(l => l.id === lesson)?.title}</strong></div>
      <div className="study-progress"><span>本章进度 64%</span><ProgressBar value={64} /></div>
      <div className="study-actions"><IconButton label={bookmarked ? '取消收藏' : '收藏本节'} className={bookmarked ? 'is-active' : ''} onClick={() => { setBookmarked(!bookmarked); notify(bookmarked ? '已取消收藏' : '已收藏本节') }}><BookMarked size={18} fill={bookmarked ? 'currentColor' : 'none'} /></IconButton><Button variant="secondary" size="sm" onClick={onFocus}><Maximize2 size={16} />专注模式</Button></div>
    </div>
    <div className="study-layout">
      <aside className={`course-rail ${courseOpen ? '' : 'is-collapsed'}`}>
        <button className="course-rail-title" onClick={() => setCourseOpen(!courseOpen)}><span><ListTree size={17} />课程目录</span><ChevronDown size={16} /></button>
        {courseOpen && <><div className="course-chapter"><span>第四章</span><strong>条件概率与贝叶斯</strong></div><div className="lesson-list">{lessons.map(item => <button key={item.id} className={lesson === item.id ? 'active' : ''} onClick={() => setLesson(item.id)}><span className="lesson-state">{item.done ? <Check size={13} /> : item.id}</span><div><strong>{item.title}</strong><small>{item.duration}</small></div>{lesson === item.id && <ChevronRight size={15} />}</button>)}</div><div className="course-rail-bottom"><span>本章掌握度</span><strong>64%</strong><ProgressBar value={64} /><small>来自 12 道练习与 2 次复习</small></div></>}
      </aside>

      <main className="reading-pane">
        <div className="reading-toolbar"><div className="content-tabs">{['阅读', '视频', '资料'].map(item => <button className={tab === item ? 'active' : ''} key={item} onClick={() => setTab(item)}>{item === '阅读' ? <BookOpen size={16} /> : item === '视频' ? <Video size={16} /> : <FileText size={16} />}{item}</button>)}</div><div className="reading-tools"><IconButton label="减小字号" onClick={() => setFontScale(Math.max(.9, fontScale - .1))}><Minus size={16} /></IconButton><span>{Math.round(fontScale * 100)}%</span><IconButton label="增大字号" onClick={() => setFontScale(Math.min(1.25, fontScale + .1))}><Plus size={16} /></IconButton><IconButton label="生成自学讲义" onClick={() => setLectureOpen(true)}><Sparkles size={17} /></IconButton><IconButton label="记录学习笔记" onClick={() => setNoteOpen(true)}><NotebookPen size={17} /></IconButton></div></div>
        {tab === '阅读' && <article className="reading-article" style={{ fontSize: `${fontScale}rem` }}>
          <div className="article-kicker"><Badge tone="green">核心概念</Badge><span>预计阅读 12 分钟</span></div>
          <h1>{lessons.find(l => l.id === lesson)?.title}</h1>
          <p className="article-lead">贝叶斯定理不是一条孤立的计算公式，而是一种在新证据出现后，理性更新判断的方式。</p>
          <div className="article-summary"><span>本节你将学会</span><ul><li>区分先验概率、似然与后验概率</li><li>用全概率公式计算证据出现的概率</li><li>解释低基线率情境中的假阳性问题</li></ul></div>
          <h2>一、从“结果”反推“原因”</h2>
          <p>假设你收到一封被标记为垃圾邮件的邮件。你真正关心的不是“垃圾邮件中出现这个词的概率”，而是<strong>“在出现这个词的条件下，这封邮件是垃圾邮件的概率”</strong>。</p>
          <p>这两个条件概率方向相反。贝叶斯定理提供的正是它们之间的桥梁：</p>
          <div className="formula-block"><span>P(A｜B) =</span><span className="fraction"><i>P(B｜A) · P(A)</i><i>P(B)</i></span><button aria-label="收藏公式" onClick={() => { setNote('贝叶斯公式：P(A|B) = P(B|A) × P(A) / P(B)'); setNoteOpen(true) }}><Quote size={17} />引用到笔记</button></div>
          <div className="term-grid"><div><span>先验概率 P(A)</span><p>看到新证据之前，我们对原因 A 的判断。</p></div><div><span>似然 P(B｜A)</span><p>原因 A 为真时，观察到证据 B 的可能性。</p></div><div><span>后验概率 P(A｜B)</span><p>结合证据之后，对原因 A 更新后的判断。</p></div></div>
          <h2>二、为什么“准确率 99%”还不够？</h2>
          <p>想象一种患病率为 1% 的疾病。检测对患者的阳性识别率为 99%，对健康人的阴性识别率为 95%。现在，一位随机受检者得到阳性结果。</p>
          <p><mark>直觉很容易盯住 99%，却忽略只有 1% 的人患病。</mark>要判断阳性结果意味着什么，我们需要同时考虑两组人：真正患病且检测阳性的人，以及健康但被误判为阳性的人。</p>
          <div className="worked-example"><div className="example-head"><span>例题</span><strong>用 10,000 人建立直觉</strong></div><ol><li>约有 100 人患病，其中 99 人检测为阳性。</li><li>约有 9,900 人健康，其中 5% 被误判为阳性，即 495 人。</li><li>阳性共有 594 人，真正患病的是 99 人。</li></ol><p>因此，阳性后的患病概率约为 99 ÷ 594 ≈ <strong>16.7%</strong>。</p><button onClick={() => setNoteOpen(true)}><Highlighter size={16} />对这道例题做批注</button></div>
          <h2>三、把公式变成可迁移的思维</h2>
          <p>面对任何新证据，可以先问三个问题：我原先相信什么？如果我的判断为真，看到这份证据的可能性有多大？如果我的判断为假，这份证据又有多可能出现？</p>
          <div className="article-check"><div><Check size={20} /><h3>离开之前，做一次主动回忆</h3></div><p>不看上文，用自己的话解释：为什么低患病率会放大假阳性的影响？</p><Button onClick={() => notify('已进入理解检查，请在右侧导师面板写下你的解释')}>写下我的解释<ArrowRight size={16} /></Button></div>
          <div className="article-footer"><Button variant="secondary" onClick={() => setLesson(Math.max(1, lesson - 1))}><ArrowLeft size={16} />上一节</Button><Button onClick={() => { setLesson(Math.min(5, lesson + 1)); notify('本节学习进度已保存') }}>完成并继续<ArrowRight size={16} /></Button></div>
        </article>}
        {tab === '视频' && <div className="video-learning"><div className="video-stage"><div className="video-diagram"><span>先验</span><ArrowRight /><span>新证据</span><ArrowRight /><strong>后验</strong></div><button onClick={() => setPlaying(!playing)} aria-label={playing ? '暂停视频' : '播放视频'}>{playing ? <Pause size={30} /> : <Play size={30} />}</button><div className="video-time">08:42 / 18:24</div></div><div className="video-controls"><h2>贝叶斯思维的三个直觉实验</h2><Badge tone="green">中文字幕</Badge></div><div className="transcript"><h3>同步逐句稿</h3><button onClick={() => notify('已跳转至 06:18')}>06:18</button><p>我们先不看公式，而是用一万个人的例子，看看假阳性从哪里来……</p><button onClick={() => notify('已跳转至 08:42')}>08:42</button><p className="active">当先验概率非常小时，即使新证据很强，后验概率也可能没有直觉上那么高。</p></div></div>}
        {tab === '资料' && <div className="chapter-resources"><h1>本章关联资料</h1>{[['教材原文 · 第 4 章', 'PDF · 48 页'], ['贝叶斯定理公式卡', 'PDF · 2 页'], ['低基线率变式练习', '12 道题'], ['医学筛查案例数据集', 'CSV · 1,000 条记录']].map(([name, meta]) => <button key={name} onClick={() => notify(`已打开：${name}`)}><FileText size={21} /><div><strong>{name}</strong><span>{meta}</span></div><ArrowRight size={17} /></button>)}</div>}
      </main>
      <aside className="study-tutor"><TutorPanel /></aside>
    </div>
    <Modal open={noteOpen} onClose={() => setNoteOpen(false)} title="记录学习笔记">
      <div className="form-stack"><div className="note-context"><BookOpen size={16} />贝叶斯定理 · 当前阅读位置</div><label>笔记内容<textarea autoFocus rows={7} value={note} onChange={e => setNote(e.target.value)} placeholder="记录你的理解、疑问或一个自己的例子…" /></label><div className="note-quick"><span>快速插入：</span><button onClick={() => setNote(note + '\n我的理解：')}>我的理解</button><button onClick={() => setNote(note + '\n仍有疑问：')}>仍有疑问</button><button onClick={() => setNote(note + '\n自己的例子：')}>自己的例子</button></div><Button onClick={saveNote} disabled={!note.trim()}>保存并关联本章</Button></div>
    </Modal>
    <Modal open={lectureOpen} onClose={() => { if (!lectureRunning) setLectureOpen(false) }} title="生成自学讲义"><div className="form-stack"><div className="lecture-scope"><BookOpen size={18} /><div><strong>贝叶斯定理 · 难度 3/5</strong><p>按知识节点加权切分 4 个单元，引用仅保留资料白名单。</p></div></div><label>生成方式<select defaultValue="SSE 流式生成"><option>SSE 流式生成</option><option>同步 JSON</option><option>后台任务</option></select></label><div className="lecture-options"><span>单元 1：直觉与先验</span><span>单元 2：公式与证据</span><span>单元 3：低基线率案例</span><span>单元 4：迁移与检查</span></div>{lectureRunning && <div className="lecture-progress"><LoaderCircle size={18} className="spin" /><div><strong>正在逐单元生成讲义</strong><p>单元 2 / 4 · 引文清洗 · 内容未超过 500 行，无需分片</p><ProgressBar value={48} /></div></div>}{lectureDone && <div className="lecture-done"><Check size={18} /><div><strong>4 个讲义单元已就绪</strong><p>18 条引文均通过白名单校验，单元检查题已同步生成。</p></div></div>}<div className="generator-actions"><Button variant="secondary" onClick={() => setLectureOpen(false)} disabled={lectureRunning}>取消</Button><Button onClick={() => { if (lectureDone) { setLectureOpen(false); notify('新讲义已替换当前阅读版本，旧版本已保留') } else { setLectureRunning(true); window.setTimeout(() => { setLectureRunning(false); setLectureDone(true) }, 1200) } }} disabled={lectureRunning}>{lectureRunning ? '生成中…' : lectureDone ? '打开新讲义' : '开始生成'}</Button></div></div></Modal>
  </div>
}

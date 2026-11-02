import { useState } from 'react'
import { ArrowLeft, ArrowRight, BookOpenCheck, Check, CheckCircle2, ChevronRight, CircleAlert, Clock3, FileQuestion, Lightbulb, ListChecks, RefreshCw, RotateCcw, SlidersHorizontal, Target, X, XCircle } from 'lucide-react'
import { practiceQuestions } from '../data'
import { Badge, Button, Modal, ProgressBar, SectionHeader } from '../components/UI'
import SubjectivePractice from '../components/SubjectivePractice'

const mistakes = [
  { id: 1, title: '低基线率下的阳性结果判断', tag: '贝叶斯定理', reason: '忽略先验概率', count: 3 },
  { id: 2, title: '独立与互斥的概念区分', tag: '概率基础', reason: '概念混淆', count: 2 },
  { id: 3, title: '递归终止条件覆盖不全', tag: '数据结构', reason: '边界遗漏', count: 2 },
]

export default function Practice({ notify }: { notify: (message: string) => void }) {
  const [mode, setMode] = useState('针对练习')
  const [index, setIndex] = useState(0)
  const [selected, setSelected] = useState<number | null>(null)
  const [submitted, setSubmitted] = useState(false)
  const [hint, setHint] = useState(false)
  const [score, setScore] = useState(0)
  const [finished, setFinished] = useState(false)
  const [setupOpen, setSetupOpen] = useState(false)
  const question = practiceQuestions[index]
  const submit = () => {
    if (selected === null) return
    setSubmitted(true)
    if (selected === question.answer) setScore(score + 1)
  }
  const next = () => {
    if (index === practiceQuestions.length - 1) { setFinished(true); return }
    setIndex(index + 1); setSelected(null); setSubmitted(false); setHint(false)
  }
  const restart = (start = 0) => { setIndex(start); setSelected(null); setSubmitted(false); setHint(false); setScore(0); setFinished(false); setMode('针对练习') }
  return <div className="page practice-page">
    <div className="page-heading"><div><h1>练习与测评</h1><p>不是证明你看过，而是确认你能独立调用。</p></div><Button onClick={() => setSetupOpen(true)}><SlidersHorizontal size={17} />生成练习</Button></div>
    <div className="practice-tabs">{['针对练习', '主观题', '错题本', '模拟考试'].map(item => <button key={item} className={mode === item ? 'active' : ''} onClick={() => { setMode(item); if (item !== '针对练习') setFinished(false) }}>{item === '针对练习' ? <Target size={17} /> : item === '错题本' ? <RotateCcw size={17} /> : <Clock3 size={17} />}{item}{item === '错题本' && <span>6</span>}</button>)}</div>

    {mode === '针对练习' && <div className="practice-layout">
      <section className="surface question-surface">
        {!finished ? <>
          <div className="question-top"><div><Badge tone="green">贝叶斯定理</Badge><span>单选题 · 难度适中</span></div><span>第 {index + 1} / {practiceQuestions.length} 题</span></div>
          <ProgressBar value={(index + (submitted ? 1 : 0)) / practiceQuestions.length * 100} />
          <div className="question-body"><h2>{question.question}</h2><div className="option-list">{question.options.map((option, i) => {
            const isCorrect = submitted && i === question.answer
            const isWrong = submitted && selected === i && i !== question.answer
            return <button key={option} disabled={submitted} className={`${selected === i ? 'selected' : ''} ${isCorrect ? 'correct' : ''} ${isWrong ? 'wrong' : ''}`} onClick={() => setSelected(i)}><span>{String.fromCharCode(65 + i)}</span><strong>{option}</strong>{isCorrect && <CheckCircle2 size={20} />}{isWrong && <XCircle size={20} />}</button>
          })}</div>
          {hint && !submitted && <div className="hint-box"><Lightbulb size={18} /><p>{question.hint}</p></div>}
          {submitted && <div className={`answer-feedback ${selected === question.answer ? 'is-correct' : 'is-wrong'}`}><div>{selected === question.answer ? <CheckCircle2 size={21} /> : <CircleAlert size={21} />}<h3>{selected === question.answer ? '回答正确，判断依据很清晰。' : '这一步值得再看一次。'}</h3></div><p>{question.explanation}</p><button onClick={() => notify('已收藏本题的解析与知识点')}>收藏解析</button></div>}
          </div>
          <div className="question-actions"><Button variant="ghost" onClick={() => setHint(!hint)} disabled={submitted}><Lightbulb size={17} />{hint ? '收起提示' : '只给一点提示'}</Button>{!submitted ? <Button onClick={submit} disabled={selected === null}>提交答案<ArrowRight size={17} /></Button> : <Button onClick={next}>{index === practiceQuestions.length - 1 ? '查看结果' : '下一题'}<ArrowRight size={17} /></Button>}</div>
        </> : <div className="practice-result"><span className="result-mark"><BookOpenCheck size={36} /></span><p>本次针对练习</p><h2>{score} / {practiceQuestions.length}</h2><span>答对题目</span><div className="result-analysis"><div><strong>{score === 3 ? '掌握稳定' : '仍需巩固'}</strong><p>{score === 3 ? '可以尝试更高难度的综合应用，减少同类重复练习。' : '主要缺口仍与先验概率的判断有关，建议完成一次变式练习。'}</p></div><Badge tone={score === 3 ? 'green' : 'amber'}>掌握度 {Math.round(score / 3 * 100)}%</Badge></div><div className="result-actions"><Button onClick={() => restart()}><RefreshCw size={17} />再练一组</Button><Button variant="secondary" onClick={() => setMode('错题本')}>查看错题本</Button></div></div>}
      </section>
      <aside className="practice-side"><section className="surface"><SectionHeader title="本组依据" /><p className="side-copy">题目来自你最近的学习内容与 3 次同类错误，重点检查是否能迁移，而非记忆原题。</p><div className="evidence-list"><span><Check size={14} />贝叶斯定理学习完成</span><span><Check size={14} />低基线率错误 3 次</span><span><Check size={14} />最近复习在 3 天前</span></div></section><section className="surface practice-strategy"><Target size={22} /><h3>答题策略</h3><p>先写出题目问的是哪个条件概率，再选择公式。需要时只要提示，不必直接查看答案。</p></section></aside>
    </div>}

    {mode === '主观题' && <SubjectivePractice notify={notify} />}

    {mode === '错题本' && <section className="surface mistakes-surface"><SectionHeader title="错题不是仓库，而是下一步练习的入口" description="按错误原因归类，重做对同类变式题后才标记为掌握。" action={<Button variant="secondary" size="sm" onClick={() => notify('错题记录已导出为 JSON 文件')}>导出错题</Button>} /><div className="mistake-list">{mistakes.map(item => <div className="mistake-row" key={item.id}><span className="mistake-count">{item.count}<small>次错误</small></span><div><Badge tone="neutral">{item.tag}</Badge><h3>{item.title}</h3><p>错误原因：{item.reason} · 最近出现在 2 天前</p></div><div className="mistake-actions"><Button variant="secondary" size="sm" onClick={() => restart(0)}>重做变式<ChevronRight size={15} /></Button><button onClick={() => notify(`已将「${item.title}」标记为已掌握`)}>标记掌握</button></div></div>)}</div></section>}

    {mode === '模拟考试' && <div className="exam-layout"><section className="surface exam-intro"><Badge tone="blue">阶段测评</Badge><h2>概率论 · 第四章综合测验</h2><p>覆盖条件概率、全概率公式、贝叶斯定理与独立性。完成后会更新知识地图掌握度。</p><div className="exam-facts"><div><FileQuestion size={20} /><strong>20 题</strong><span>12 单选 · 6 计算 · 2 简答</span></div><div><Clock3 size={20} /><strong>45 分钟</strong><span>提交后统一查看解析</span></div><div><ListChecks size={20} /><strong>80 分</strong><span>建议达标分数</span></div></div><Button size="lg" onClick={() => { restart(0); notify('模拟考试已开始，答案将在最后统一反馈') }}>开始模拟考试<ArrowRight size={17} /></Button></section><aside className="surface exam-history"><h3>最近测评</h3><div><span>概率基础诊断</span><strong>86</strong></div><div><span>条件概率小测</span><strong>78</strong></div><div><span>十月综合测评</span><strong>82</strong></div><p>成绩仅作为掌握证据之一，不影响日常学习路径。</p></aside></div>}

    <Modal open={setupOpen} onClose={() => setSetupOpen(false)} title="生成一组针对练习"><div className="form-stack"><label>练习范围<select><option>贝叶斯定理 · 当前薄弱点</option><option>概率论 · 第四章</option><option>数据结构 · 递归与分治</option></select></label><div className="form-grid"><label>题目数量<select><option>5 题</option><option>10 题</option><option>20 题</option></select></label><label>难度<select><option>自适应</option><option>基础</option><option>进阶</option><option>综合</option></select></label></div><fieldset className="question-type-field"><legend>题型覆盖</legend><label><input type="checkbox" defaultChecked />单选 choice</label><label><input type="checkbox" defaultChecked />填空 fill</label><label><input type="checkbox" defaultChecked />简答 short</label><label><input type="checkbox" defaultChecked />论述 essay</label></fieldset><label>反馈方式<select><option>每题提交后反馈</option><option>全部完成后反馈</option><option>只给提示，不显示答案</option></select></label><Button onClick={() => { setSetupOpen(false); restart(); notify('已生成 3 道演示变式题') }}>开始练习</Button></div></Modal>
  </div>
}

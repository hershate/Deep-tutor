import { useState } from 'react'
import { ArrowRight, CheckCircle2, FileText, Lightbulb, PenLine, RotateCcw } from 'lucide-react'
import { Badge, Button, ProgressBar } from './UI'

const questions = [
  { type: 'fill', label: '填空题', question: '在贝叶斯定理中，看到新证据之前对事件 A 的概率判断称为 ______。', answer: '先验概率', hint: '它对应符号 P(A)。', explanation: '先验概率 P(A) 是引入证据 B 之前的判断；结合证据后得到后验概率 P(A|B)。' },
  { type: 'short', label: '简答题', question: '用自己的话解释：为什么低患病率会让阳性结果中包含更多假阳性？', answer: '健康人群基数', hint: '比较患者与健康人两组的数量。', explanation: '优质回答应同时提到健康人群基数大、假阳性数量与真阳性数量的相对比较。' },
  { type: 'essay', label: '论述题', question: '结合一个真实决策场景，论述“新证据很强时，为什么仍然不能忽略先验概率”。', answer: '论证结构', hint: '选择医学筛查、垃圾邮件或质量检测之一。', explanation: '论述应包含场景、先验、证据、假阳性／假阴性代价以及最终决策，而不是只复述公式。' },
]

export default function SubjectivePractice({ notify }: { notify: (message: string) => void }) {
  const [index, setIndex] = useState(0)
  const [answer, setAnswer] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [hint, setHint] = useState(false)
  const question = questions[index]
  return <section className="surface subjective-practice"><div className="question-top"><div><Badge tone="blue">{question.label}</Badge><span>题源：当前单元「检查理解」</span></div><span>{index + 1} / {questions.length}</span></div><ProgressBar value={(index + (submitted ? 1 : 0)) / questions.length * 100} /><div className="subjective-body"><PenLine size={24} /><h2>{question.question}</h2><textarea rows={question.type === 'essay' ? 9 : 5} value={answer} onChange={e => setAnswer(e.target.value)} disabled={submitted} placeholder={question.type === 'fill' ? '填写一个准确术语…' : '先组织自己的回答，再查看评价要点…'} />{hint && !submitted && <div className="hint-box"><Lightbulb size={17} /><p>{question.hint}</p></div>}{submitted && <div className="subjective-rubric"><div><CheckCircle2 size={20} /><h3>已完成作答，以下是要点反馈</h3></div><p>{question.explanation}</p><div><span>内容覆盖</span><strong>{answer.length > 30 ? '完整' : '可补充'}</strong></div><div><span>参考答案关键词</span><strong>{question.answer}</strong></div><small>当前为前端演示评价，真实服务端判分应返回分项得分与依据。</small></div>}</div><div className="question-actions"><Button variant="ghost" onClick={() => setHint(!hint)} disabled={submitted}><Lightbulb size={16} />{hint ? '收起提示' : '查看提示'}</Button>{!submitted ? <Button onClick={() => { setSubmitted(true); notify('答案已提交，知识点覆盖记录已更新') }} disabled={!answer.trim()}>提交答案<ArrowRight size={16} /></Button> : <Button onClick={() => { if (index < questions.length - 1) { setIndex(index + 1); setAnswer(''); setSubmitted(false); setHint(false) } else { setIndex(0); setAnswer(''); setSubmitted(false); setHint(false) } }}>{index < questions.length - 1 ? '下一题型' : '重新练习'}<RotateCcw size={16} /></Button>}</div></section>
}

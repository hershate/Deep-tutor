import { useEffect, useRef, useState } from 'react'
import { ArrowRight, CheckCircle2, ChevronRight, CircleAlert, Clock3, FileQuestion, RefreshCw } from 'lucide-react'
import { Badge, Button, ConfirmDialog, Modal, ProgressBar, SectionHeader } from './UI'
import { backend, backendErrorMessage, onBackendEvent, reportBackendError, isCancelRace, type LearningPlan, deltaThrottle } from '../backend/client'
import { clampToBytes } from '../limits'
import { practiceBackend, type PracticeDetail, type PracticeInput, type PracticeSet, type QuestionKind } from '../backend/practice'
import { statusName } from '../status'

type Props = {
  detail: PracticeDetail | null
  history: PracticeSet[]
  plans: LearningPlan[]
  generationOutput: string
  onUpdated: (detail: PracticeDetail) => void
  onGenerated: (detail: PracticeDetail) => void
  notify: (message: string) => void
}

const kindNames: Record<QuestionKind, string> = { choice: '单选', fill: '填空', short: '简答', essay: '论述' }
const initialInput: PracticeInput = { planId: '', stageId: null, count: 5, difficulty: 'comprehensive', feedbackMode: 'deferred', kinds: ['choice', 'fill', 'short', 'essay'], mode: 'exam', timeLimitMinutes: 45, passScorePercent: 80 }

export default function ExamWorkspace({ detail, history, plans, generationOutput, onUpdated, onGenerated, notify }: Props) {
  const [index, setIndex] = useState(0)
  // 28#2：草稿与选项按题暂存——翻题不再静默丢弃未保存作答（论述长文一次误点即丢）
  const [selected, setSelected] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const draftByQuestion = useRef<Record<number, string>>({})
  const selectedByQuestion = useRef<Record<number, number>>({})
  const [busy, setBusy] = useState(false)
  const [setupOpen, setSetupOpen] = useState(false)
  const [input, setInput] = useState<PracticeInput>(initialInput)
  const [output, setOutput] = useState('')
  const [seconds, setSeconds] = useState(0)
  const exam = detail?.set.mode === 'exam' ? detail : null
  const question = exam?.questions[index]
  const answer = question ? exam?.answers.find(item => item.questionId === question.id) : undefined
  const recorded = question ? exam?.answeredQuestionIds.includes(question.id) ?? false : false
  const plan = exam ? plans.find(item => item.id === exam.set.planId) : undefined
  const selectedPlan = plans.find(item => item.id === input.planId)
  const pending = exam?.answers.some(item => ['grading', 'cancelling'].includes(item.status)) ?? false
  const activeRequest = exam && ['generating', 'cancelling'].includes(exam.set.status) ? exam.set.requestId : answer && ['grading', 'cancelling'].includes(answer.status) ? answer.requestId : null
  const completed = exam?.set.status === 'completed'
  const selectedValue = selected ?? (typeof answer?.response === 'number' ? answer.response : null)
  const percentage = exam && (exam.set.maxScore ?? 0) > 0 ? (exam.set.score ?? 0) / (exam.set.maxScore ?? 1) * 100 : 0
  const passed = percentage >= (exam?.set.passScorePercent ?? 80)

  useEffect(() => {
    if (!input.planId && plans[0]) setInput(previous => ({ ...previous, planId: plans[0].id }))
  }, [plans, input.planId])
  useEffect(() => {
    if (!exam) return
    const first = exam.questions.findIndex(item => !exam.answeredQuestionIds.includes(item.id))
    setIndex(Math.max(0, first))
    // 28#9：切换试卷（考试历史）显式清空上一份的选中项/草稿——两次首题
    // 序号都是 0 时 [index] effect 不触发，旧卷选择会高亮到新卷首题
    setSelected(null)
    setDraft('')
  }, [exam?.set.id])
  // 28#2：切换题目时换入该题的暂存内容（无则清空）
  useEffect(() => {
    setSelected(question ? selectedByQuestion.current[question.id] ?? null : null)
    setDraft(question ? draftByQuestion.current[question.id] ?? '' : '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, exam?.set.id])
  // 28#3：倒计时归零立即本地判定过期——不再停留"剩余 00:00 但仍可作答"
  // 的矛盾态（后端拒绝仍是最终守卫，此处让界面先行一致）
  const [deadlinePassed, setDeadlinePassed] = useState(false)
  const expired = exam?.examExpired === true || deadlinePassed
  useEffect(() => {
    if (!exam?.set.examDeadline || exam.set.status !== 'ready') { setSeconds(0); setDeadlinePassed(false); return }
    const deadline = exam.set.examDeadline
    const update = () => {
      const remaining = Math.max(0, Math.ceil((Date.parse(deadline) - Date.now()) / 1000))
      setSeconds(remaining)
      setDeadlinePassed(remaining === 0)
    }
    update()
    const timer = window.setInterval(update, 1000)
    return () => window.clearInterval(timer)
  }, [exam?.set.examDeadline, exam?.set.status])
  useEffect(() => {
    if (!activeRequest) { setOutput(''); return }
    const requestId = activeRequest
    let active = true
    const update = () => {
      void backend.getProviderChat(requestId).then(request => { if (active) setOutput(request.output) }).catch(error => {
        if (typeof error !== 'object' || error === null || !('code' in error) || error.code !== 'not_found') reportBackendError(error)
      })
    }
    update()
    let unlisten: (() => void) | undefined
    // failover 后 delta 携带 {requestId}-fN 上游 id：记录 route_attempt 映射再对齐逻辑 id
    const upstreams = new Map<string, string>()
    // 每条 delta 不再各拉一次全量输出：150ms 窗口内合并为一次
    const throttledUpdate = deltaThrottle()
    const listening = onBackendEvent(event => {
      if (event.kind === 'provider.route_attempt' && typeof event.payload.requestId === 'string' && typeof event.payload.upstreamRequestId === 'string' && event.payload.requestId === requestId) upstreams.set(event.payload.upstreamRequestId, requestId)
      if (event.kind === 'provider.chat_delta' && typeof event.payload.requestId === 'string' && (event.payload.requestId === requestId || upstreams.get(event.payload.requestId) === requestId)) throttledUpdate(update)
    })
    listening.then(stop => { if (active) unlisten = stop; else stop() }).catch(reportBackendError)
    // 28#5：卸载早于 listen() promise resolve 时立即注销，不再把 stop 存入死闭包
    return () => { active = false; unlisten?.(); void listening.then(stop => { if (!active) stop() }) }
  }, [activeRequest])

  const generate = async () => {
    if (busy || !input.planId || !input.kinds.length) return
    setBusy(true)
    try {
      onGenerated(await practiceBackend.generate(input))
      setSetupOpen(false)
      notify('模拟考试已开始生成，开考时间将在题目就绪后保存')
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const cancelGeneration = async () => {
    if (!exam) return
    try { onUpdated(await practiceBackend.cancel(exam.set.id)) } catch (error) { if (!isCancelRace(error)) reportBackendError(error) }
  }
  const resumeGeneration = async () => {
    if (!exam) return
    try { onUpdated(await practiceBackend.resume(exam.set.id)) } catch (error) { reportBackendError(error) }
  }
  const submit = async () => {
    if (!exam || !question || recorded || exam.examExpired || busy) return
    const answer = question.kind === 'choice' ? selected : draft.trim()
    if (answer === null || answer === '') return
    setBusy(true)
    try {
      await practiceBackend.submit(exam.set.id, question.id, answer)
      onUpdated(await practiceBackend.detail(exam.set.id))
      setSelected(null); setDraft('')
      notify('答案已保存，交卷后统一显示反馈')
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  // 28#11：交卷不可逆（未作答按 0 分计并写入证据）——确认后再执行
  const [finalizeOpen, setFinalizeOpen] = useState(false)
  const finalize = async () => {
    if (!exam || pending || busy) return
    setBusy(true)
    try {
      const result = await practiceBackend.submitExam(exam.set.id)
      onUpdated(result)
      notify(`已交卷，实际得分 ${result.set.score ?? '—'} / ${result.set.maxScore ?? '—'}`)
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const cancelAnswer = async () => {
    if (!answer) return
    try { onUpdated(await practiceBackend.cancelAnswer(answer.id)) } catch (error) { if (!isCancelRace(error)) reportBackendError(error) }
  }
  const resumeAnswer = async () => {
    if (!answer) return
    try { onUpdated(await practiceBackend.resumeAnswer(answer.id)) } catch (error) { reportBackendError(error) }
  }
  const selectSet = async (id: number) => {
    try { onGenerated(await practiceBackend.detail(id)) } catch (error) { reportBackendError(error) }
  }
  const time = `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`

  const intro = <section className="surface exam-intro"><Badge tone="blue">阶段测评</Badge><h2>创建一份真实模拟考试</h2><p>按已采用学习方案与资料生成试题。题目就绪时才开始计时，交卷后统一查看答案、评分和知识证据。</p><div className="exam-facts"><div><FileQuestion size={20} /><strong>5–20 题</strong><span>四种题型可组合</span></div><div><Clock3 size={20} /><strong>1–240 分钟</strong><span>绝对截止时间，重启不重置</span></div></div><p className="form-help">交卷时未作答按 0 分计，并明确标记为未作答；正在评分的作答不会伪造为完成。</p><Button size="lg" onClick={() => setSetupOpen(true)} disabled={!plans.length}>配置模拟考试<ArrowRight size={17} /></Button></section>

  return <div className="exam-layout"><div>
    {!exam ? intro : ['generating', 'cancelling'].includes(exam.set.status) ? <section className="surface exam-intro"><h2>{exam.set.status === 'cancelling' ? '正在取消试卷生成' : '正在生成真实试卷'}</h2><p>{plan?.goal ?? exam.set.planId} · 第 {exam.set.attempt} 次尝试</p><pre className="streaming-output">{output || generationOutput || '等待模型返回试题…'}</pre><Button variant="secondary" onClick={() => void cancelGeneration()} disabled={exam.set.status === 'cancelling'}>取消生成</Button></section>
    : ['failed', 'cancelled', 'interrupted'].includes(exam.set.status) ? <section className="surface exam-intro"><CircleAlert size={30} /><h2>试卷生成未完成</h2><p>{exam.set.error ? backendErrorMessage(exam.set.error) : '生成已停止'}</p><Button onClick={() => void resumeGeneration()}>恢复生成<RefreshCw size={17} /></Button></section>
    : <section className="surface question-surface">
      <div className="question-top"><div><Badge tone="blue">模拟考试</Badge><span>{plan?.goal ?? exam.set.planId}</span></div><span>{completed ? '已交卷' : `剩余 ${time}`}</span></div>
      {completed && <div className="practice-result"><span className="result-mark"><CheckCircle2 size={34} /></span><h2>{exam.set.score ?? '—'} / {exam.set.maxScore ?? '—'}</h2><p>正确率 {Math.round(percentage)}% · 建议通过线 {exam.set.passScorePercent}%</p><Badge tone={passed ? 'green' : 'amber'}>{passed ? '达到建议分数线' : '未达到建议分数线'}</Badge><p>{passed ? '成绩已保存，可作为知识掌握证据之一。' : '可查看逐题反馈，再通过变式练习巩固。'}</p></div>}
      {expired && !completed && <div className="form-error"><Clock3 size={18} />考试时间已结束，不能继续作答；请提交已保存的作答并交卷。</div>}
      {question ? <>
        <div className="question-top"><div><Badge tone="neutral">{kindNames[question.kind]}</Badge><span>第 {index + 1} / {exam.questions.length} 题 · 难度 {question.difficulty} / 5</span></div><span>已判分 {exam.set.answeredQuestions} / {exam.set.totalQuestions}</span></div>
        <ProgressBar value={exam.set.answeredQuestions / exam.set.totalQuestions * 100} />
        <div className="question-body"><h2>{question.prompt}</h2>
          {question.kind === 'choice' && <div className="option-list">{question.options.map((option, optionIndex) => {
            const correct = completed && answer?.feedback.answer === optionIndex
            const wrong = completed && selectedValue === optionIndex && answer?.correct === false
            return <button key={`${option}-${optionIndex}`} disabled={recorded || busy || expired} className={`${selectedValue === optionIndex ? 'selected' : ''} ${correct ? 'correct' : ''} ${wrong ? 'wrong' : ''}`} onClick={() => { if (question) selectedByQuestion.current[question.id] = optionIndex; setSelected(optionIndex) }}><span>{String.fromCharCode(65 + optionIndex)}</span><strong>{option}</strong></button>
          })}</div>}
          {question.kind === 'fill' && <input className="practice-fill-input" value={draft} disabled={recorded || busy || expired} onChange={event => { if (question) draftByQuestion.current[question.id] = event.target.value; setDraft(event.target.value) }} placeholder="填写准确术语" />}
          {['short', 'essay'].includes(question.kind) && <textarea rows={question.kind === 'essay' ? 8 : 5} value={recorded && typeof answer?.response === 'string' ? answer.response : draft} disabled={recorded || busy || expired} onChange={event => { const next = clampToBytes(event.target.value, 30000); if (question) draftByQuestion.current[question.id] = next; setDraft(next) }} placeholder="组织自己的回答，交卷后由后端按评分要点评分" />}
          {question.rubric.length > 0 && <div className="rubric-preview"><strong>评分要点</strong>{question.rubric.map(criterion => <div key={criterion.id}><span>{criterion.description}</span><small>{criterion.points.toFixed(2)} 分</small></div>)}</div>}
          {recorded && !answer && <div className="answer-feedback"><h3>答案已保存</h3><p>交卷后统一显示得分与解析。</p></div>}
          {answer && ['grading', 'cancelling'].includes(answer.status) && <div className="answer-feedback"><h3>正在按评分要点判分</h3><pre className="streaming-output">{output || '作答已保存，等待评分模型输出…'}</pre><Button variant="secondary" onClick={() => void cancelAnswer()} disabled={answer.status === 'cancelling'}>取消评分</Button></div>}
          {answer && ['failed', 'cancelled', 'interrupted'].includes(answer.status) && <div className="answer-feedback is-wrong"><h3>评分未完成</h3><p>{answer.error ? backendErrorMessage(answer.error) : '评分已停止'}</p><Button onClick={() => void resumeAnswer()}>恢复评分<RefreshCw size={17} /></Button></div>}
          {completed && answer && <div className="subjective-rubric"><div><CheckCircle2 size={20} /><h3>{answer.feedback.unanswered ? '未作答，按 0 分计' : answer.correct ? '达到本题掌握阈值' : '本题仍需巩固'}</h3>{answer.score !== null && <strong>{answer.score.toFixed(2)} / {answer.maxScore?.toFixed(2)}</strong>}</div>
            {answer.feedback.overallFeedback && <p>{answer.feedback.overallFeedback}</p>}
            {answer.feedback.criteria?.map(criterion => <div className="rubric-result" key={criterion.id}><span>{criterion.description}</span><strong>{criterion.score.toFixed(2)} / {criterion.maxScore.toFixed(2)}</strong><p>{criterion.feedback}</p><small>评分依据：{criterion.evidence}</small></div>)}
            {answer.feedback.answer !== undefined && <p>参考答案：{question.kind === 'choice' && typeof answer.feedback.answer === 'number' ? question.options[answer.feedback.answer] : answer.feedback.answer}</p>}
            {answer.feedback.explanation && <p>{answer.feedback.explanation}</p>}
          </div>}
          <div className="practice-citations"><strong>资料依据</strong>          {question.citations.map((citation, citationIndex) => <p key={`${citation.materialId}-${citationIndex}`}>资料 #{citation.materialId}：{citation.quote}</p>)}</div>
        </div>
        <div className="question-actions">{!completed && <Button onClick={() => void submit()} disabled={recorded || busy || expired || (question.kind === 'choice' ? selected === null : !draft.trim())}>保存答案<ArrowRight size={17} /></Button>}<Button variant="secondary" onClick={() => setIndex(Math.min(index + 1, exam.questions.length - 1))} disabled={index === exam.questions.length - 1}>下一题<ChevronRight size={17} /></Button></div>
      </> : <p>试卷尚未生成题目。</p>}
      {!completed && <div className="editor-actions"><p className="form-help">交卷后统一显示评分。未作答按 0 分计；未完成的评分不会被伪造。</p><Button onClick={() => setFinalizeOpen(true)} disabled={pending || busy}>{pending ? '等待评分完成' : '交卷并查看结果'}<CheckCircle2 size={17} /></Button></div>}
    </section>}
  </div><aside className="surface exam-history"><SectionHeader title="真实测评历史" description="成绩来自已保存的交卷结果" />{history.filter(item => item.mode === 'exam').map(item => <button key={item.id} onClick={() => void selectSet(item.id)}><span>{plans.find(plan => plan.id === item.planId)?.goal ?? item.planId}<small>{item.completedAt ? new Date(item.completedAt).toLocaleString() : statusName(item.status)}</small></span><strong>{item.status === 'completed' && item.score !== null && item.maxScore !== null ? `${item.score} / ${item.maxScore}` : `${item.answeredQuestions} / ${item.totalQuestions}`}</strong></button>)}</aside>

    <ConfirmDialog open={finalizeOpen} onClose={() => setFinalizeOpen(false)} onConfirm={() => { setFinalizeOpen(false); void finalize() }} title="交卷并查看结果？" description={`交卷后不可重考：未作答的题按 0 分计并写入知识证据；当前已判分 ${exam?.set.answeredQuestions ?? 0} / ${exam?.set.totalQuestions ?? 0} 题。`} confirmLabel="确认交卷" busy={busy} />
    <Modal open={setupOpen} onClose={() => setSetupOpen(false)} title="配置模拟考试"><div className="form-stack"><label>学习方案<select value={input.planId} onChange={event => setInput({ ...input, planId: event.target.value, stageId: null })}><option value="" disabled>选择已采用的方案</option>{plans.map(plan => <option key={plan.id} value={plan.id}>{plan.goal}</option>)}</select></label><label>考试范围<select value={input.stageId ?? ''} onChange={event => setInput({ ...input, stageId: event.target.value || null })}><option value="">整个学习方案</option>{selectedPlan?.result?.stages.map(stage => <option key={stage.id} value={stage.id}>{stage.title}</option>)}</select></label><div className="form-grid"><label>题目数量<select value={input.count} onChange={event => setInput({ ...input, count: Number(event.target.value) })}>{[5, 10, 20].map(count => <option value={count} key={count}>{count} 题</option>)}</select></label><label>考试时长（分钟）<input type="number" min={1} max={240} value={input.timeLimitMinutes} onChange={event => setInput({ ...input, timeLimitMinutes: Math.min(240, Math.max(1, Number(event.target.value) || 1)) })} /></label></div><label>建议通过百分比<input type="number" min={1} max={100} value={input.passScorePercent} onChange={event => setInput({ ...input, passScorePercent: Math.min(100, Math.max(1, Number(event.target.value) || 1)) })} /></label><fieldset className="question-type-field"><legend>题型覆盖</legend>{(['choice', 'fill', 'short', 'essay'] as const).map(kind => <label key={kind}><input type="checkbox" checked={input.kinds.includes(kind)} onChange={event => setInput({ ...input, kinds: event.target.checked ? [...input.kinds, kind] : input.kinds.filter(item => item !== kind) })} />{kindNames[kind]}</label>)}</fieldset><p className="form-help">题目就绪后开始计时。交卷前统一隐藏答案；未作答按 0 分计，正在评分的作答需完成后才能交卷。</p><Button onClick={() => void generate()} disabled={busy || !input.planId || !input.kinds.length}>生成模拟考试<ArrowRight size={17} /></Button></div></Modal>
  </div>
}

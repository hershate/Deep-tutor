import { useEffect, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import { ArrowRight, CheckCircle2, CircleAlert, Lightbulb, PenLine, RefreshCw, SlidersHorizontal } from 'lucide-react'
import { Badge, Button, Modal, ProgressBar, SectionHeader } from './UI'
import { backend, backendErrorMessage, onBackendEvent, reportBackendError, type LearningPlan, isCancelRace, deltaThrottle } from '../backend/client'
import { practiceBackend, type PracticeDetail, type PracticeInput, type PracticeSet } from '../backend/practice'
import { statusName } from '../status'
import { clampToBytes } from '../limits'

type Props = {
  detail: PracticeDetail | null
  history: PracticeSet[]
  plans: LearningPlan[]
  onUpdated: (detail: PracticeDetail) => void
  onGenerated: (detail: PracticeDetail) => void
  onShowObjective: () => void
  notify: (message: string) => void
  /** 76#3：草稿映射由父组件持有——切 tab 卸载不丢未保存作答 */
  draftByQuestion: MutableRefObject<Record<number, string>>
}

export default function SubjectivePractice({ detail, history, plans, onUpdated, onGenerated, onShowObjective, notify, draftByQuestion }: Props) {
  const [index, setIndex] = useState(0)
  // 28#2：草稿按题暂存——翻题不再静默丢弃未提交的长文作答
  const [draft, setDraft] = useState('')
  // 76#2/76#3：草稿由父组件持有 + 历史切换序号守卫（同 ExamWorkspace）
  const [hint, setHint] = useState(false)
  const [busy, setBusy] = useState(false)
  const [output, setOutput] = useState('')
  const [setupOpen, setSetupOpen] = useState(false)
  const [input, setInput] = useState<PracticeInput>({ planId: '', stageId: null, count: 2, difficulty: 'adaptive', feedbackMode: 'immediate', kinds: ['short', 'essay'] })
  const questions = detail?.questions.filter(question => ['short', 'essay'].includes(question.kind)) ?? []
  const question = questions[index]
  const answer = question ? detail?.answers.find(item => item.questionId === question.id) : undefined
  const recorded = question ? detail?.answeredQuestionIds.includes(question.id) ?? false : false
  const plan = detail ? plans.find(item => item.id === detail.set.planId) : undefined
  const selectedPlan = plans.find(item => item.id === input.planId)
  const setId = detail?.set.id

  useEffect(() => {
    if (!input.planId && plans[0]) setInput(previous => ({ ...previous, planId: plans[0].id }))
  }, [plans, input.planId])
  useEffect(() => {
    if (!setId) return
    const nextIndex = questions.findIndex(item => !detail?.answeredQuestionIds.includes(item.id))
    setIndex(Math.max(0, nextIndex))
    setDraft('')
    setHint(false)
  }, [setId])
  useEffect(() => {
    // 28#2：切换题目换入该题暂存草稿（无则清空）
    setDraft(question ? draftByQuestion.current[question.id] ?? '' : '')
    setHint(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, setId])
  useEffect(() => {
    if (question && index >= questions.length) setIndex(Math.max(0, questions.length - 1))
  }, [questions.length, index, question])
  useEffect(() => {
    const requestId = answer?.requestId
    if (!requestId || !answer || !['grading', 'cancelling'].includes(answer.status)) {
      setOutput('')
      return
    }
    let active = true
    const update = () => {
      void backend.getProviderChat(requestId).then(request => {
        if (active) setOutput(request.output)
      }).catch(error => {
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
    // 28#5：卸载早于 listen() promise resolve 时立即注销
    return () => { active = false; unlisten?.(); void listening.then(stop => { if (!active) stop() }) }
      // 76#7：卸载取消武装中的定时器
      throttledUpdate.cancel()
  }, [answer?.requestId, answer?.status])

  const submit = async () => {
    if (!detail || !question || recorded || busy || !draft.trim()) return
    setBusy(true)
    try {
      await practiceBackend.submit(detail.set.id, question.id, draft.trim())
      onUpdated(await practiceBackend.detail(detail.set.id))
      setDraft('')
      notify('作答已保存，原生后端正在按评分要点判分')
    } catch (error) { reportBackendError(error, 'write') } finally { setBusy(false) }
  }
  const cancel = async () => {
    if (!answer || busy) return
    setBusy(true)
    try {
      onUpdated(await practiceBackend.cancelAnswer(answer.id))
      notify('已请求取消评分，原始作答仍然保留')
    } catch (error) { if (!isCancelRace(error)) reportBackendError(error) } finally { setBusy(false) }
  }
  const resume = async () => {
    if (!answer || busy) return
    setBusy(true)
    try {
      onUpdated(await practiceBackend.resumeAnswer(answer.id))
      notify('已从保存的作答恢复评分')
    } catch (error) { reportBackendError(error, 'write') } finally { setBusy(false) }
  }
  const generate = async () => {
    if (busy || !input.planId) return
    setBusy(true)
    try {
      onGenerated(await practiceBackend.generate(input))
      setSetupOpen(false)
      notify('简答题与论述题正在由真实模型生成')
    } catch (error) { reportBackendError(error, 'write') } finally { setBusy(false) }
  }
  // 76#2：历史切换序号+busy 守卫——慢响应后到不再覆盖已选组（同 ExamWorkspace）
  const selectSeq = useRef(0)
  const [selecting, setSelecting] = useState(false)
  const selectSet = async (id: number) => {
    if (busy || selecting) return
    const ticket = ++selectSeq.current
    setSelecting(true)
    try {
      const next = await practiceBackend.detail(id)
      if (ticket === selectSeq.current) onGenerated(next)
    } catch (error) { reportBackendError(error) } finally { setSelecting(false) }
  }

  const body = question && detail ? <>
    <div className="question-top"><div><Badge tone="blue">{question.kind === 'short' ? '简答题' : '论述题'}</Badge><span>{plan?.goal ?? detail.set.planId}</span></div><span>{index + 1} / {questions.length}</span></div>
    <ProgressBar value={questions.filter(item => detail.answeredQuestionIds.includes(item.id)).length / questions.length * 100} />
    <div className="subjective-body"><PenLine size={24} /><h2>{question.prompt}</h2>
      <div className="rubric-preview"><strong>评分要点（合计 1 分）</strong>{question.rubric.map(criterion => <div key={criterion.id}><span>{criterion.description}</span><small>{criterion.points.toFixed(2)} 分</small></div>)}</div>
      <textarea rows={question.kind === 'essay' ? 9 : 5} value={recorded && typeof answer?.response === 'string' ? answer.response : draft} onChange={event => { const next = clampToBytes(event.target.value, 30000); if (question) draftByQuestion.current[question.id] = next; setDraft(next) }} disabled={recorded || busy} placeholder="先组织自己的回答，再由后端按评分要点评价…" />
      {hint && !recorded && <div className="hint-box"><Lightbulb size={17} /><p>{question.hint}</p></div>}
      {answer && ['grading', 'cancelling'].includes(answer.status) && <div className="answer-feedback" aria-live="polite"><h3>{answer.status === 'cancelling' ? '正在取消评分' : '正在按评分要点判分'}</h3><pre className="streaming-output">{output || '作答已保存，等待评分模型输出…'}</pre><Button variant="secondary" onClick={() => void cancel()} disabled={busy || answer.status === 'cancelling'}>取消评分</Button></div>}
      {answer && ['failed', 'cancelled', 'interrupted'].includes(answer.status) && <div className="answer-feedback is-wrong"><CircleAlert size={21} /><h3>评分尚未完成</h3><p>{answer.error ? backendErrorMessage(answer.error) : '评分任务已停止'}</p><p>原始作答已保留，恢复不会重复提交答案。</p><Button onClick={() => void resume()} disabled={busy}>恢复评分<RefreshCw size={17} /></Button></div>}
      {recorded && !answer && <div className="answer-feedback"><h3>答案已保存</h3><p>本组全部题目判分完成后统一显示得分、要点与解析。</p></div>}
      {answer?.status === 'done' && <div className="subjective-rubric"><div><CheckCircle2 size={20} /><h3>{answer.score === null ? '评分已保存，按当前设置仅显示提示' : answer.correct ? '达到本题掌握阈值' : '本题仍需巩固'}</h3>{answer.score !== null && <strong>{answer.score.toFixed(2)} / {answer.maxScore?.toFixed(2)}</strong>}</div>
        {answer.feedback.overallFeedback && <p>{answer.feedback.overallFeedback}</p>}
        {answer.feedback.criteria?.map(criterion => <div className="rubric-result" key={criterion.id}><span>{criterion.description}</span><strong>{criterion.score.toFixed(2)} / {criterion.maxScore.toFixed(2)}</strong><p>{criterion.feedback}</p><small>评分依据：{criterion.evidence}</small></div>)}
        {answer.feedback.answer !== undefined && <div><span>参考答案</span><p>{answer.feedback.answer}</p></div>}
        {answer.feedback.explanation && <p>{answer.feedback.explanation}</p>}
        {answer.feedback.hint && <p>{answer.feedback.hint}</p>}
        {(answer.feedback.citations ?? question.citations).map((citation, citationIndex) => <small key={`${citation.materialId}-${citationIndex}`}>资料 #{citation.materialId}：{citation.quote}</small>)}
      </div>}
      <div className="practice-citations"><strong>资料依据</strong>{question.citations.map((citation, citationIndex) => <p key={`${citation.materialId}-${citationIndex}`}>资料 #{citation.materialId}：{citation.quote}</p>)}</div>
    </div>
    <div className="question-actions"><Button variant="ghost" onClick={() => setHint(!hint)} disabled={recorded}><Lightbulb size={16} />{hint ? '收起提示' : '查看提示'}</Button>{!recorded ? <Button onClick={() => void submit()} disabled={busy || !draft.trim()}>提交并真实评分<ArrowRight size={16} /></Button> : <Button onClick={() => setIndex(Math.min(index + 1, questions.length - 1))} disabled={index === questions.length - 1}>下一题<ArrowRight size={16} /></Button>}</div>
  </> : <div className="practice-result"><span className="result-mark"><PenLine size={32} /></span><h2>从真实资料生成主观题</h2><p>{plans.length ? '每次生成 2 道主观题，默认简答与论述各 1 道（以模型实际生成为准）。作答由后端按题目保存的评分要点逐项判分。' : '请先创建并采用一份包含资料的学习方案。'}</p>{plans.length > 0 && <Button onClick={() => setSetupOpen(true)}>生成主观题<ArrowRight size={17} /></Button>}</div>

  return <div className="practice-layout"><section className="surface subjective-practice"><SectionHeader title="简答与论述" description="分项得分、评分依据和资料引用都来自真实后端评分结果。" action={<Button size="sm" onClick={() => setSetupOpen(true)} disabled={!plans.length}><SlidersHorizontal size={16} />生成主观题</Button>} />
    {detail?.set.status === 'completed' && <div className="connection-summary"><CheckCircle2 size={21} /><div><h3>本组已全部判分</h3><p>{detail.set.score === null || detail.set.maxScore === null ? '提示模式：不公布总分；各题反馈可在下方查看。' : `总分 ${detail.set.score} / ${detail.set.maxScore}；各题反馈可在下方查看。`}</p></div></div>}
    {detail && detail.questions.some(item => ['choice', 'fill'].includes(item.kind)) && <Button variant="ghost" onClick={onShowObjective}>本组还包含客观题，去针对练习作答<ArrowRight size={16} /></Button>}
    {body}
  </section><aside className="practice-side"><section className="surface"><SectionHeader title="主观题历史" description="点击恢复保存的作答与评分" /><div className="practice-history-list">{history.filter(item => item.kinds.some(kind => ['short', 'essay'].includes(kind))).map(item => <button key={item.id} disabled={busy || selecting} onClick={() => void selectSet(item.id)}><span>{plans.find(plan => plan.id === item.planId)?.goal ?? item.planId}<small>第 {item.attempt} 次尝试 · {statusName(item.status)}</small></span><strong>{item.status === 'completed' && item.score !== null && item.maxScore !== null ? `${item.score} / ${item.maxScore}` : `${item.answeredQuestions} / ${item.totalQuestions}`}</strong></button>)}</div></section><section className="surface"><SectionHeader title="评分原则" /><p className="side-copy">每题 1 分，按保存的评分要点加权。针对练习默认 80% 为掌握阈值，模拟考试按所配通过线判定；低于阈值的作答进入真实错题记录。模型必须返回资料原文引用，无效评分不会被保存为成绩。</p></section></aside>

    <Modal open={setupOpen} onClose={() => setSetupOpen(false)} title="生成简答与论述题"><div className="form-stack"><label>学习方案<select value={input.planId} onChange={event => setInput({ ...input, planId: event.target.value, stageId: null })}><option value="" disabled>选择已采用的方案</option>{plans.map(plan => <option key={plan.id} value={plan.id}>{plan.goal}</option>)}</select></label><label>练习范围<select value={input.stageId ?? ''} onChange={event => setInput({ ...input, stageId: event.target.value || null })}><option value="">整个学习方案</option>{selectedPlan?.result?.stages.map(stage => <option key={stage.id} value={stage.id}>{stage.title}</option>)}</select></label><label>难度<select value={input.difficulty} onChange={event => setInput({ ...input, difficulty: event.target.value as PracticeInput['difficulty'] })}><option value="adaptive">自适应</option><option value="basic">基础</option><option value="advanced">进阶</option><option value="comprehensive">综合</option></select></label><label>反馈方式<select value={input.feedbackMode} onChange={event => setInput({ ...input, feedbackMode: event.target.value as PracticeInput['feedbackMode'] })}><option value="immediate">每题判分后显示分项反馈</option><option value="deferred">全部判分后统一显示</option><option value="hints">只给提示，不显示标准答案</option></select></label><p className="form-help">默认生成简答与论述各 1 道，每题含 2 至 6 项评分要点。</p><Button onClick={() => void generate()} disabled={busy || !input.planId}>开始真实生成<ArrowRight size={17} /></Button></div></Modal>
  </div>
}

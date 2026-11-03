import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowRight, BookOpenCheck, CheckCircle2, ChevronRight, CircleAlert, Clock3, Lightbulb, RefreshCw, RotateCcw, SlidersHorizontal, Target, XCircle } from 'lucide-react'
import { Badge, Button, Modal, ProgressBar, SectionHeader } from '../components/UI'
import SubjectivePractice from '../components/SubjectivePractice'
import ExamWorkspace from '../components/ExamWorkspace'
import { backend, backendErrorMessage, onBackendEvent, reportBackendError, type LearningPlan } from '../backend/client'
import { practiceBackend, type PracticeDetail, type PracticeInput, type PracticeMistake, type PracticeSet, type PracticeTarget } from '../backend/practice'

type Tab = 'practice' | 'subjective' | 'mistakes' | 'exam'
const emptyInput: PracticeInput = { planId: '', stageId: null, count: 5, difficulty: 'adaptive', feedbackMode: 'immediate', kinds: ['choice', 'fill'] }

export default function Practice({ notify, target }: { notify: (message: string) => void; target?: PracticeTarget | null }) {
  const [tab, setTab] = useState<Tab>('practice')
  const [plans, setPlans] = useState<LearningPlan[]>([])
  const [history, setHistory] = useState<PracticeSet[]>([])
  const [detail, setDetail] = useState<PracticeDetail | null>(null)
  const [mistakes, setMistakes] = useState<PracticeMistake[]>([])
  const [index, setIndex] = useState(0)
  const [selected, setSelected] = useState<number | null>(null)
  const [textAnswer, setTextAnswer] = useState('')
  const [hint, setHint] = useState(false)
  const [busy, setBusy] = useState(false)
  const [setupOpen, setSetupOpen] = useState(false)
  const [input, setInput] = useState<PracticeInput>(emptyInput)
  const [generationOutput, setGenerationOutput] = useState('')
  const currentId = useRef<number | null>(null)
  const requestId = useRef<string | null>(null)

  const refresh = useCallback(async () => {
    const [allPlans, sets, allMistakes] = await Promise.all([
      backend.listLearningPlans(), practiceBackend.history(), practiceBackend.mistakes(),
    ])
    const available = allPlans.filter(plan => plan.adoptedProjectId !== null && plan.result)
    setPlans(available)
    setHistory(sets)
    setMistakes(allMistakes)
    setInput(previous => previous.planId || !available[0] ? previous : { ...previous, planId: available[0].id })
    const id = currentId.current ?? sets[0]?.id ?? null
    currentId.current = id
    if (!id) { setDetail(null); setGenerationOutput(''); return }
    const next = await practiceBackend.detail(id)
    requestId.current = next.set.requestId
    setDetail(next)
    setIndex(previous => Math.min(previous, Math.max(0, next.questions.filter(question => ['choice', 'fill'].includes(question.kind)).length - 1)))
    if (['generating', 'cancelling'].includes(next.set.status) && next.set.requestId) {
      try { setGenerationOutput((await backend.getProviderChat(next.set.requestId)).output) }
      catch (error) {
        if (typeof error !== 'object' || error === null || !('code' in error) || error.code !== 'not_found') throw error
      }
    } else setGenerationOutput('')
  }, [])

  useEffect(() => {
    void refresh().catch(reportBackendError)
    let unlisten: (() => void) | undefined
    const listening = onBackendEvent(event => {
      if (event.kind.startsWith('practice.')) void refresh().catch(reportBackendError)
      if (event.kind === 'provider.chat_delta' && event.payload.requestId === requestId.current) {
        const id = requestId.current
        if (id) void backend.getProviderChat(id).then(request => setGenerationOutput(request.output)).catch(reportBackendError)
      }
    }).then(stop => { unlisten = stop }).catch(reportBackendError)
    return () => { unlisten?.(); void listening }
  }, [refresh])

  useEffect(() => {
    if (!target) return
    setTab('practice')
    setInput(previous => ({ ...previous, planId: target.planId, stageId: target.stageId, nodeId: target.nodeId }))
    setSetupOpen(true)
  }, [target])

  const chooseSet = (next: PracticeDetail) => {
    currentId.current = next.set.id
    requestId.current = next.set.requestId
    setDetail(next)
    const objectiveQuestions = next.questions.filter(question => ['choice', 'fill'].includes(question.kind))
    setIndex(Math.max(0, objectiveQuestions.findIndex(question => !next.answeredQuestionIds.includes(question.id))))
    setSelected(null); setTextAnswer(''); setHint(false)
    setTab(next.set.mode === 'exam' ? 'exam' : next.set.kinds.some(kind => ['short', 'essay'].includes(kind)) ? 'subjective' : 'practice')
  }
  const generate = async () => {
    if (busy || !input.planId || !input.kinds.length) return
    setBusy(true)
    try {
      chooseSet(await practiceBackend.generate(input))
      setSetupOpen(false)
      await refresh()
      notify('练习生成任务已保存，正在等待真实模型输出')
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const cancel = async () => {
    if (!detail) return
    try {
      setDetail(await practiceBackend.cancel(detail.set.id))
      await refresh()
      notify('已请求取消，正在停止模型请求')
    } catch (error) { reportBackendError(error) }
  }
  const resume = async () => {
    if (!detail || busy) return
    setBusy(true)
    try {
      chooseSet(await practiceBackend.resume(detail.set.id))
      await refresh()
      notify('已从保存的任务状态恢复生成')
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const submit = async () => {
    if (!detail || busy) return
    const question = detail.questions[index]
    if (!question || detail.answeredQuestionIds.includes(question.id)) return
    const answer = question.kind === 'choice' ? selected : textAnswer.trim()
    if (answer === null || answer === '') return
    setBusy(true)
    try {
      await practiceBackend.submit(detail.set.id, question.id, answer)
      setDetail(await practiceBackend.detail(detail.set.id))
      await refresh()
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const bookmark = async () => {
    const question = detail?.questions[index]
    if (!question) return
    try {
      await practiceBackend.bookmark(question.id, !question.bookmarked)
      setDetail(await practiceBackend.detail(detail!.set.id))
      notify(question.bookmarked ? '已取消收藏本题' : '已收藏本题、解析与资料依据')
    } catch (error) { reportBackendError(error) }
  }
  const exportMistakes = async () => {
    try {
      const data = await practiceBackend.exportMistakes()
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
      const anchor = document.createElement('a')
      anchor.href = url; anchor.download = 'DeepTutor-错题.json'; document.body.appendChild(anchor); anchor.click(); anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      notify('已从原生后端导出真实错题记录')
    } catch (error) { reportBackendError(error) }
  }
  const setMastered = async (item: PracticeMistake) => {
    try {
      await practiceBackend.masterMistake(item.questionId, !item.mastered)
      setMistakes(await practiceBackend.mistakes())
      notify(item.mastered ? '已恢复为待巩固' : '已保存手动掌握标记')
    } catch (error) { reportBackendError(error) }
  }
  const generateVariant = async (item: PracticeMistake) => {
    if (busy) return
    setBusy(true)
    try {
      chooseSet(await practiceBackend.generate({
        planId: item.planId,
        stageId: item.stageId,
        count: 3,
        difficulty: 'adaptive',
        feedbackMode: 'immediate',
        kinds: [item.kind],
        variantOf: item.questionId,
      }))
      await refresh()
      notify('已根据真实错题创建变式生成任务')
    } catch (error) { reportBackendError(error) } finally { setBusy(false) }
  }
  const selectHistory = async (id: number) => {
    try { chooseSet(await practiceBackend.detail(id)) } catch (error) { reportBackendError(error) }
  }

  const objectiveQuestions = detail?.questions.filter(question => ['choice', 'fill'].includes(question.kind)) ?? []
  const question = objectiveQuestions[index]
  const answered = question ? detail?.answeredQuestionIds.includes(question.id) ?? false : false
  const answer = question ? detail?.answers.find(item => item.questionId === question.id) : undefined
  const plan = detail ? plans.find(item => item.id === detail.set.planId) : undefined
  const nodes = question?.nodeIds.map(id => plan?.result?.graph.nodes.find(node => node.id === id)?.title ?? id) ?? []
  const correctOption = typeof answer?.feedback.answer === 'number' ? answer.feedback.answer : null
  const selectedPlan = plans.find(plan => plan.id === input.planId)

  return <div className="page practice-page">
    <div className="page-heading"><div><h1>练习与测评</h1><p>题目、答案、解析和掌握证据都来自已保存的学习方案与真实模型结果。</p></div><Button onClick={() => setSetupOpen(true)} disabled={!plans.length}><SlidersHorizontal size={17} />生成练习</Button></div>
    <div className="practice-tabs">{([{ id: 'practice', label: '针对练习' }, { id: 'subjective', label: '主观题' }, { id: 'mistakes', label: '错题本' }, { id: 'exam', label: '模拟考试' }] as { id: Tab; label: string }[]).map(item => <button key={item.id} className={tab === item.id ? 'active' : ''} onClick={() => setTab(item.id)}>{item.id === 'practice' ? <Target size={17} /> : item.id === 'mistakes' ? <RotateCcw size={17} /> : <Clock3 size={17} />}{item.label}{item.id === 'mistakes' && <span>{mistakes.filter(item => !item.mastered).length}</span>}</button>)}</div>

    {tab === 'practice' && <div className="practice-layout">
      <section className="surface question-surface">
        {!detail ? <div className="practice-result"><span className="result-mark"><BookOpenCheck size={36} /></span><h2>从真实学习方案生成练习</h2><p>{plans.length ? '选择题与填空题会引用方案中的资料原文，并关联实际知识节点。' : '请先在 AI 方案生成中创建并采用一份包含资料的学习方案。'}</p>{plans.length > 0 && <Button onClick={() => setSetupOpen(true)}>生成第一组练习<ArrowRight size={17} /></Button>}</div>
        : ['generating', 'cancelling'].includes(detail.set.status) ? <div className="practice-result"><span className="result-mark"><RefreshCw size={34} /></span><h2>{detail.set.status === 'cancelling' ? '正在取消生成' : '正在生成练习'}</h2><p>{plan?.goal ?? detail.set.planId} · 第 {detail.set.attempt} 次尝试 · {detail.set.totalQuestions} 题</p><details open><summary>实时模型输出</summary><pre className="streaming-output">{generationOutput || '请求已保存，等待模型返回文本…'}</pre></details><Button variant="secondary" onClick={() => void cancel()} disabled={detail.set.status === 'cancelling'}>取消生成</Button></div>
        : ['failed', 'cancelled', 'interrupted'].includes(detail.set.status) ? <div className="practice-result"><span className="result-mark"><CircleAlert size={34} /></span><h2>练习生成未完成</h2><p>{detail.set.error ? backendErrorMessage(detail.set.error) : '生成任务已停止'}</p><p>已保存的模型结果不会重复请求；需要重试时会使用新的请求标识。</p><Button onClick={() => void resume()} disabled={busy}>恢复生成<RefreshCw size={17} /></Button></div>
        : detail.set.status === 'completed' ? <div className="practice-result"><span className="result-mark"><BookOpenCheck size={36} /></span><p>本次针对练习</p><h2>{detail.set.score} / {detail.set.maxScore}</h2><span>实际得分</span><div className="result-analysis"><div><strong>练习与知识证据已保存</strong><p>共提交 {detail.set.answeredQuestions} 题。逐题答案与资料依据可在下方查看，掌握度同时参考其他学习与复习证据。</p></div><Badge tone={detail.set.maxScore > 0 && detail.set.score === detail.set.maxScore ? 'green' : 'amber'}>正确率 {detail.set.maxScore ? Math.round(detail.set.score / detail.set.maxScore * 100) : 0}%</Badge></div><div className="result-actions"><Button onClick={() => setSetupOpen(true)}><RefreshCw size={17} />生成新练习</Button><Button variant="secondary" onClick={() => setTab('mistakes')}>查看错题本</Button></div><details><summary>查看本组题目与解析</summary><div className="practice-citations">{detail.questions.map((item, questionIndex) => { const result = detail.answers.find(entry => entry.questionId === item.id); return <div key={item.id}><h3>{questionIndex + 1}. {item.prompt}</h3><p>{item.kind === 'choice' ? `正确选项：${typeof result?.feedback.answer === 'number' ? item.options[result.feedback.answer] : '仅提示模式'}` : `参考答案：${result?.feedback.answer ?? '仅提示模式'}`}</p><p>{result?.feedback.explanation ?? result?.feedback.hint}</p></div> })}</div></details></div>
        : question ? <>
          <div className="question-top"><div>{nodes.map(node => <Badge tone="green" key={node}>{node}</Badge>)}<span>{question.kind === 'choice' ? '单选题' : '填空题'} · 难度 {question.difficulty} / 5</span></div><span>第 {index + 1} / {objectiveQuestions.length} 题</span></div>
          <ProgressBar value={detail.set.answeredQuestions / detail.set.totalQuestions * 100} />
          <div className="question-body"><h2>{question.prompt}</h2>{question.kind === 'choice' ? <div className="option-list">{question.options.map((option, optionIndex) => {
            const isCorrect = correctOption === optionIndex
            const isWrong = answered && selected === optionIndex && answer?.correct === false
            return <button key={option} disabled={answered || busy} className={`${selected === optionIndex ? 'selected' : ''} ${isCorrect ? 'correct' : ''} ${isWrong ? 'wrong' : ''}`} onClick={() => setSelected(optionIndex)}><span>{String.fromCharCode(65 + optionIndex)}</span><strong>{option}</strong>{isCorrect && <CheckCircle2 size={20} />}{isWrong && <XCircle size={20} />}</button>
          })}</div> : <input className="practice-fill-input" value={textAnswer} disabled={answered || busy} onChange={event => setTextAnswer(event.target.value)} placeholder="填写资料中的准确术语" />}
          {hint && !answered && <div className="hint-box"><Lightbulb size={18} /><p>{question.hint}</p></div>}
          {answered && answer && <div className={`answer-feedback ${answer.correct === true ? 'is-correct' : answer.correct === false ? 'is-wrong' : ''}`}><div>{answer.correct === true ? <CheckCircle2 size={21} /> : <CircleAlert size={21} />}<h3>{answer.correct === true ? '回答正确' : answer.correct === false ? '回答错误，解析与资料依据如下' : '答案已保存'}</h3></div><p>{answer.feedback.explanation ?? answer.feedback.hint ?? (detail.set.feedbackMode === 'deferred' ? '全部题目提交后统一显示答案与解析。' : '本组只提供提示，不显示标准答案。')}</p></div>}
          {answered && !answer && <div className="answer-feedback"><h3>答案已保存</h3><p>全部题目提交后统一显示答案与解析。</p></div>}
          <div className="practice-citations"><strong>本题资料依据</strong>{question.citations.map(citation => <p key={`${citation.materialId}-${citation.quote}`}>资料 #{citation.materialId}：{citation.quote}</p>)}</div></div>
          <div className="question-actions"><Button variant="ghost" onClick={() => setHint(!hint)} disabled={answered}><Lightbulb size={17} />{hint ? '收起提示' : '只给一点提示'}</Button><Button variant="ghost" onClick={() => void bookmark()}>{question.bookmarked ? '取消收藏' : '收藏解析'}</Button>{!answered ? <Button onClick={() => void submit()} disabled={busy || (question.kind === 'choice' ? selected === null : !textAnswer.trim())}>提交答案<ArrowRight size={17} /></Button> : <Button onClick={() => { setIndex(Math.min(index + 1, objectiveQuestions.length - 1)); setSelected(null); setTextAnswer(''); setHint(false) }} disabled={index === objectiveQuestions.length - 1}>下一题<ChevronRight size={17} /></Button>}</div>
        </> : <p>题目尚未写入，请刷新练习状态。</p>}
      </section>
      <aside className="practice-side"><section className="surface"><SectionHeader title="本组依据" description={plan?.goal} /><p className="side-copy">范围：{detail?.set.nodeId ? `节点：${plan?.result?.graph.nodes.find(node => node.id === detail.set.nodeId)?.title ?? detail.set.nodeId}` : detail ? plan?.result?.stages.find(stage => stage.id === detail.set.stageId)?.title ?? '整个方案' : '尚未选择'}。每道题都必须通过原文引用与知识节点校验。</p>{detail && <p className="side-copy">题型：{detail.set.kinds.map(kind => kind === 'choice' ? '单选' : kind === 'fill' ? '填空' : kind === 'short' ? '简答' : '论述').join('、')} · 反馈：{{ immediate: '逐题反馈', deferred: '统一反馈', hints: '只给提示' }[detail.set.feedbackMode]}</p>}</section><section className="surface"><SectionHeader title="真实练习历史" description="点击可恢复未完成的作答" /><div className="practice-history-list">{history.map(item => <button key={item.id} onClick={() => void selectHistory(item.id)}><span>{plans.find(plan => plan.id === item.planId)?.goal ?? item.planId}<small>第 {item.attempt} 次尝试 · {item.status}</small></span><strong>{item.status === 'completed' ? `${item.score} / ${item.maxScore}` : `${item.answeredQuestions} / ${item.totalQuestions}`}</strong></button>)}</div></section></aside>
    </div>}

    {tab === 'subjective' && <SubjectivePractice detail={detail} history={history} plans={plans} onUpdated={setDetail} onGenerated={chooseSet} onSelectSet={id => void selectHistory(id)} onShowObjective={() => setTab('practice')} notify={notify} />}
    {tab === 'mistakes' && <section className="surface mistakes-surface"><SectionHeader title="真实错题与下一步" description="变式达到真实掌握阈值后自动更新原错题；手动标记会单独注明。" action={<Button variant="secondary" size="sm" onClick={() => void exportMistakes()}>导出错题</Button>} /><div className="mistake-list">{!mistakes.length && <p>还没有错题。提交练习后，答错的题目会显示在这里。</p>}{mistakes.map(item => <div className="mistake-row" key={item.questionId}><span className="mistake-count">{item.wrongCount}<small>次错误</small></span><div><Badge tone={item.mastered ? 'green' : 'neutral'}>{item.mastered ? item.masteredQuestionId ? `变式 #${item.masteredQuestionId} 通过` : '手动标记掌握' : item.nodeIds.join('、')}</Badge><h3>{item.prompt}</h3><p>{item.reason} · 最近错误：{new Date(item.lastWrongAt).toLocaleString()}</p></div><div className="mistake-actions"><Button variant="secondary" size="sm" onClick={() => void generateVariant(item)} disabled={busy}>生成变式<ChevronRight size={15} /></Button><button onClick={() => void setMastered(item)}>{item.mastered ? '恢复待巩固' : '标记掌握'}</button></div></div>)}</div></section>}
    {tab === 'exam' && <ExamWorkspace detail={detail} history={history} plans={plans} generationOutput={generationOutput} onUpdated={setDetail} onGenerated={chooseSet} onSelectSet={id => void selectHistory(id)} notify={notify} />}

    <Modal open={setupOpen} onClose={() => setSetupOpen(false)} title="生成一组针对练习"><div className="form-stack"><label>学习方案<select value={input.planId} onChange={event => setInput({ ...input, planId: event.target.value, stageId: null, nodeId: null })}><option value="" disabled>选择已采用的方案</option>{plans.map(plan => <option key={plan.id} value={plan.id}>{plan.goal}</option>)}</select></label><label>练习范围<select value={input.stageId ?? ''} onChange={event => setInput({ ...input, stageId: event.target.value || null, nodeId: null })}><option value="">整个学习方案</option>{selectedPlan?.result?.stages.map(stage => <option key={stage.id} value={stage.id}>{stage.title}</option>)}</select></label><label>针对知识节点（可选）<select value={input.nodeId ?? ''} onChange={event => setInput({ ...input, nodeId: event.target.value || null })}><option value="">当前范围的全部节点</option>{selectedPlan?.result?.stages.find(stage => stage.id === input.stageId)?.nodeIds.map(id => <option key={id} value={id}>{selectedPlan.result?.graph.nodes.find(node => node.id === id)?.title ?? id}</option>)}{!input.stageId && selectedPlan?.result?.graph.nodes.map(node => <option key={node.id} value={node.id}>{node.title}</option>)}</select></label><div className="form-grid"><label>题目数量<select value={input.count} onChange={event => setInput({ ...input, count: Number(event.target.value) })}>{[1, 5, 10, 20].map(count => <option key={count} value={count}>{count} 题</option>)}</select></label><label>难度<select value={input.difficulty} onChange={event => setInput({ ...input, difficulty: event.target.value as PracticeInput['difficulty'] })}><option value="adaptive">自适应</option><option value="basic">基础</option><option value="advanced">进阶</option><option value="comprehensive">综合</option></select></label></div><fieldset className="question-type-field"><legend>题型覆盖</legend>{(['choice', 'fill', 'short', 'essay'] as const).map(kind => <label key={kind}><input type="checkbox" checked={input.kinds.includes(kind)} onChange={event => setInput({ ...input, kinds: event.target.checked ? [...input.kinds, kind] : input.kinds.filter(item => item !== kind) })} />{{ choice: '单选 choice', fill: '填空 fill', short: '简答 short', essay: '论述 essay' }[kind]}</label>)}</fieldset><label>反馈方式<select value={input.feedbackMode} onChange={event => setInput({ ...input, feedbackMode: event.target.value as PracticeInput['feedbackMode'] })}><option value="immediate">每题提交后反馈</option><option value="deferred">全部完成后反馈</option><option value="hints">只给提示，不显示答案</option></select></label><Button onClick={() => void generate()} disabled={busy || !input.planId || !input.kinds.length}>开始真实生成<ArrowRight size={17} /></Button></div></Modal>
  </div>
}

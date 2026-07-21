// 澄清页（iter 191 F6 重构）：深链可恢复 + 提交后全程进度可见。
// - 问题三级回退：location.state -> 任务台账 -> GET /sessions/{id}.clarify_questions
//   （此前深链无 state 即死胡同，用户离开后 paused_clarify 会话无任何入口能发现）
// - 回答草稿：输入即写 localStorage('dt_clarify_draft_<id>')（对齐 Home 草稿先例），
//   提交成功即清除；任务引擎在方案进入任意终态（done/error/cancelled/interrupted）
//   时同样清除对应会话草稿（iter 233 W2）——放弃澄清/他端完成不留孤儿键
// - 提交走任务引擎：成功发起（收到首个流事件）即 replace 跳 /generate/:id——
//   clarify 流与 generate 同构，复用进度页全套 UI（消灭此前提交后黑窗等待）
import { useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { SkeletonCards } from '../components/Skeleton'
import { getSession } from '../lib/api'
import { SseError } from '../lib/sse'
import { engine } from '../lib/taskEngine'
import { planTaskId, useTaskStore } from '../store/tasks'

const draftKey = (sid: string | undefined) => `dt_clarify_draft_${sid}`

function loadDraft(sid: string | undefined): Record<string, string> {
  try {
    const raw = localStorage.getItem(draftKey(sid))
    const v = raw ? (JSON.parse(raw) as Record<string, string>) : {}
    return v && typeof v === 'object' ? v : {}
  } catch {
    return {}
  }
}

export default function Clarify() {
  const { sessionId } = useParams()
  const sid = Number(sessionId)
  const loc = useLocation()
  const nav = useNavigate()
  const stateQuestions = (loc.state as { questions?: string[] } | null)?.questions
  const task = useTaskStore((s) => s.tasks[planTaskId(sid)])

  // 三级回退第 3 级：state/台账均无问题时才查服务端（深链/刷新恢复）
  const needFetch = (!stateQuestions || stateQuestions.length === 0) && !(task?.questions?.length)
  const { data: detail, isLoading } = useQuery({
    queryKey: ['sessions', sid, 'detail'],
    queryFn: () => getSession(sid),
    enabled: needFetch && !!sid,
    retry: 1,
  })

  const questions = useMemo(() => {
    if (stateQuestions?.length) return stateQuestions
    if (task?.questions?.length) return task.questions
    return detail?.clarify_questions ?? []
  }, [stateQuestions, task?.questions, detail])

  // 草稿：挂载恢复；输入即写；提交成功清除
  const [answers, setAnswers] = useState<Record<string, string>>(() => loadDraft(sessionId))
  useEffect(() => {
    try {
      if (Object.values(answers).some((v) => v.trim())) localStorage.setItem(draftKey(sessionId), JSON.stringify(answers))
      else localStorage.removeItem(draftKey(sessionId))
    } catch {
      /* 隐私模式等：草稿不存 */
    }
  }, [answers, sessionId])

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  function submit() {
    if (!sid) return
    // 过滤空回答
    const filtered = Object.fromEntries(Object.entries(answers).filter(([, v]) => v.trim()))
    // err.md D14：全空提交等于放弃澄清却走完整生成——拦下提示
    if (Object.keys(filtered).length === 0) {
      setError('请至少回答一个问题（或返回首页重新开始）')
      return
    }
    setLoading(true)
    setError('')
    engine
      .submitClarify(sid, filtered)
      .then(() => {
        // 成功发起：清草稿，跳进度页（流由引擎托管，多轮澄清自动回本页）
        try {
          localStorage.removeItem(draftKey(sessionId))
        } catch {
          /* 忽略 */
        }
        nav(`/generate/${sid}`, { replace: true })
      })
      .catch((e: unknown) => {
        setLoading(false)
        if (e instanceof SseError && e.status === 409) {
          setError('会话状态已变化（可能已在其他页面/标签页继续），请查看最新进度。')
        } else {
          setError(e instanceof Error ? e.message : '连接失败，请重试')
        }
      })
  }

  // 深链加载中（第 3 级回退取数）
  if (needFetch && isLoading) {
    return (
      <div className="space-y-4" role="status" aria-label="加载澄清问题">
        <h2 className="text-2xl font-bold">补充信息</h2>
        <SkeletonCards count={2} />
      </div>
    )
  }

  // err.md D14：无问题显式出口（已回答过/状态已变化）；iter 191 补「查看会话进度」入口
  if (questions.length === 0)
    return (
      <div className="space-y-4">
        <h2 className="text-2xl font-bold">补充信息</h2>
        <p className="text-slate-600 dark:text-slate-300">
          没有待回答的澄清问题（可能已回答过或会话状态已变化）。
        </p>
        <div className="flex items-center gap-3">
          <button
            onClick={() => nav(`/generate/${sid}`)}
            className="rounded-lg border px-4 py-2 text-sm font-medium transition-colors hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            查看会话进度
          </button>
          <button
            onClick={() => nav('/')}
            className="rounded-lg bg-blue-600 px-6 py-2.5 font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98]"
          >
            返回首页
          </button>
        </div>
      </div>
    )

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">补充信息</h2>
      <p className="text-slate-600 dark:text-slate-300">为了生成更贴合的学习方案，请回答以下问题（回答会自动保存在本机，刷新不丢）：</p>

      <div className="space-y-3">
        {questions.map((q, i) => {
          const inputId = `clarify-q-${i}`
          return (
            // iter 100 美化：问题卡片化 + 错峰入场
            <div
              key={i}
              className="animate-fade-in-up space-y-1 rounded-xl border bg-white p-4 shadow-sm dark:border-slate-700 dark:bg-slate-800"
              style={{ animationDelay: `${Math.min(i, 6) * 70}ms` }}
            >
              <label htmlFor={inputId} className="text-sm font-medium">
                {q}
              </label>
              <input
                id={inputId}
                className="w-full rounded-lg border border-slate-300 p-2 transition-shadow focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-slate-600 dark:bg-slate-900"
                value={answers[q] ?? ''}
                onChange={(e) => setAnswers((a) => ({ ...a, [q]: e.target.value }))}
                placeholder="你的回答…"
                maxLength={2000} // err.md D14
              />
            </div>
          )
        })}
      </div>

      {error && (
        <div role="alert" className="animate-fade-in rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {error}
          {error.includes('状态已变化') && (
            <button onClick={() => nav(`/generate/${sid}`)} className="ml-2 font-medium underline underline-offset-2">
              查看任务
            </button>
          )}
        </div>
      )}

      <button
        onClick={submit}
        disabled={loading}
        className="rounded-lg bg-blue-600 px-6 py-2.5 font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-[.98] disabled:opacity-50 disabled:hover:shadow-sm disabled:active:scale-100"
      >
        {loading ? '提交中…' : '提交并继续生成'}
      </button>
    </div>
  )
}

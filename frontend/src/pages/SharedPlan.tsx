import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { ApiError, getShared, type SharedPlanData } from '../lib/api'
import { isHttpUrl } from '../lib/url'

// 协作分享公开页（P5）：只读展示方案，无需登录。仅含方案内容，无用户/会话信息。
export default function SharedPlan() {
  const { token } = useParams()
  const [data, setData] = useState<SharedPlanData | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!token) return
    getShared(token)
      .then(setData)
      .catch((e) => setError(e instanceof ApiError ? e.message : '加载失败'))
      .finally(() => setLoading(false))
  }, [token])

  if (loading)
    return (
      // iter 100：加载中转圈替代纯文本
      <div className="flex items-center justify-center gap-2 p-10 text-slate-500 dark:text-slate-400" role="status">
        <span aria-hidden="true" className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600 dark:border-slate-600 dark:border-t-blue-400" />
        加载中…
      </div>
    )
  if (error || !data)
    return (
      <div className="mx-auto max-w-xl animate-fade-in p-10 text-center">
        <div className="text-4xl">🔗</div>
        <h1 className="mt-4 text-lg font-medium text-slate-700 dark:text-slate-200">{error || '分享链接不存在或已撤销'}</h1>
        <p className="mt-2 text-sm text-slate-400">请向分享者确认链接是否仍然有效</p>
      </div>
    )

  const stages = data.content.stages ?? []
  const quizCount = data.content.quizzes?.length ?? 0
  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <header className="mb-6 animate-fade-in-up border-b pb-4 dark:border-slate-700">
        <div className="text-xs text-slate-400">Deep-tutor 分享的学习方案（只读）</div>
        <h1 className="mt-1 text-2xl font-bold text-slate-800 dark:text-slate-100">{data.title}</h1>
        {data.content.topic && <div className="mt-1 text-sm text-slate-500 dark:text-slate-400">主题：{data.content.topic}</div>}
      </header>

      {stages.map((st, i) => (
        <section
          key={st.index}
          className="mb-4 animate-fade-in-up rounded-xl border bg-white shadow-sm transition-shadow hover:shadow-md dark:border-slate-700 dark:bg-slate-800 p-4"
          style={{ animationDelay: `${Math.min(i, 8) * 60}ms` }}
        >
          <h2 className="font-semibold text-slate-800 dark:text-slate-100">
            阶段 {st.index + 1}：{st.name}
          </h2>
          {st.goal && <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">{st.goal}</p>}
          {st.milestone && <p className="mt-1 text-sm text-indigo-600 dark:text-indigo-400">🎯 里程碑：{st.milestone}</p>}
          {st.topics && st.topics.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {st.topics.map((t) => (
                <span
                  key={t.name}
                  className={`rounded-full px-2 py-0.5 text-xs ${t.must_learn ? 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200' : 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300'}`}
                >
                  {t.name}
                  {t.must_learn ? ' ★' : ''}
                </span>
              ))}
            </div>
          )}
          {st.resources && st.resources.length > 0 && (
            <ul className="mt-2 space-y-1 text-sm">
              {st.resources.map((r, i) => (
                <li key={i} className="text-slate-600 dark:text-slate-300">
                  {r.verified === 'ok' ? '✅' : r.verified === 'invalid' ? '❌' : '⚠️'}{' '}
                  {isHttpUrl(r.url) ? (
                    <a href={r.url} target="_blank" rel="noreferrer" className="break-all text-blue-600 underline-offset-2 transition-colors hover:text-blue-500 hover:underline dark:text-blue-400 dark:hover:text-blue-300">
                      {r.title || r.url}
                    </a>
                  ) : (
                    r.title || r.url
                  )}
                  {r.type && <span className="ml-1 text-xs text-slate-400">（{r.type}）</span>}
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}

      {quizCount > 0 && <div className="text-sm text-slate-500 dark:text-slate-400">📝 配套测验题 {quizCount} 道（仅方案所有者可用）</div>}
      <footer className="mt-8 text-center text-xs text-slate-400">由 Deep-tutor 生成并分享</footer>
    </div>
  )
}

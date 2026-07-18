import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { ApiError, getShared, type SharedPlanData } from '../lib/api'

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

  if (loading) return <div className="p-10 text-center text-slate-500">加载中…</div>
  if (error || !data)
    return (
      <div className="mx-auto max-w-xl p-10 text-center">
        <div className="text-4xl">🔗</div>
        <h1 className="mt-4 text-lg font-medium text-slate-700">{error || '分享链接不存在或已撤销'}</h1>
        <p className="mt-2 text-sm text-slate-400">请向分享者确认链接是否仍然有效</p>
      </div>
    )

  const stages = data.content.stages ?? []
  const quizCount = data.content.quizzes?.length ?? 0
  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <header className="mb-6 border-b pb-4">
        <div className="text-xs text-slate-400">Deep-tutor 分享的学习方案（只读）</div>
        <h1 className="mt-1 text-2xl font-bold text-slate-800">{data.title}</h1>
        {data.content.topic && <div className="mt-1 text-sm text-slate-500">主题：{data.content.topic}</div>}
      </header>

      {stages.map((st) => (
        <section key={st.index} className="mb-4 rounded-lg border bg-white p-4">
          <h2 className="font-semibold text-slate-800">
            阶段 {st.index + 1}：{st.name}
          </h2>
          {st.goal && <p className="mt-1 text-sm text-slate-600">{st.goal}</p>}
          {st.milestone && <p className="mt-1 text-sm text-indigo-600">🎯 里程碑：{st.milestone}</p>}
          {st.topics && st.topics.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {st.topics.map((t) => (
                <span
                  key={t.name}
                  className={`rounded-full px-2 py-0.5 text-xs ${t.must_learn ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-600'}`}
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
                <li key={i} className="text-slate-600">
                  {r.verified === 'ok' ? '✅' : r.verified === 'invalid' ? '❌' : '⚠️'}{' '}
                  {r.url ? (
                    <a href={r.url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline">
                      {r.title || r.url}
                    </a>
                  ) : (
                    r.title
                  )}
                  {r.type && <span className="ml-1 text-xs text-slate-400">（{r.type}）</span>}
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}

      {quizCount > 0 && <div className="text-sm text-slate-500">📝 配套测验题 {quizCount} 道（仅方案所有者可用）</div>}
      <footer className="mt-8 text-center text-xs text-slate-400">由 Deep-tutor 生成并分享</footer>
    </div>
  )
}

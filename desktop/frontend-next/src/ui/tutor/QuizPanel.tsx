import { useEffect, useState } from "react";
import { t } from "../../i18n";
import { reason } from "../../i18n/kernel";
import type { AgentPort } from "../../port/port";
import type { TutorQuizItem, TutorQuizStats, TutorWrongItem } from "../../port/tutor";

// The quiz panel: a quiz run as a tab. Choice questions are
// judged by the server; fill/short/essay reveal a reference answer for
// self-assessment. When every choice is judged, per-node accuracy is written
// back as progress — score ≥ 0.8 marks the node done. Below the run sits the
// wrong drill: this plan's wrongly-answered questions, re-answered until the
// server judges them right — which is what takes them out of the book.
interface Props {
  port: AgentPort;
  pid: string;
  onError: (message: string) => void;
}

type Verdict = { correct: boolean | null; answer?: string; explanation?: string };

export function QuizPanel({ port, pid, onError }: Props) {
  const [items, setItems] = useState<TutorQuizItem[] | null>(null);
  const [generating, setGenerating] = useState(false);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [verdicts, setVerdicts] = useState<Record<string, Verdict>>({});
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});
  const [wrongs, setWrongs] = useState<TutorWrongItem[] | null>(null);
  const [wrongPicked, setWrongPicked] = useState<Record<string, string>>({});
  const [wrongVerdicts, setWrongVerdicts] = useState<Record<string, boolean | null>>({});
  const [stats, setStats] = useState<TutorQuizStats | null>(null);

  useEffect(() => {
    port.tutorQuizzes(pid).then((set) => setItems(set.quizzes)).catch(() => setItems([]));
    port.tutorQuizStats(pid).then(setStats).catch(() => setStats(null));
  }, [port, pid]);

  const generate = async () => {
    if (generating) return;
    setGenerating(true);
    try {
      const set = await port.tutorQuizGenerate(pid, items !== null && items.length > 0);
      setItems(set.quizzes);
      setPicked({});
      setVerdicts({});
      setRevealed({});
    } catch (e) {
      onError(reason(e));
    } finally {
      setGenerating(false);
    }
  };

  const judge = async (q: TutorQuizItem) => {
    const selected = picked[q.id];
    if (!selected) return;
    try {
      const verdict = await port.tutorQuizSubmit(q.id, pid, selected);
      // The write-back reads this map, so it gets the one that includes the
      // verdict that just landed — the state from this render is one behind.
      const next = { ...verdicts, [q.id]: verdict };
      setVerdicts(next);
      maybeWriteProgress(next);
    } catch (e) {
      onError(reason(e));
    }
  };

  // Aggregate judged choice accuracy per node and write it back as one
  // progress record per node. Judged means the server answered — a selection
  // is an intent, not a result.
  const maybeWriteProgress = (current: Record<string, Verdict>) => {
    const choices = (items ?? []).filter((q) => q.type === "choice");
    const judged = choices.filter((q) => current[q.id] !== undefined);
    if (choices.length === 0 || judged.length < choices.length) return;
    const byNode = new Map<string, { hit: number; total: number }>();
    for (const q of choices) {
      const row = byNode.get(q.node_id) ?? { hit: 0, total: 0 };
      row.total += 1;
      if (current[q.id]?.correct === true) row.hit += 1;
      byNode.set(q.node_id, row);
    }
    for (const [nodeId, row] of byNode) {
      const score = row.hit / row.total;
      void port.tutorProgressRecord(pid, nodeId, score >= 0.8 ? "done" : "learning", score).catch(() => undefined);
    }
  };

  const choiceTotal = (items ?? []).filter((q) => q.type === "choice").length;
  const choiceHit = (items ?? []).filter((q) => verdicts[q.id]?.correct === true).length;

  // The wrong drill reads the plan's wrong book on demand and re-judges each
  // answer through the same submit endpoint — a right answer is what clears
  // the row, so the drill is honest about needing the server, not a local tick.
  const loadWrong = () => {
    port.tutorQuizWrong(pid)
      .then((r) => {
        setWrongs(r.items);
        setWrongPicked({});
        setWrongVerdicts({});
      })
      .catch((e) => onError(reason(e)));
  };

  const judgeWrong = async (item: TutorWrongItem) => {
    const selected = wrongPicked[item.id];
    if (!selected || wrongVerdicts[item.id] !== undefined) return;
    try {
      const verdict = await port.tutorQuizSubmit(item.id, pid, selected);
      setWrongVerdicts((v) => ({ ...v, [item.id]: verdict.correct === true }));
    } catch (e) {
      onError(reason(e));
    }
  };

  return (
    <div className="tut-card">
      <header className="tut-plan-hd">
        <h3>{t("练习测验")}</h3>
        <button type="button" data-action="tutor.quiz-gen" disabled={generating} onClick={() => void generate()}>
          {generating ? t("出题中…") : items && items.length > 0 ? t("换一批题") : t("生成测验")}
        </button>
      </header>
      {choiceTotal > 0 && <p className="tut-dim">{t("选择题得分 {hit}/{total}", { hit: choiceHit, total: choiceTotal })}</p>}
      {stats !== null && stats.attempts > 0 && (
        <p className="tut-dim">
          {t("累计作答 {n} 次", { n: stats.attempts })} · {t("正确率 {pct}%", { pct: Math.round((stats.accuracy ?? 0) * 100) })}
        </p>
      )}
      {items === null && <p className="tut-dim">{t("读取中…")}</p>}
      {items?.length === 0 && <p className="tut-dim">{t("还没有题目，点上面的按钮生成。")}</p>}
      {(items ?? []).map((q, i) => {
        const verdict = verdicts[q.id];
        return (
          <div className="tut-quiz" key={q.id}>
            <p className="tut-quiz-q">
              <strong>{i + 1}.</strong> {q.question}
              <span className="tut-dim"> · {q.type === "choice" ? t("单选") : q.type === "fill" ? t("填空") : t("简答")}</span>
            </p>
            {q.type === "choice" ? (
              <>
                <div role="radiogroup" aria-label={q.question}>
                  {(q.options ?? []).map((opt, j) => (
                    <label key={j} className="tut-opt">
                      <input
                        type="radio" name={q.id} data-action="tutor.quiz-pick"
                        disabled={verdict !== undefined}
                        checked={picked[q.id] === opt}
                        onChange={() => setPicked((p) => ({ ...p, [q.id]: opt }))}
                      />
                      <span className="tut-opt-badge">{j + 1}</span> {opt}
                    </label>
                  ))}
                </div>
                {!verdict && (
                  <button
                    type="button" data-action="tutor.quiz-judge" disabled={!picked[q.id]}
                    onClick={() => void judge(q)}
                  >
                    {t("判定")}
                  </button>
                )}
                {verdict && (
                  <p className={verdict.correct === true ? "tut-ok" : "tut-error"}>
                    {verdict.correct === true ? `✓ ${t("答对了")}` : `✕ ${t("再想想")}`}
                    {verdict.answer ? ` · ${t("正确答案：{answer}", { answer: verdict.answer })}` : ""}
                    {verdict.explanation ? <span className="tut-dim"> — {verdict.explanation}</span> : null}
                  </p>
                )}
              </>
            ) : (
              <>
                {revealed[q.id] && q.answer
                  ? <p className="tut-ok">{t("参考答案：{answer}", { answer: q.answer })}</p>
                  : <button type="button" data-action="tutor.quiz-reveal" onClick={() => setRevealed((r) => ({ ...r, [q.id]: true }))}>{t("显示参考答案")}</button>}
              </>
            )}
          </div>
        );
      })}

      <header className="tut-plan-hd">
        <h3>{t("错题重练")}</h3>
        <button type="button" data-action="tutor.quiz-wrong" onClick={loadWrong}>
          {wrongs === null ? t("取本方案错题") : t("刷新错题")}
        </button>
      </header>
      {wrongs === null && <p className="tut-dim">{t("答错的选择题会进错题本；这里逐题重答，答对即出本。")}</p>}
      {wrongs?.length === 0 && <p className="tut-dim">{t("这一方案没有错题。")}</p>}
      {(wrongs ?? []).map((item, i) => {
        const ok = wrongVerdicts[item.id];
        return (
          <div className="tut-quiz" key={item.id}>
            <p className="tut-quiz-q">
              <strong>{i + 1}.</strong> {item.question}
              {item.wrong_at && <span className="tut-dim"> · {new Date(item.wrong_at).toLocaleDateString()}</span>}
            </p>
            <div role="radiogroup" aria-label={item.question}>
              {(item.options ?? []).map((opt, j) => (
                <label key={j} className="tut-opt">
                  <input
                    type="radio" name={`wrong-${item.id}`} data-action="tutor.wrong-pick"
                    disabled={ok !== undefined}
                    checked={wrongPicked[item.id] === opt}
                    onChange={() => setWrongPicked((p) => ({ ...p, [item.id]: opt }))}
                  />
                  <span className="tut-opt-badge">{j + 1}</span> {opt}
                </label>
              ))}
            </div>
            {ok === undefined && (
              <button type="button" data-action="tutor.wrong-judge" disabled={!wrongPicked[item.id]} onClick={() => void judgeWrong(item)}>
                {t("判定")}
              </button>
            )}
            {ok !== undefined && (
              <p className={ok ? "tut-ok" : "tut-error"}>
                {ok ? `✓ ${t("答对了，这题出了错题本。")}` : `✕ ${item.answer ? t("正确答案：{answer}", { answer: item.answer }) : t("再想想")}`}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

import { useEffect, useState } from "react";
import { t } from "../../i18n";
import { reason } from "../../i18n/kernel";
import type { AgentPort } from "../../port/port";
import type { TutorReviewItem, TutorWrongItem } from "../../port/tutor";
import type { TutorView } from "./Tutor";

// The review workflow: due knowledge cards first, then wrong quizzes — an
// honest-recall flow (flip before rating), with the three self-assessment
// buttons writing progress back.
interface Props {
  port: AgentPort;
  onGo: (view: TutorView) => void;
  onError: (message: string) => void;
}

type Mode = "all" | "nodes" | "quiz";

const MODES: { key: Mode; label: () => string }[] = [
  { key: "all", label: () => t("全部") },
  { key: "nodes", label: () => t("知识点") },
  { key: "quiz", label: () => t("错题") },
];

interface Overview {
  due: number;
  wrongTotal: number;
  wrongByPlan: { plan_id: string; topic: string; count: number }[];
}

export function Review({ port, onGo, onError }: Props) {
  const [mode, setMode] = useState<Mode>(() => (localStorage.getItem("tutor_review_mode") as Mode) ?? "all");
  const [cards, setCards] = useState<TutorReviewItem[]>([]);
  const [wrongs, setWrongs] = useState<TutorWrongItem[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [at, setAt] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [counts, setCounts] = useState({ forgot: 0, fuzzy: 0, remembered: 0, quizRight: 0, quizWrong: 0 });
  const [weak, setWeak] = useState<{ nodes: TutorReviewItem[]; quiz: TutorWrongItem[] }>({ nodes: [], quiz: [] });
  const [done, setDone] = useState(false);

  useEffect(() => {
    localStorage.setItem("tutor_review_mode", mode);
  }, [mode]);

  const load = () => {
    port.tutorReviewQueue(50).then((q) => setCards(q.items)).catch((e) => onError(reason(e)));
    port.tutorWrongQueue(50).then((q) => setWrongs(q.items)).catch(() => setWrongs([]));
    // The day's shape, separate from the queue the flow below walks: how much
    // is due today, and where the wrong answers live.
    port.tutorReviewDue(1).then((d) => setOverview((o) => ({ ...(o ?? { wrongTotal: 0, wrongByPlan: [] }), due: d.total }))).catch(() => undefined);
    port.tutorWrongOverview().then((w) => setOverview((o) => ({ due: o?.due ?? 0, wrongTotal: w.total, wrongByPlan: w.items }))).catch(() => undefined);
    setAt(0);
    setFlipped(false);
    setDone(false);
    setCounts({ forgot: 0, fuzzy: 0, remembered: 0, quizRight: 0, quizWrong: 0 });
    setWeak({ nodes: [], quiz: [] });
  };
  useEffect(load, [port, onError]); // eslint-disable-line react-hooks/exhaustive-deps

  const card = cards[at];
  // Where the wrong item for this position sits. In the "all" mode the due
  // cards hold the first positions and the wrongs follow them; in the
  // wrongs-only mode the wrongs are the whole deck, so `at` indexes them
  // directly — clamping through the card count would repeat the first item.
  const quizAt = mode === "quiz" ? at : Math.max(0, at - cards.length);
  const quiz = mode === "quiz" || (mode === "all" && !card) ? wrongs[quizAt] : undefined;

  const rate = (level: 0 | 1 | 2) => {
    if (!card) return;
    if (!flipped) {
      setFlipped(true);
      return;
    }
    const status = level === 0 ? "not_started" : level === 1 ? "learning" : "done";
    const score = level === 0 ? 0 : level === 1 ? 0.5 : 1;
    void port.tutorProgressRecord(card.plan_id, card.node_id, status, score).catch(() => undefined);
    setCounts((c) => ({ ...c, forgot: c.forgot + (level === 0 ? 1 : 0), fuzzy: c.fuzzy + (level === 1 ? 1 : 0), remembered: c.remembered + (level === 2 ? 1 : 0) }));
    if (level !== 2) setWeak((w) => ({ ...w, nodes: [...w.nodes, card] }));
    advance();
  };

  const advance = () => {
    setFlipped(false);
    const total = mode === "quiz" ? wrongs.length : mode === "nodes" ? cards.length : cards.length + wrongs.length;
    if (at + 1 >= total) setDone(true);
    else setAt(at + 1);
  };

  const [quizPick, setQuizPick] = useState<Record<number, string>>({});
  const [quizResult, setQuizResult] = useState<Record<number, boolean | null>>({});

  const judgeQuiz = async (item: TutorWrongItem, offset: number) => {
    const selected = quizPick[offset];
    if (!selected || quizResult[offset] !== undefined) return;
    try {
      const result = await port.tutorQuizSubmit(item.id, item.plan_id ?? "", selected);
      const ok = result.correct === true;
      setQuizResult((r) => ({ ...r, [offset]: ok }));
      setCounts((c) => ({ ...c, quizRight: c.quizRight + (ok ? 1 : 0), quizWrong: c.quizWrong + (ok ? 0 : 1) }));
      if (!ok) setWeak((w) => ({ ...w, quiz: [...w.quiz, item] }));
      setTimeout(advance, 900);
    } catch (e) {
      onError(reason(e));
    }
  };

  const requeueWeak = () => {
    // Rerun only this round's forgot/still-wrong items, narrowing each pass.
    setCards(weak.nodes);
    setWrongs(weak.quiz);
    setAt(0);
    setFlipped(false);
    setQuizPick({});
    setQuizResult({});
    setDone(false);
    setCounts({ forgot: 0, fuzzy: 0, remembered: 0, quizRight: 0, quizWrong: 0 });
    setWeak({ nodes: [], quiz: [] });
  };

  const totalAll = mode === "quiz" ? wrongs.length : mode === "nodes" ? cards.length : cards.length + wrongs.length;

  return (
    <div className="tut-page">
      {overview && (overview.due > 0 || overview.wrongTotal > 0) && (
        <div className="tut-card">
          <p className="tut-chips">
            <span>{t("今日到期 {n} 项", { n: overview.due })}</span>
            <span>{t("错题 {n} 道", { n: overview.wrongTotal })}</span>
          </p>
          {overview.wrongByPlan.length > 0 && (
            <ul className="tut-list">
              {overview.wrongByPlan.map((w) => (
                <li key={w.plan_id}>
                  <button
                    type="button" data-action="tutor.wrong-plan"
                    onClick={() => onGo({ k: "plan", pid: w.plan_id, tab: "quiz" })}
                  >
                    {w.topic || w.plan_id}
                    <span className="tut-dim"> · {t("{n} 道错题", { n: w.count })} →</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="tut-card">
        <div className="tut-filter" role="group" aria-label={t("复习范围")}>
          {MODES.map((m) => (
            <button key={m.key} type="button" data-action="tutor.review-mode" aria-pressed={mode === m.key}
              onClick={() => {
                setMode(m.key);
                setAt(0);
                setFlipped(false);
                // Each mode indexes the wrongs its own way, so a pick or a
                // result from the mode being left would answer for a different
                // item in the one being entered.
                setQuizPick({});
                setQuizResult({});
              }}>{m.label()}</button>
          ))}
        </div>

        {done ? (
          <>
            <h3>{t("这一轮复习完了")}</h3>
            <p className="tut-chips">
              <span>{t("忘了 {n}", { n: counts.forgot })}</span>
              <span>{t("模糊 {n}", { n: counts.fuzzy })}</span>
              <span>{t("记住了 {n}", { n: counts.remembered })}</span>
              <span>{t("错题对 {n}", { n: counts.quizRight })}/{counts.quizRight + counts.quizWrong}</span>
            </p>
            {(weak.nodes.length > 0 || weak.quiz.length > 0) && (
              <button type="button" className="tut-primary" data-action="tutor.review-weak" onClick={requeueWeak}>
                {t("重练薄弱（{n} 项）", { n: weak.nodes.length + weak.quiz.length })}
              </button>
            )}
            <button type="button" data-action="tutor.review-again" onClick={load}>{t("再复习一次")}</button>
          </>
        ) : card && (mode === "nodes" || mode === "all") ? (
          <>
            <p className="tut-dim">{at + 1}/{totalAll} · {card.plan_topic}</p>
            <div className="tut-flash" data-action="tutor.flip" onClick={() => setFlipped(true)}>
              <p className="tut-flash-q">{card.name}</p>
              {flipped ? (
                <div className="tut-flash-a">
                  <p>{t("掌握度 {pct}%", { pct: Math.round(card.mastery * 100) })} · {t("难度 {n}", { n: card.difficulty ?? "?" })}</p>
                  {card.note ? <p>{card.note}</p> : <p className="tut-dim">{t("（这条还没有笔记）")}</p>}
                  <p className="tut-hint">{t("诚实回忆后再翻面评分，效果最好。")}</p>
                </div>
              ) : (
                <p className="tut-dim">{t("先在心里回忆这个知识点，再点卡片翻面。")}</p>
              )}
            </div>
            {flipped && (
              <div className="tut-rate">
                <button type="button" data-action="tutor.rate-forgot" onClick={() => rate(0)}>{t("忘了 (1)")}</button>
                <button type="button" data-action="tutor.rate-fuzzy" onClick={() => rate(1)}>{t("模糊 (2)")}</button>
                <button type="button" className="tut-primary" data-action="tutor.rate-remembered" onClick={() => rate(2)}>{t("记住了 (3)")}</button>
                <button type="button" data-action="tutor.rate-skip" onClick={advance}>{t("跳过 →")}</button>
              </div>
            )}
          </>
        ) : quiz ? (
          <>
            <p className="tut-dim">{at + 1}/{totalAll} · {t("错题重练")}</p>
            <p className="tut-quiz-q">{quiz.question}</p>
            <div role="radiogroup" aria-label={quiz.question}>
              {(quiz.options ?? []).map((opt, j) => (
                <label key={j} className="tut-opt">
                  <input type="radio" name={`rev-${at}`} data-action="tutor.review-pick"
                    checked={quizPick[quizAt] === opt}
                    onChange={() => setQuizPick((p) => ({ ...p, [quizAt]: opt }))} />
                  <span className="tut-opt-badge">{j + 1}</span> {opt}
                </label>
              ))}
            </div>
            <button
              type="button" data-action="tutor.review-judge"
              disabled={!quizPick[quizAt] || quizResult[quizAt] !== undefined}
              onClick={() => void judgeQuiz(quiz, quizAt)}
            >
              {t("判定")}
            </button>
            {quizResult[quizAt] !== undefined && (
              <p className={quizResult[quizAt] ? "tut-ok" : "tut-error"}>
                {quizResult[quizAt] ? `✓ ${t("答对了")}` : `✕ ${quiz.answer ? t("正确答案：{answer}", { answer: quiz.answer }) : t("再想想")}`}
              </p>
            )}
          </>
        ) : (
          <>
            <p className="tut-dim">{totalAll === 0 ? t("现在没有到期的知识点，也没有错题。") : t("这一类已经复习完。")}</p>
            <button type="button" data-action="tutor.review-refresh" onClick={load}>{t("再取一次最新数据")}</button>
            <button type="button" data-action="tutor.review-home" onClick={() => onGo({ k: "home" })}>{t("返回首页")}</button>
          </>
        )}
      </div>
    </div>
  );
}

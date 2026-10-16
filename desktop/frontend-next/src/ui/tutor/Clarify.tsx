import { useEffect, useState } from "react";
import { t } from "../../i18n";
import { reason } from "../../i18n/kernel";
import type { AgentPort } from "../../port/port";
import * as engine from "./engine";
import { useTasks } from "./Tutor";
import type { TutorView } from "./Tutor";

// The clarify page: questions are plain free-text strings, one input each,
// with the draft kept locally until submit. Submitting resumes the run and
// lands back on the progress view.
interface Props {
  port: AgentPort;
  sid: string;
  onGo: (view: TutorView) => void;
  onError: (message: string) => void;
}

const draftKey = (sid: string) => `tutor_clarify_draft_${sid}`;

export function Clarify({ port, sid, onGo, onError }: Props) {
  const { list } = useTasks();
  const entry = list.find((e) => e.sessionId === sid);
  const [questions, setQuestions] = useState<string[]>([]);
  const [answers, setAnswers] = useState<string[]>([]);
  const [sending, setSending] = useState(false);

  // Three-level question fallback: the live ledger, the session row, the
  // locally kept draft — so a deep link still shows them.
  useEffect(() => {
    if (entry?.questions && entry.questions.length > 0) {
      setQuestions(entry.questions);
      return;
    }
    port.tutorSession(sid)
      .then((s) => setQuestions(s.clarify_questions ?? []))
      .catch(() => setQuestions([]));
  }, [port, sid, entry?.questions]);

  useEffect(() => {
    try {
      const kept = JSON.parse(localStorage.getItem(draftKey(sid)) ?? "[]") as string[];
      if (Array.isArray(kept) && kept.length === questions.length) setAnswers(kept);
    } catch {
      // no draft
    }
  }, [sid, questions.length]);

  useEffect(() => {
    if (questions.length > 0) localStorage.setItem(draftKey(sid), JSON.stringify(answers));
  }, [sid, questions.length, answers]);

  const submit = async () => {
    if (answers.some((a) => a.trim() !== "") === false || sending) return;
    setSending(true);
    try {
      await engine.submitClarify(port, sid, answers.map((a) => a.trim()));
      localStorage.removeItem(draftKey(sid));
      onGo({ k: "generate", sid });
    } catch (e) {
      onError(reason(e));
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="tut-page">
      <div className="tut-card">
        <h3>{t("回答几个问题，方案会更贴合你")}</h3>
        {questions.length === 0 && <p className="tut-dim">{t("这个会话当前没有待回答的问题。")}</p>}
        {questions.map((q, i) => (
          <div className="tut-q" key={i}>
            <label htmlFor={`tut-q-${i}`}>{q}</label>
            <input
              id={`tut-q-${i}`}
              data-action="tutor.clarify-answer"
              maxLength={2000}
              value={answers[i] ?? ""}
              onChange={(e) => {
                const next = [...answers];
                next[i] = e.target.value;
                setAnswers(next);
              }}
            />
          </div>
        ))}
        {questions.length > 0 && (
          <button
            type="button" className="tut-primary" data-action="tutor.clarify-submit"
            disabled={sending || answers.every((a) => (a ?? "").trim() === "")}
            onClick={() => void submit()}
          >
            {sending ? t("提交中…") : t("提交并继续生成")}
          </button>
        )}
        <p className="tut-hint">{t("至少回答一个问题；草稿会自动保留。")}</p>
      </div>
    </div>
  );
}

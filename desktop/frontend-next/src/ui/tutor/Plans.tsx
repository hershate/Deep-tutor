import { useEffect, useState } from "react";
import { t } from "../../i18n";
import { reason } from "../../i18n/kernel";
import type { AgentPort } from "../../port/port";
import type { TutorPlanSummary, TutorSession } from "../../port/tutor";
import type { TutorView } from "./Tutor";

// The plans list: every delivered plan lineage plus placeholder rows for
// in-flight sessions.
interface Props {
  port: AgentPort;
  onGo: (view: TutorView) => void;
  onError: (message: string) => void;
}

export function Plans({ port, onGo, onError }: Props) {
  const [plans, setPlans] = useState<TutorPlanSummary[] | null>(null);
  const [sessions, setSessions] = useState<TutorSession[]>([]);

  useEffect(() => {
    port.tutorPlans().then(setPlans).catch((e) => {
      onError(reason(e));
      setPlans([]);
    });
    port.tutorSessions().then((all) => setSessions(all.filter((s) => s.status === "running" || s.status === "paused_clarify"))).catch(() => undefined);
  }, [port, onError]);

  return (
    <div className="tut-page">
      <div className="tut-card">
        <h3>{t("我的方案")}</h3>
        {sessions.map((s) => (
          <div className="tut-row" key={`run-${s.id}`}>
            <button
              type="button" className="tut-row-main" data-action="tutor.attach"
              onClick={() => onGo({ k: s.status === "paused_clarify" ? "clarify" : "generate", sid: s.id })}
            >
              <span className="tut-dim">{t("生成中")}</span> {s.title || t("未命名目标")}
            </button>
          </div>
        ))}
        {plans === null && <p className="tut-dim">{t("读取中…")}</p>}
        {plans !== null && plans.length === 0 && sessions.length === 0 && (
          <p className="tut-dim">{t("还没有方案。")} <button type="button" data-action="tutor.new-goal" onClick={() => onGo({ k: "home" })}>{t("去制定一个")}</button></p>
        )}
        {plans?.map((p) => (
          <div className="tut-row" key={p.id}>
            <button type="button" className="tut-row-main" data-action="tutor.open-plan" onClick={() => onGo({ k: "plan", pid: p.id })}>
              {p.topic || p.title}
              <span className="tut-dim">
                {" "}· {t("版本 {n}", { n: p.version })} · {new Date(p.updated_at).toLocaleDateString()}
              </span>
            </button>
            <button
              type="button" className="tut-row-act" data-action="tutor.study"
              onClick={() => onGo({ k: "learn", pid: p.id })}
            >
              {t("学习")}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

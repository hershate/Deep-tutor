import { useEffect, useState } from "react";
import { t } from "../../i18n";
import { reason } from "../../i18n/kernel";
import type { AgentPort } from "../../port/port";
import type { TutorLearningLog, TutorPlanSummary, TutorProgressOverview, TutorStatsSummary } from "../../port/tutor";
import type { TutorView } from "./Tutor";

// The learn center: today's study rows, the plan picker, the progress board
// with node status cycling, and the check-in form with logs — one sheet view.
interface Props {
  port: AgentPort;
  pid?: string;
  onGo: (view: TutorView) => void;
  onError: (message: string) => void;
}

export function Learn({ port, pid, onGo, onError }: Props) {
  const [plans, setPlans] = useState<TutorPlanSummary[]>([]);
  const [planId, setPlanId] = useState(pid ?? "");
  const [overview, setOverview] = useState<TutorProgressOverview | null>(null);
  const [logs, setLogs] = useState<TutorLearningLog[]>([]);
  const [stats, setStats] = useState<TutorStatsSummary | null>(null);
  const [minutes, setMinutes] = useState("30");
  const [note, setNote] = useState("");

  useEffect(() => {
    port.tutorPlans().then((all) => {
      setPlans(all);
      if (!pid && all.length > 0 && all[0]) setPlanId(all[0].id);
    }).catch((e) => onError(reason(e)));
    port.tutorStatsSummary().then(setStats).catch(() => undefined);
  }, [port, pid, onError]);

  const reloadPlan = () => {
    if (!planId) return;
    port.tutorProgressOf(planId).then(setOverview).catch(() => setOverview(null));
    port.tutorLogs(planId).then(setLogs).catch(() => setLogs([]));
  };
  useEffect(reloadPlan, [port, planId]); // eslint-disable-line react-hooks/exhaustive-deps

  const cycle = (nodeId: string, status: string) => {
    const next = status === "not_started" ? "learning" : status === "learning" ? "done" : "not_started";
    void port.tutorProgressRecord(planId, nodeId, next).catch((e) => onError(reason(e))).then(reloadPlan);
  };

  const checkIn = async () => {
    const m = Number(minutes);
    if (!Number.isFinite(m) || m <= 0 || !planId) return;
    try {
      await port.tutorLogAdd(planId, m, note.trim() || undefined);
      setNote("");
      port.tutorStatsSummary().then(setStats).catch(() => undefined);
      reloadPlan();
    } catch (e) {
      onError(reason(e));
    }
  };

  return (
    <div className="tut-page">
      {stats && (
        <div className="tut-card tut-stats">
          <span>{t("今日 {n} 分钟", { n: stats.today_minutes })}</span>
          <span>{t("累计 {n} 分钟", { n: stats.total_minutes })}</span>
          <span>{t("连续 {n} 天", { n: stats.streak_days })}</span>
          <span>{t("打卡 {n} 天", { n: stats.checkin_days })}</span>
        </div>
      )}

      <div className="tut-card">
        {plans.length > 1 && (
          <select data-action="tutor.learn-plan" value={planId} onChange={(e) => setPlanId(e.target.value)} aria-label={t("选择方案")}>
            {plans.map((p) => <option key={p.id} value={p.id}>{p.topic || p.title}</option>)}
          </select>
        )}
        {plans.length === 0 && <p className="tut-dim">{t("还没有方案。")} <button type="button" data-action="tutor.new-goal" onClick={() => onGo({ k: "home" })}>{t("去制定一个")}</button></p>}

        {overview && (
          <>
            <h3>{t("学习进度")}</h3>
            <p className="tut-dim">
              {t("掌握度 {pct}%", { pct: Math.round(overview.mastery_avg * 100) })} · {t("{done}/{total} 已完成", { done: overview.done_count, total: overview.total })}
            </p>
            <ul className="tut-board">
              {overview.nodes.map((n) => (
                <li key={n.node_id} data-status={n.status}>
                  <button type="button" data-action="tutor.node-cycle" onClick={() => cycle(n.node_id, n.status)}>
                    <span aria-hidden="true">{n.status === "done" ? "●" : n.status === "learning" ? "◐" : "○"}</span> {n.name}
                    <span className="tut-dim"> {Math.round(n.mastery * 100)}%</span>
                  </button>
                </li>
              ))}
            </ul>
            <p className="tut-hint">{t("点节点循环切换：未开始 → 学习中 → 已完成。")}</p>
          </>
        )}
      </div>

      {planId && (
        <div className="tut-card">
          <h3>{t("学习打卡")}</h3>
          <div className="tut-fields">
            <label>
              <span>{t("时长（分钟）")}</span>
              <input type="number" min={1} max={1440} data-action="tutor.checkin-min" value={minutes} onChange={(e) => setMinutes(e.target.value)} />
            </label>
            <label>
              <span>{t("备注")}</span>
              <input data-action="tutor.checkin-note" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} />
            </label>
          </div>
          <button type="button" className="tut-primary" data-action="tutor.checkin" onClick={() => void checkIn()}>{t("打卡")}</button>
          {logs.length > 0 && (
            <ul className="tut-logs">
              {logs.map((l) => (
                <li key={l.id}>
                  {l.date} · {l.minutes}{t("分钟")}{l.note ? ` · ${l.note}` : ""}
                  <button
                    type="button" className="tut-row-act" data-action="tutor.log-del"
                    onClick={() => void port.tutorLogDelete(l.plan_id, l.id).then(reloadPlan).catch((e) => onError(reason(e)))}
                  >
                    {t("删除")}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

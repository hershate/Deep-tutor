import { useEffect, useState } from "react";
import { t } from "../../i18n";
import { reason } from "../../i18n/kernel";
import type { AgentPort } from "../../port/port";
import type { TutorUsageSummary } from "../../port/tutor";

// The tutor usage view: today against the daily budget and the trailing
// cost bars — honestly limited to what the file ledger keeps (cost and runs
// per day).
interface Props {
  port: AgentPort;
  onError: (message: string) => void;
}

export function UsageView({ port, onError }: Props) {
  const [summary, setSummary] = useState<TutorUsageSummary | null>(null);

  useEffect(() => {
    port.tutorUsage(14).then(setSummary).catch((e) => {
      onError(reason(e));
      setSummary(null);
    });
  }, [port, onError]);

  if (!summary) {
    return <div className="tut-page"><div className="tut-card"><p className="tut-dim">{t("读取中…")}</p></div></div>;
  }
  const pct = summary.budget_enabled && summary.daily_budget > 0
    ? Math.min(100, Math.round((summary.today.cost / summary.daily_budget) * 100))
    : 0;
  const over = summary.budget_enabled && summary.today.cost > summary.daily_budget;
  const peak = Math.max(...summary.daily.map((d) => d.cost), 0.01);

  return (
    <div className="tut-page">
      <div className="tut-card">
        <h3>{t("今日花费")}</h3>
        {summary.budget_enabled ? (
          <>
            <p className={over ? "tut-error" : pct >= 80 ? "tut-warn" : undefined}>
              ¥{summary.today.cost.toFixed(4)} / ¥{summary.daily_budget.toFixed(2)} · {t("{n} 次生成", { n: summary.today.runs })}
            </p>
            <div className="tut-progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
              <div data-over={over || undefined} style={{ width: `${pct}%` }} />
            </div>
          </>
        ) : (
          <p>¥{summary.today.cost.toFixed(4)} · {t("{n} 次生成", { n: summary.today.runs })} · {t("未设日预算")}</p>
        )}
      </div>
      <div className="tut-card">
        <h3>{t("近 14 天")}</h3>
        <div className="tut-bars">
          {summary.daily.map((d) => (
            <div key={d.date} className="tut-bar" title={`${d.date} · ¥${d.cost.toFixed(4)} · ${d.runs}`}>
              <div style={{ height: `${Math.max(2, Math.round((d.cost / peak) * 100))}%` }} />
              <span>{d.date.slice(5)}</span>
            </div>
          ))}
        </div>
        <p className="tut-dim">{t("累计 ¥{cost} · {runs} 次生成", { cost: summary.total_cost.toFixed(4), runs: summary.total_runs })}</p>
      </div>
    </div>
  );
}

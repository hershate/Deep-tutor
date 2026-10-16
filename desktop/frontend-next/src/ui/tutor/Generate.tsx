import { useEffect, useState } from "react";
import { t } from "../../i18n";
import { reason } from "../../i18n/kernel";
import type { AgentPort } from "../../port/port";
import * as engine from "./engine";
import { useConfirm } from "./Confirm";
import { useTasks } from "./Tutor";
import type { TutorView } from "./Tutor";

// The generation progress page: a pure subscriber of the task ledger. The
// stream lives in the engine, so leaving this view never cancels anything.
interface Props {
  port: AgentPort;
  sid: string;
  onGo: (view: TutorView) => void;
  onError: (message: string) => void;
}

export function Generate({ port, sid, onGo, onError }: Props) {
  const { list } = useTasks();
  const entry = list.find((e) => e.sessionId === sid);
  const [showPartial, setShowPartial] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const { confirm, overlay } = useConfirm();

  useEffect(() => {
    if (!entry || entry.phase !== "running") return;
    const started = entry.startedAt;
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [entry?.phase, entry?.startedAt]); // eslint-disable-line react-hooks/exhaustive-deps

  // The view switches to the plan 1.5s after done, once the plan id exists.
  useEffect(() => {
    if (entry?.phase === "done" && entry.planId) {
      const timer = setTimeout(() => onGo({ k: "plan", pid: entry.planId! }), 1500);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [entry?.phase, entry?.planId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!entry) {
    return (
      <div className="tut-page">
        <div className="tut-card">
          <p>{t("这个会话还没有生成记录。")}</p>
          <button type="button" data-action="tutor.back-home" onClick={() => onGo({ k: "home" })}>
            {t("返回首页")}
          </button>
        </div>
        {/* The ledger can drop the task while its own question is open; the
            ask renders on this branch too so it still gets its answer. */}
        {overlay}
      </div>
    );
  }

  const doneCount = entry.steps.filter((s) => s.status !== "run").length;
  const total = entry.steps.length > 0 ? Math.max(...entry.steps.map((s) => s.totalSteps)) : 0;
  const pct = total > 0 ? Math.round((doneCount / total) * 100) : 0;

  return (
    <div className="tut-page">
      <div className="tut-card">
        {entry.phase === "running" && (
          <h3><span className="tut-ping" aria-hidden="true" /> {t("正在生成你的学习方案")}</h3>
        )}
        {entry.phase === "clarify" && <h3>{t("需要你补充一点信息")}</h3>}
        {entry.phase === "done" && <h3>{t("方案已生成")}</h3>}
        {entry.phase === "error" && <h3>{t("生成失败了")}</h3>}
        {entry.phase === "cancelled" && <h3>{t("已取消")}</h3>}
        {entry.phase === "interrupted" && <h3>{t("生成中断")}</h3>}

        {entry.conn === "reconnecting" && <p className="tut-warn">{t("连接中断，正在自动重连，生成仍在继续…")}</p>}
        {entry.conn === "offline" && entry.phase === "running" && (
          <p className="tut-warn">
            {t("连接已断开。生成可能仍在后台进行。")}
            <button
              type="button" data-action="tutor.reattach"
              onClick={() => void engine.startGenerate(port, sid, entry.rawText ?? " ").catch((e) => onError(reason(e)))}
            >
              {t("重试连接")}
            </button>
          </p>
        )}

        {(entry.phase === "running" || entry.phase === "clarify") && (
          <>
            <div className="tut-progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
              <div style={{ width: `${pct}%` }} />
            </div>
            <p className="tut-dim">
              {doneCount}/{total || "…"} · {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")} ·{" "}
              {entry.tokens > 0 ? `${entry.tokens} ${t("tokens")} · ` : ""}
              {entry.cost > 0 ? `¥${entry.cost.toFixed(2)}` : ""}
            </p>
            <ol className="tut-steps">
              {entry.steps.map((s) => (
                <li key={`${s.stepIndex}-${s.tool}`} data-status={s.status}>
                  <span aria-hidden="true">{s.status === "run" ? "◌" : s.status === "fail" ? "✕" : s.status === "degraded" ? "⚠" : "✓"}</span>{" "}
                  {engine.toolLabel(s.tool)}
                  {s.attempt > 1 && <em> · {t("第 {n} 次重试", { n: s.attempt })}</em>}
                </li>
              ))}
            </ol>
            {entry.partialPlan && (
              <button type="button" data-action="tutor.partial" onClick={() => setShowPartial((v) => !v)}>
                {showPartial ? t("收起部分方案") : t("查看部分方案 →")}
              </button>
            )}
            {showPartial && entry.partialPlan && <PartialPlan plan={entry.partialPlan} />}
            <p className="tut-hint">{t("现在离开不会打断生成；费用一旦发生不会因取消退回。")}</p>
            {entry.phase === "running" && (
              <button
                type="button" data-action="tutor.cancel"
                onClick={() => {
                  void confirm(t("取消后已产生的模型费用不会退回，确定取消？")).then((ok) => {
                    if (ok) void engine.cancelTask(port, sid).catch(() => undefined);
                  });
                }}
              >
                {t("取消生成")}
              </button>
            )}
          </>
        )}

        {entry.phase === "clarify" && entry.questions && entry.questions.length > 0 && (
          <button type="button" className="tut-primary" data-action="tutor.go-clarify" onClick={() => onGo({ k: "clarify", sid })}>
            {t("去回答问题")}
          </button>
        )}

        {entry.phase === "done" && (
          <button
            type="button" className="tut-primary" data-action="tutor.open-plan"
            onClick={() => entry.planId && onGo({ k: "plan", pid: entry.planId })}
          >
            {t("立即查看 →")}
          </button>
        )}

        {(entry.phase === "error" || entry.phase === "cancelled" || entry.phase === "interrupted") && (
          <>
            {entry.errorMessage && <p className="tut-error">{entry.errorMessage}</p>}
            <button
              type="button" className="tut-primary" data-action="tutor.retry"
              onClick={() => void engine.retry(port, sid).catch((e) => onError(reason(e)))}
            >
              {t("重试生成")}
            </button>
            <button type="button" data-action="tutor.back-home" onClick={() => onGo({ k: "home" })}>
              {t("返回首页")}
            </button>
          </>
        )}
      </div>

      {overlay}
    </div>
  );
}

// A light outline of the streamed partial plan — enough to judge direction
// while the run is still going, not the full detail view.
function PartialPlan({ plan }: { plan: unknown }) {
  const p = plan as { topic?: string; stages?: { name?: string; goal?: string }[] };
  if (!p || !Array.isArray(p.stages)) {
    return <pre className="tut-partial">{JSON.stringify(p, null, 2).slice(0, 400)}</pre>;
  }
  return (
    <div className="tut-partial">
      {p.topic && <strong>{p.topic}</strong>}
      <ol>
        {p.stages.map((s, i) => (
          <li key={i}>{s.name}{s.goal ? ` — ${s.goal}` : ""}</li>
        ))}
      </ol>
    </div>
  );
}

import { useEffect, useState } from "react";
import { t } from "../../i18n";
import { reason } from "../../i18n/kernel";
import type { AgentPort } from "../../port/port";
import type { TutorPlan, TutorPlanVersionRow, TutorShare, TutorValidation } from "../../port/tutor";
import * as engine from "./engine";
import { useTasks } from "./Tutor";
import type { PlanTab, TutorView } from "./Tutor";
import { QuizPanel } from "./QuizPanel";
import { MaterialsPanel } from "./MaterialsPanel";

// Plan detail: header with versions and exports, summary chips, then tabs —
// outline/schedule/resources/validation plus quiz and self-study materials
// as first-class tabs.
interface Props {
  port: AgentPort;
  pid: string;
  initialTab?: PlanTab;
  onGo: (view: TutorView) => void;
  onError: (message: string) => void;
}

const TABS: { key: PlanTab; label: () => string }[] = [
  { key: "outline", label: () => t("学习路径") },
  { key: "schedule", label: () => t("时间线") },
  { key: "resources", label: () => t("资源") },
  { key: "validation", label: () => t("校验报告") },
  { key: "quiz", label: () => t("测验") },
  { key: "materials", label: () => t("自学讲义") },
];

const REGEN_SCOPES = ["design_stages", "search_resources", "schedule_plan", "validate_plan"] as const;

export function PlanDetail({ port, pid, initialTab, onGo, onError }: Props) {
  const [plan, setPlan] = useState<TutorPlan | null>(null);
  const [versions, setVersions] = useState<TutorPlanVersionRow[]>([]);
  const [tab, setTab] = useState<PlanTab>(initialTab ?? "outline");
  const [resourceFilter, setResourceFilter] = useState<"all" | "ok" | "uncertain" | "invalid">("all");
  const [regenScope, setRegenScope] = useState<string>(REGEN_SCOPES[0]);
  const [regenFeedback, setRegenFeedback] = useState("");
  const [share, setShare] = useState<TutorShare | null>(null);
  const [copied, setCopied] = useState(false);
  const { list } = useTasks();

  const reload = () => {
    port.tutorPlan(pid).then((p) => {
      setPlan(p);
      port.tutorPlanVersions(p.id).then(setVersions).catch(() => undefined);
    }).catch((e) => onError(reason(e)));
    port.tutorShareStatus(pid)
      .then((s) => setShare(s.shared ? s : null))
      .catch(() => undefined);
  };
  useEffect(reload, [port, pid, onError]); // eslint-disable-line react-hooks/exhaustive-deps

  const shareURL = () => `${window.location.origin}${share?.path ?? ""}`;
  const copyShare = async () => {
    try {
      await navigator.clipboard?.writeText(shareURL());
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      onError(reason(e));
    }
  };

  const download = async (kind: "md" | "ics") => {
    try {
      const text = kind === "md" ? await port.tutorPlanMarkdown(pid) : await port.tutorPlanICS(pid);
      const blob = new Blob([text], { type: kind === "md" ? "text/markdown" : "text/calendar" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `plan-${pid}.${kind}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      onError(reason(e));
    }
  };

  const downloadApkg = async (wrongOnly: boolean) => {
    try {
      const blob = await port.tutorPlanApkg(pid, wrongOnly);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `plan-${pid}${wrongOnly ? "-wrong" : ""}.apkg`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      onError(reason(e));
    }
  };

  const downloadSlides = async () => {
    try {
      const text = await port.tutorPlanSlides(pid);
      const blob = new Blob([text], { type: "text/html" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `plan-${pid}.slides.html`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      onError(reason(e));
    }
  };

  const downloadPDF = async () => {
    try {
      const { content, degraded } = await port.tutorPlanPDF(pid);
      const blob = new Blob([content], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `plan-${pid}.md`;
      a.click();
      URL.revokeObjectURL(url);
      if (degraded) {
        onError(t("PDF 导出已降级为 Markdown，可自行打印为 PDF。"));
      }
    } catch (e) {
      onError(reason(e));
    }
  };

  if (!plan) {
    return <div className="tut-page"><div className="tut-card"><p className="tut-dim">{t("读取中…")}</p></div></div>;
  }

  const regen = list.find((e) => e.sessionId === plan.session_id && e.phase === "running");
  // A degraded run delivers a plan whose lists never landed: the kernel's nil
  // slices arrive as JSON null, and the page must say "none of those" rather
  // than die on a null read.
  const nodes = plan.knowledge_map?.nodes ?? [];
  const schedule = plan.schedule ?? [];
  const weeks = plan.stages.reduce((sum, s) => sum + s.estimated_weeks, 0);
  const resourceCount = plan.stages.reduce((sum, s) => sum + (s.resources?.length ?? 0), 0);

  return (
    <div className="tut-page">
      <div className="tut-card">
        <header className="tut-plan-hd">
          <h3>{plan.topic || plan.title}</h3>
          {versions.length > 1 && (
            <select
              data-action="tutor.version"
              value={plan.version}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (!Number.isFinite(v) || v === plan.version) return;
                port.tutorPlanVersion(pid, v).then(setPlan).catch((err) => onError(reason(err)));
              }}
              aria-label={t("方案版本")}
            >
              {[...versions].reverse().map((v) => (
                <option key={v.version} value={v.version}>{t("版本 {n}", { n: v.version })}</option>
              ))}
            </select>
          )}
        </header>
        <p className="tut-chips">
          <span>{t("{n} 个阶段", { n: plan.stages.length })}</span>
          <span>{t("{n} 个知识点", { n: nodes.length })}</span>
          <span>{t("{n} 条资源", { n: resourceCount })}</span>
          <span>{t("约 {n} 周", { n: Math.round(weeks) || 1 })}</span>
          {plan.intent.deadline && <span>{t("截止 {date}", { date: plan.intent.deadline })}</span>}
        </p>
        <div className="tut-acts">
          <button type="button" data-action="tutor.export-md" onClick={() => void download("md")}>{t("导出 Markdown")}</button>
          <button type="button" data-action="tutor.export-ics" onClick={() => void download("ics")}>{t("导出日历 ics")}</button>
          <button type="button" data-action="tutor.export-pdf" onClick={() => void downloadPDF()}>{t("导出 PDF")}</button>
          <button type="button" data-action="tutor.export-apkg" onClick={() => void downloadApkg(false)}>{t("导出 Anki 卡片")}</button>
          <button type="button" data-action="tutor.export-apkg-wrong" onClick={() => void downloadApkg(true)}>{t("导出错题卡片")}</button>
          <button type="button" data-action="tutor.export-slides" onClick={() => void downloadSlides()}>{t("导出幻灯片")}</button>
          {share ? (
            <>
              <a href={share.path} target="_blank" rel="noopener noreferrer">{t("查看分享页")}</a>
              <button type="button" data-action="tutor.share-copy" onClick={() => void copyShare()}>
                {copied ? t("已复制") : t("复制链接")}
              </button>
              <button type="button" data-action="tutor.share-revoke" onClick={() => {
                port.tutorShareRevoke(pid).then(() => setShare(null)).catch((e) => onError(reason(e)));
              }}>{t("撤销分享")}</button>
            </>
          ) : (
            <button type="button" data-action="tutor.share-create" onClick={() => {
              port.tutorShareCreate(pid).then((s) => setShare(s)).catch((e) => onError(reason(e)));
            }}>{t("只读分享")}</button>
          )}
          <button type="button" className="tut-primary" data-action="tutor.study" onClick={() => onGo({ k: "learn", pid: plan.id })}>{t("学习中心")}</button>
        </div>
      </div>

      <div className="tut-tabs" role="tablist" aria-label={t("方案视图")}>
        {TABS.map((x) => (
          <button
            key={x.key}
            role="tab" type="button" data-action="tutor.tab"
            aria-selected={tab === x.key}
            onClick={() => setTab(x.key)}
          >
            {x.label()}
          </button>
        ))}
      </div>

      {tab === "outline" && (
        <div className="tut-card">
          {plan.stages.map((s) => (
            <details key={s.index} className="tut-stage" open={s.index === 0}>
              <summary>
                <strong>{t("阶段 {n}", { n: s.index + 1 })} · {s.name}</strong>
                <span className="tut-dim"> {t("难度 {n}", { n: s.difficulty })} · {t("约 {n} 周", { n: Math.round(s.estimated_weeks) || 1 })}</span>
              </summary>
              <p>{s.goal}</p>
              <ul>
                {(s.topics ?? []).map((topic, i) => (
                  <li key={i}>
                    {topic.name}{topic.must_learn ? ` · ${t("必学")}` : ""}
                    {topic.why ? <span className="tut-dim"> — {topic.why}</span> : null}
                  </li>
                ))}
              </ul>
              {s.exercises && s.exercises.length > 0 && (
                <>
                  <h4>{t("练习")}</h4>
                  <ul>{s.exercises.map((ex, i) => <li key={i}>{ex.desc}（{ex.est_hours}h）</li>)}</ul>
                </>
              )}
              <p className="tut-milestone">🏁 {s.milestone}</p>
            </details>
          ))}
          <h4>{t("知识图谱")}</h4>
          <ul className="tut-nodes">
            {nodes.map((n) => (
              <li key={n.id}>
                {n.name}
                <span className="tut-dim"> · {t("难度 {n}", { n: n.difficulty })}{n.must_learn ? ` · ${t("必学")}` : ""}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {tab === "schedule" && (
        <div className="tut-card">
          <table className="tut-table">
            <thead><tr><th>{t("日期")}</th><th>{t("阶段")}</th><th>{t("任务")}</th><th>{t("分钟")}</th></tr></thead>
            <tbody>
              {schedule.map((d) => (
                <tr key={d.date}>
                  <td>{d.date}</td>
                  <td>{plan.stages[d.stage_index]?.name ?? d.stage_index}</td>
                  <td>{d.tasks.join("；")}</td>
                  <td>{d.est_minutes}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "resources" && (
        <div className="tut-card">
          <div className="tut-filter" role="group" aria-label={t("资源筛选")}>
            {(["all", "ok", "uncertain", "invalid"] as const).map((f) => (
              <button key={f} type="button" data-action="tutor.res-filter" aria-pressed={resourceFilter === f} onClick={() => setResourceFilter(f)}>
                {f === "all" ? t("全部") : f === "ok" ? t("✅ 已验证") : f === "uncertain" ? t("⚠️ 存疑") : t("❌ 无效")}
              </button>
            ))}
          </div>
          <ul className="tut-res">
            {plan.stages.flatMap((s) => (s.resources ?? [])
              .filter((r) => resourceFilter === "all" || r.verified === resourceFilter)
              .map((r) => (
                <li key={`${s.index}-${r.url}`}>
                  {r.verified === "ok" ? "✅" : r.verified === "uncertain" ? "⚠️" : "❌"}{" "}
                  <a href={r.url} target="_blank" rel="noreferrer noopener">{r.title}</a>
                  <span className="tut-dim"> · {t("阶段 {n}", { n: s.index + 1 })}{r.note ? ` · ${r.note}` : ""}</span>
                </li>
              )))}
          </ul>
        </div>
      )}

      {tab === "validation" && plan.validation_report && <ValidationCard report={plan.validation_report} />}

      {tab === "quiz" && <QuizPanel port={port} pid={plan.id} onError={onError} />}
      {tab === "materials" && <MaterialsPanel port={port} pid={plan.id} onError={onError} />}

      <div className="tut-card">
        <h3>{t("重新生成")}</h3>
        {regen ? (
          <p className="tut-dim">
            <span className="tut-ping" aria-hidden="true" /> {t("正在重新生成…")}
            <button type="button" data-action="tutor.regen-view" onClick={() => onGo({ k: "generate", sid: plan.session_id })}>{t("查看进度")}</button>
          </p>
        ) : (
          <>
            <label className="tut-label">
              {t("从哪一步重做")}
              <select data-action="tutor.regen-scope" value={regenScope} onChange={(e) => setRegenScope(e.target.value)}>
                {REGEN_SCOPES.map((s) => <option key={s} value={s}>{engine.toolLabel(s)}</option>)}
              </select>
            </label>
            <textarea
              rows={2} data-action="tutor.regen-feedback" maxLength={500}
              placeholder={t("想改什么？可留空（选中的步骤及其后全部重跑，生成新版本）")}
              value={regenFeedback} onChange={(e) => setRegenFeedback(e.target.value)}
            />
            <button
              type="button" className="tut-primary" data-action="tutor.regen"
              onClick={() => void engine.regenerate(port, plan.id, plan.session_id, regenScope, regenFeedback).catch((e) => onError(reason(e)))}
            >
              {t("开始重新生成")}
            </button>
            <p className="tut-hint">{t("每个方案最多重生成 2 次；重做会沿用未选中步骤的旧产出。")}</p>
          </>
        )}
      </div>
    </div>
  );
}

function ValidationCard({ report }: { report: TutorValidation }) {
  const rows: [string, { status: string; details?: string }][] = [
    [t("依赖闭合"), report.dependency_closure],
    [t("难度递进"), report.difficulty_step],
    [t("认知负荷"), report.cognitive_load],
    [t("里程碑可行性"), report.milestone_feasibility],
    [t("缺口分析"), report.gap_analysis],
  ];
  return (
    <div className="tut-card">
      <h3>{t("校验报告")} {report.passed ? "✅" : "⚠️"}</h3>
      {report.degraded && <p className="tut-warn">{t("校验环节降级运行，结果仅供参考。")}</p>}
      <ul className="tut-checks">
        {rows.map(([name, check]) => (
          <li key={name} data-status={check.status}>
            {check.status === "pass" ? "✅" : check.status === "warn" ? "⚠️" : "❌"} {name}
            {check.details && <span className="tut-dim"> — {check.details}</span>}
          </li>
        ))}
      </ul>
      {report.fixes && report.fixes.length > 0 && (
        <>
          <h4>{t("自动修复")}</h4>
          <ul>{report.fixes.map((f, i) => <li key={i}>{f}</li>)}</ul>
        </>
      )}
    </div>
  );
}

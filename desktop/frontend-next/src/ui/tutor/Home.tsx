import { useEffect, useMemo, useRef, useState } from "react";
import { t } from "../../i18n";
import { reason } from "../../i18n/kernel";
import type { AgentPort } from "../../port/port";
import type { TutorBackupCounts, TutorPlanSummary, TutorSession, TutorShareEntry } from "../../port/tutor";
import * as engine from "./engine";
import type { TutorView } from "./Tutor";

// Home: the big goal box with structured time fields, the optional material
// rows a generation can be grounded in, the running tasks and recent plans to
// jump back into, and the learning data — milestones, the check-in calendar,
// and where the time went.
interface Props {
  port: AgentPort;
  onGo: (view: TutorView) => void;
  onError: (message: string) => void;
}

const DRAFT_KEY = "tutor_home_draft";

type ParseMode = "full" | "fast" | "none";

const PARSE_MODES: { key: ParseMode; label: () => string }[] = [
  { key: "full", label: () => t("完整解析") },
  { key: "fast", label: () => t("快速解析") },
  { key: "none", label: () => t("仅添加") },
];

// One staged file: the blob plus the parse effort chosen per row, the way the
// kernel's confirm step asks for it.
interface StagedFile {
  file: File;
  mode: ParseMode;
}

export function Home({ port, onGo, onError }: Props) {
  const [text, setText] = useState(() => localStorage.getItem(DRAFT_KEY) ?? "");
  const [daily, setDaily] = useState("");
  const [deadline, setDeadline] = useState("");
  const [starting, setStarting] = useState(false);
  const [progress, setProgress] = useState("");
  const [plans, setPlans] = useState<TutorPlanSummary[]>([]);
  const [sessions, setSessions] = useState<TutorSession[]>([]);
  const [shares, setShares] = useState<TutorShareEntry[]>([]);
  const [stats, setStats] = useState<{ streak: number; minutes: number; checkins: number; today: number } | null>(null);
  const [calendar, setCalendar] = useState<Record<string, number> | null>(null);
  const [byPlan, setByPlan] = useState<{ plan_id: string; topic: string; minutes: number }[] | null>(null);
  const [files, setFiles] = useState<StagedFile[]>([]);
  const [urls, setUrls] = useState<string[]>([]);
  const [urlDraft, setUrlDraft] = useState("");
  const [backupPreview, setBackupPreview] = useState<TutorBackupCounts | null>(null);
  const [backupSummary, setBackupSummary] = useState<TutorBackupCounts | null>(null);
  const [backupB64, setBackupB64] = useState("");
  const [backupBusy, setBackupBusy] = useState(false);
  const backupInput = useRef<HTMLInputElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    port.tutorPlans().then(setPlans).catch(() => setPlans([]));
    port.tutorSessions().then(setSessions).catch(() => setSessions([]));
    port.tutorShares().then((r) => setShares(r.shares)).catch(() => setShares([]));
    port.tutorStatsSummary().then((s) => setStats({ streak: s.streak_days, minutes: s.total_minutes, checkins: s.checkin_days, today: s.today_minutes })).catch(() => setStats(null));
    port.tutorStatsCalendar().then(setCalendar).catch(() => setCalendar(null));
    port.tutorStatsByPlan().then(setByPlan).catch(() => setByPlan(null));
  }, [port]);

  useEffect(() => {
    localStorage.setItem(DRAFT_KEY, text);
  }, [text]);

  // A goal can start with nothing attached; these rows are what turns the
  // generation from "about the goal" into "about the material you brought".
  const stageFiles = (list: FileList | null) => {
    if (!list?.length) return;
    setFiles((cur) => [...cur, ...Array.from(list, (file) => ({ file, mode: "full" as ParseMode }))]);
    if (fileInput.current) fileInput.current.value = "";
  };

  const addUrl = () => {
    const u = urlDraft.trim();
    if (u === "") return;
    setUrls((cur) => (cur.includes(u) ? cur : [...cur, u]));
    setUrlDraft("");
  };

  const start = async () => {
    if (text.trim() === "" || starting) return;
    setStarting(true);
    let session: TutorSession | null = null;
    try {
      session = await port.tutorCreateSession();
      // Materials ride the session before the pipeline starts: each file is
      // uploaded, then confirmed with the parse effort its row chose — a
      // parse failure keeps the row confirmed with the reason on it, so the
      // pipeline can still refuse honestly downstream.
      for (let i = 0; i < files.length; i++) {
        setProgress(t("处理资料 {n}/{m}", { n: i + 1, m: files.length }));
        const f = files[i]!;
        const bytes = new Uint8Array(await f.file.arrayBuffer());
        const material = await port.tutorMaterialUpload(session.id, f.file.name, toBase64(bytes));
        await port.tutorMaterialConfirm(session.id, material.id, f.mode);
      }
      for (let i = 0; i < urls.length; i++) {
        setProgress(t("抓取网页 {n}/{m}", { n: i + 1, m: urls.length }));
        await port.tutorMaterialURL(session.id, urls[i]!);
      }
      setProgress("");
    } catch (e) {
      // An upload that cannot complete leaves a session nothing will run in;
      // removing it keeps retries from piling up orphans. A generation that
      // fails to start is different — its session stays for a retry.
      if (session) void port.tutorDeleteSession(session.id).catch(() => undefined);
      setStarting(false);
      setProgress("");
      onError(reason(e));
      return;
    }
    try {
      // Navigate first: the stream belongs to the engine, and the progress
      // view subscribes — waiting here would freeze the sheet on a whole
      // generation.
      onGo({ k: "generate", sid: session.id });
      await engine.startGenerate(port, session.id, text.trim(), {
        daily_minutes: Number(daily) > 0 ? Number(daily) : undefined,
        deadline: deadline || undefined,
      });
      setFiles([]);
      setUrls([]);
    } catch (e) {
      onError(reason(e));
    } finally {
      setStarting(false);
      setProgress("");
    }
  };

  const running = sessions.filter((s) => s.status === "running" || s.status === "paused_clarify");

  const exportBackup = async () => {
    setBackupBusy(true);
    try {
      const blob = await port.tutorBackupExport();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `reasonix-tutor-backup-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      onError(reason(e));
    } finally {
      setBackupBusy(false);
    }
  };

  const chooseBackup = async (files2: FileList | null) => {
    const f = files2?.[0];
    if (!f) return;
    setBackupBusy(true);
    setBackupSummary(null);
    try {
      const bytes = new Uint8Array(await f.arrayBuffer());
      let binary = "";
      for (let i = 0; i < bytes.length; i += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      }
      const b64 = btoa(binary);
      setBackupB64(b64);
      setBackupPreview(await port.tutorBackupPreview(b64));
    } catch (e) {
      setBackupPreview(null);
      onError(reason(e));
    } finally {
      setBackupBusy(false);
      if (backupInput.current) backupInput.current.value = "";
    }
  };

  const importBackup = async () => {
    if (!backupB64) return;
    setBackupBusy(true);
    try {
      setBackupSummary(await port.tutorBackupImport(backupB64));
      setBackupPreview(null);
      setBackupB64("");
      port.tutorPlans().then(setPlans).catch(() => undefined);
      port.tutorSessions().then(setSessions).catch(() => undefined);
    } catch (e) {
      onError(reason(e));
    } finally {
      setBackupBusy(false);
    }
  };

  return (
    <div className="tut-page">
      <section className="tut-card">
        <label className="tut-label" htmlFor="tut-goal">{t("我想学会…")}</label>
        <textarea
          id="tut-goal"
          className="tut-goal"
          data-action="tutor.goal"
          rows={5}
          maxLength={4000}
          placeholder={t("用一段话描述你的学习目标，例如：三个月内掌握 React，每天能投入一小时")}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="tut-fields">
          <label>
            <span>{t("每天可投入（分钟）")}</span>
            <input
              type="number" min={10} max={1440} data-action="tutor.daily"
              value={daily} onChange={(e) => setDaily(e.target.value)}
            />
          </label>
          <label>
            <span>{t("目标完成日期")}</span>
            <input
              type="date" data-action="tutor.deadline"
              value={deadline} onChange={(e) => setDeadline(e.target.value)}
            />
          </label>
        </div>
        <button
          type="button" className="tut-primary" data-action="tutor.start"
          disabled={text.trim() === "" || starting} onClick={() => void start()}
        >
          {starting ? t("正在创建…") : t("制定学习方案")}
        </button>
        {progress && <p className="tut-dim" role="status">{progress}</p>}
        <p className="tut-hint">{t("生成会经过意图理解、知识图谱、阶段设计、资源检索、排程与校验八道工序，全程可中断。")}</p>
      </section>

      <section className="tut-card">
        <h3>{t("附加资料（可选）")}</h3>
        <div className="tut-acts">
          <label className="tut-file-btn">
            {t("选择文件")}
            <input
              ref={fileInput} type="file" multiple data-action="tutor.mat-file"
              accept=".pdf,.docx,.pptx,.epub,.md,.txt"
              onChange={(e) => stageFiles(e.target.files)}
            />
          </label>
          <span className="tut-url-add">
            <input
              type="url" data-action-change="tutor.mat-url" data-action-keydown="tutor.mat-url-add" placeholder="https://"
              value={urlDraft} onChange={(e) => setUrlDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addUrl(); } }}
            />
            <button type="button" data-action="tutor.mat-url-add" disabled={urlDraft.trim() === ""} onClick={addUrl}>{t("添加网页")}</button>
          </span>
        </div>
        {files.length > 0 && (
          <ul className="tut-mats">
            {files.map((f, i) => (
              <li key={`${f.file.name}-${i}`}>
                <span className="tut-mat-name" title={f.file.name}>{f.file.name}</span>
                <select
                  data-action="tutor.mat-mode" value={f.mode} aria-label={t("{name} 的解析方式", { name: f.file.name })}
                  onChange={(e) => setFiles((cur) => cur.map((x, j) => (j === i ? { ...x, mode: e.target.value as ParseMode } : x)))}
                >
                  {PARSE_MODES.map((m) => <option key={m.key} value={m.key}>{m.label()}</option>)}
                </select>
                <button
                  type="button" className="tut-inline" data-action="tutor.mat-del"
                  onClick={() => setFiles((cur) => cur.filter((_, j) => j !== i))}
                >
                  {t("移除")}
                </button>
              </li>
            ))}
          </ul>
        )}
        {urls.length > 0 && (
          <ul className="tut-mats">
            {urls.map((u, i) => (
              <li key={u}>
                <span className="tut-mat-name" title={u}>{u}</span>
                <span className="tut-dim">{t("网页正文")}</span>
                <button
                  type="button" className="tut-inline" data-action="tutor.mat-del"
                  onClick={() => setUrls((cur) => cur.filter((_, j) => j !== i))}
                >
                  {t("移除")}
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="tut-hint">{t("文件支持 PDF、DOCX、PPTX、EPUB、Markdown 与纯文本；扫描版 PDF 没有文字层会被拒绝，先打印成文本再试。")}</p>
      </section>

      {running.length > 0 && (
        <section className="tut-card">
          <h3>{t("进行中的任务")}</h3>
          <ul className="tut-list">
            {running.map((s) => (
              <li key={s.id}>
                <button
                  type="button" data-action="tutor.attach"
                  onClick={() => onGo({ k: s.status === "paused_clarify" ? "clarify" : "generate", sid: s.id })}
                >
                  {s.title || t("未命名目标")}
                  <span className="tut-dim"> · {s.status === "paused_clarify" ? t("待澄清") : t("生成中")}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="tut-card">
        <h3>{t("最近方案")}</h3>
        {plans.length === 0 ? (
          <p className="tut-dim">{t("还没有方案，从上面开始第一个。")}</p>
        ) : (
          <ul className="tut-list">
            {plans.slice(0, 8).map((p) => (
              <li key={p.id}>
                <button type="button" data-action="tutor.open-plan" onClick={() => onGo({ k: "plan", pid: p.id })}>
                  {p.topic || p.title}
                  <span className="tut-dim"> · {new Date(p.updated_at).toLocaleDateString()}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {(stats !== null || calendar !== null) && (
        <section className="tut-card">
          <h3>{t("学习数据")}</h3>
          {stats && (
            <p className="tut-chips">
              <span>{t("连胜 {n} 天", { n: stats.streak })}</span>
              <span>{t("累计 {n} 小时", { n: (stats.minutes / 60).toFixed(1) })}</span>
              <span>{t("打卡 {n} 天", { n: stats.checkins })}</span>
              <span>{t("今日 {n} 分钟", { n: stats.today })}</span>
            </p>
          )}
          {calendar !== null && <Heatmap days={calendar} />}
          {byPlan !== null && byPlan.length > 0 && <ByPlanBars rows={byPlan} />}
        </section>
      )}

      {shares.length > 0 && (
        <section className="tut-card">
          <h3>{t("活跃分享")}</h3>
          <ul className="tut-list">
            {shares.map((x) => (
              <li key={x.token}>
                <a href={x.path} target="_blank" rel="noopener noreferrer">{x.title || x.topic}</a>
                <span className="tut-dim"> · {x.created_at ? new Date(x.created_at).toLocaleDateString() : ""}</span>
                {x.plan_id && (
                  <button
                    type="button" className="tut-inline" data-action="tutor.share-revoke-list"
                    onClick={() => {
                      port.tutorShareRevoke(x.plan_id!)
                        .then(() => setShares((cur) => cur.filter((y) => y.token !== x.token)))
                        .catch((e) => onError(reason(e)));
                    }}
                  >
                    {t("撤销")}
                  </button>
                )}
              </li>
            ))}
          </ul>
          <p className="tut-hint">{t("分享链接是只读的，任何拿到链接的人都能查看对应方案。")}</p>
        </section>
      )}

      <section className="tut-card">
        <h3>{t("备份")}</h3>
        <div className="tut-acts">
          <button type="button" data-action="tutor.backup-export" disabled={backupBusy} onClick={() => void exportBackup()}>
            {t("导出备份")}
          </button>
          <label className="tut-file-btn">
            {t("选择备份文件")}
            <input
              ref={backupInput} type="file" accept=".json,application/json"
              data-action="tutor.backup-file" disabled={backupBusy}
              onChange={(e) => void chooseBackup(e.target.files)}
            />
          </label>
        </div>
        {backupPreview && (
          <div className="tut-kb-answer">
            <p>{t("这份备份包含：")}</p>
            <p className="tut-chips">
              <span>{t("{n} 个会话", { n: Number(backupPreview.sessions ?? 0) })}</span>
              <span>{t("{n} 个方案", { n: Number(backupPreview.plans ?? 0) })}</span>
              <span>{t("{n} 条笔记", { n: Number(backupPreview.notes ?? 0) })}</span>
              <span>{t("{n} 篇知识库文档", { n: Number(backupPreview.kb_documents ?? 0) })}</span>
            </p>
            <button type="button" className="tut-primary" data-action="tutor.backup-import" disabled={backupBusy} onClick={() => void importBackup()}>
              {t("确认导入（追加，不覆盖现有数据）")}
            </button>
          </div>
        )}
        {backupSummary && (
          <p className="tut-hint">
            {t("已导入 {n} 个会话、{m} 个方案。", { n: Number(backupSummary.sessions ?? 0), m: Number(backupSummary.plans ?? 0) })}
            {Array.isArray(backupSummary.skipped) && backupSummary.skipped.length > 0
              ? t("部分内容超限未导入：{items}", { items: backupSummary.skipped.join("；") })
              : ""}
          </p>
        )}
        <p className="tut-hint">{t("备份是整个导师库的单文件 JSON：会话、方案、测验作答、进度、讲义、笔记与知识库；模型配置与密钥不在其中。")}</p>
      </section>
    </div>
  );
}

// The check-in calendar: twelve weeks of days, one cell per day, deeper means
// more minutes that day. Built from the ledger the kernel already keeps.
function Heatmap({ days }: { days: Record<string, number> }) {
  const weeks = useMemo(() => {
    const out: { date: string; minutes: number }[][] = [];
    const today = new Date();
    // The last column is the week today sits in, anchored on its Monday, so
    // the grid always covers today; the days after it in that column are
    // future and drawn hollow.
    const monday = new Date(today);
    monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
    for (let w = 11; w >= 0; w--) {
      const col: { date: string; minutes: number }[] = [];
      for (let d = 0; d < 7; d++) {
        const at = new Date(monday);
        at.setDate(monday.getDate() - w * 7 + d);
        const key = `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}-${String(at.getDate()).padStart(2, "0")}`;
        col.push({ date: key, minutes: at > today ? -1 : (days[key] ?? 0) });
      }
      out.push(col);
    }
    return out;
  }, [days]);
  return (
    <div className="tut-heat" role="img" aria-label={t("近 12 周学习打卡")}>
      {weeks.map((col, i) => (
        <span key={i} className="tut-heat-col">
          {col.map((d) => (
            <i
              key={d.date}
              data-level={d.minutes < 0 ? "future" : d.minutes === 0 ? "0" : d.minutes < 30 ? "1" : d.minutes < 60 ? "2" : "3"}
              title={d.minutes < 0 ? d.date : `${d.date} · ${t("{n} 分钟", { n: d.minutes })}`}
            />
          ))}
        </span>
      ))}
    </div>
  );
}

// Where the recorded time went, one bar per plan, longest first.
function ByPlanBars({ rows }: { rows: { plan_id: string; topic: string; minutes: number }[] }) {
  const sorted = useMemo(() => [...rows].sort((a, b) => b.minutes - a.minutes), [rows]);
  const peak = Math.max(...sorted.map((r) => r.minutes), 1);
  return (
    <ul className="tut-meters">
      {sorted.map((r) => (
        <li key={r.plan_id}>
          <span className="tut-mat-name" title={r.topic}>{r.topic || r.plan_id}</span>
          <span className="tut-meter" aria-hidden="true"><i style={{ width: `${Math.max(2, Math.round((r.minutes / peak) * 100))}%` }} /></span>
          <span className="tut-dim">{t("{n} 分钟", { n: r.minutes })}</span>
        </li>
      ))}
    </ul>
  );
}

function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

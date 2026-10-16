import type { AgentPort } from "../../port/port";
import { HttpError } from "../../port/port";
import { reason } from "../../i18n/kernel";
import type { TutorFrame, TutorTimeFields } from "../../port/tutor";

// engine.ts — the tutor's task ledger in this app's shapes. Run streams live
// in this module, not in the React tree: closing the page never aborts a
// generation, and a refresh reattaches by re-POSTing the kept raw text
// (the kernel answers an already-running session by relaying its stream).

export type TutorPhase = "running" | "clarify" | "done" | "error" | "cancelled" | "interrupted";
export type TutorConn = "live" | "reconnecting" | "offline" | "closed";

export interface TutorStep {
  tool: string;
  stepIndex: number;
  totalSteps: number;
  status: "run" | "ok" | "degraded" | "fail";
  attempt: number;
}

export interface TaskEntry {
  sessionId: string;
  phase: TutorPhase;
  conn: TutorConn;
  steps: TutorStep[];
  cost: number;
  tokens: number;
  startedAt: number;
  partialPlan?: unknown;
  questions?: string[];
  errorCode?: string;
  errorMessage?: string;
  planId?: string;
  rawText?: string;
  lastEventId?: string;
  lastWarn?: string;
}

const STORE_KEY = "tutor_tasks_v1";
const MAX_RETRY = 3;

const entries = new Map<string, TaskEntry>();
const listeners = new Set<() => void>();
const controllers = new Map<string, AbortController>();
let bumped = 0;

function notify() {
  bumped += 1;
  cache = null;
  for (const l of listeners) l();
}

// useSyncExternalStore compares snapshots by identity, so the projection is
// cached until the next notify invalidates it.
let cache: { rev: number; list: TaskEntry[] } | null = null;

export function subscribeTasks(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function taskSnapshot(): { rev: number; list: TaskEntry[] } {
  if (!cache || cache.rev !== bumped) cache = { rev: bumped, list: [...entries.values()] };
  return cache;
}

export function taskOf(sessionId: string): TaskEntry | undefined {
  return entries.get(sessionId);
}

/** Chinese step labels for the eight tools. */
export const TOOL_LABEL: Record<string, string> = {
  parse_material: "解析资料",
  understand_intent: "理解学习目标",
  build_knowledge_map: "构建知识图谱",
  design_stages: "设计学习阶段",
  search_resources: "搜索学习资源",
  schedule_plan: "编排日程",
  validate_plan: "校验方案",
  export_plan: "导出方案",
};

export function toolLabel(tool: string): string {
  return TOOL_LABEL[tool] ?? tool;
}

// Only recovery fields persist: the rest is rebuilt by the stream replay
// when the engine reattaches, and stale volatile numbers mislead more than
// they tell.
interface Persisted {
  sessionId: string;
  phase: TutorPhase;
  questions?: string[];
  errorCode?: string;
  errorMessage?: string;
  planId?: string;
  rawText?: string;
  lastEventId?: string;
}

function persist() {
  try {
    const rows: Persisted[] = [];
    for (const e of entries.values()) {
      if (e.phase === "running" || e.phase === "clarify" || e.phase === "done" || e.phase === "error" || e.phase === "cancelled" || e.phase === "interrupted") {
        rows.push({
          sessionId: e.sessionId, phase: e.phase, questions: e.questions,
          errorCode: e.errorCode, errorMessage: e.errorMessage, planId: e.planId,
          rawText: e.rawText, lastEventId: e.lastEventId,
        });
      }
      if (rows.length >= 200) break;
    }
    localStorage.setItem(STORE_KEY, JSON.stringify(rows));
  } catch {
    // A private window or a full quota: the ledger works in-memory regardless.
  }
}

export function restoreTasks() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return;
    for (const row of JSON.parse(raw) as Persisted[]) {
      if (entries.has(row.sessionId)) continue;
      entries.set(row.sessionId, {
        sessionId: row.sessionId, phase: row.phase, conn: "offline",
        steps: [], cost: 0, tokens: 0, startedAt: Date.now(),
        questions: row.questions, errorCode: row.errorCode, errorMessage: row.errorMessage,
        planId: row.planId, rawText: row.rawText, lastEventId: row.lastEventId,
      });
      if (row.phase === "running") {
        // The kernel is authoritative: boot() reconciles below and either
        // reattaches this one or marks it by the session's real status.
      }
    }
    notify();
  } catch {
    // Corrupt ledger: start empty rather than propagate the damage.
  }
}

function upsert(entry: TaskEntry, patch: Partial<TaskEntry>) {
  Object.assign(entry, patch);
  notify();
  persist();
}

/** One frame onto a ledger entry. Returns true when the frame was terminal. */
function applyFrame(entry: TaskEntry, frame: TutorFrame): boolean {
  if (frame.id) entry.lastEventId = frame.id;
  const d = frame.data;
  switch (frame.event) {
    case "tool_start": {
      const tool = String(d.tool ?? "");
      const step: TutorStep = {
        tool,
        stepIndex: Number(d.step_index ?? 0),
        totalSteps: Number(d.total_steps ?? 0),
        status: "run",
        attempt: Number(d.attempt ?? 1),
      };
      const at = entry.steps.findIndex((s) => s.stepIndex === step.stepIndex && s.tool === tool);
      if (at >= 0) entry.steps[at] = step;
      else entry.steps.push(step);
      entry.steps.sort((a, b) => a.stepIndex - b.stepIndex);
      break;
    }
    case "tool_end": {
      const tool = String(d.tool ?? "");
      const meta = (d.meta ?? {}) as { tokens?: number; cost?: number };
      const status = String(d.status ?? "ok");
      const at = entry.steps.findIndex((s) => s.tool === tool && s.stepIndex === Number(d.step_index ?? 0));
      if (at >= 0) entry.steps[at]!.status = status === "ok" ? "ok" : status === "degraded" ? "degraded" : "fail";
      entry.tokens += Number(meta.tokens ?? 0);
      entry.cost += Number(meta.cost ?? 0);
      break;
    }
    case "plan_chunk":
      entry.partialPlan = d.partial_plan;
      break;
    case "warn":
      entry.lastWarn = typeof d.message === "string" ? d.message : undefined;
      break;
    case "clarify_needed": {
      const questions = Array.isArray(d.questions) ? d.questions.map(String) : [];
      upsert(entry, { phase: "clarify", questions, conn: "closed" });
      return true;
    }
    case "budget_exceeded":
      upsert(entry, { phase: "interrupted", errorMessage: "模型花费已达预算上限", conn: "closed" });
      return true;
    case "error": {
      const code = String(d.code ?? "error");
      const message = typeof d.message === "string" ? d.message : "";
      upsert(entry, {
        phase: code === "cancelled" ? "cancelled" : "error",
        errorCode: code, errorMessage: message, conn: "closed",
      });
      return true;
    }
    case "done": {
      const meta = (d.metadata ?? {}) as { tokens?: number; cost?: number };
      upsert(entry, {
        phase: "done",
        planId: typeof d.plan_id === "string" ? d.plan_id : entry.planId,
        tokens: Number(meta.tokens ?? entry.tokens),
        cost: Number(meta.cost ?? entry.cost),
        conn: "closed",
      });
      return true;
    }
    default:
      break;
  }
  notify();
  return false;
}

function ensure(sessionId: string, phase: TutorPhase, rawText?: string): TaskEntry {
  let entry = entries.get(sessionId);
  if (!entry) {
    entry = { sessionId, phase, conn: "live", steps: [], cost: 0, tokens: 0, startedAt: Date.now() };
    entries.set(sessionId, entry);
  } else {
    entry.phase = phase;
    entry.conn = "live";
    entry.steps = [];
    entry.cost = 0;
    entry.tokens = 0;
    entry.partialPlan = undefined;
    entry.errorCode = undefined;
    entry.errorMessage = undefined;
    entry.startedAt = Date.now();
  }
  if (rawText !== undefined) entry.rawText = rawText;
  notify();
  return entry;
}

async function runStream(
  port: AgentPort,
  entry: TaskEntry,
  first: { path: string; body: Record<string, unknown> },
  opts: { reconnect: boolean },
) {
  const controller = new AbortController();
  controllers.set(entry.sessionId, controller);
  let attempt = 0;
  let path = first.path;
  let body = first.body;
  try {
    for (;;) {
      let terminal = false;
      entry.conn = attempt === 0 ? "live" : "reconnecting";
      notify();
      try {
        for await (const frame of port.tutorEventStream(path, body, {
          signal: controller.signal,
          lastEventId: entry.lastEventId,
        })) {
          if (applyFrame(entry, frame)) {
            terminal = true;
            break;
          }
        }
      } catch (e) {
        if (controller.signal.aborted) return;
        if (e instanceof HttpError && e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429) {
          upsert(entry, { phase: "error", errorMessage: reason(e), conn: "offline" });
          return;
        }
      }
      if (terminal) return;
      if (!opts.reconnect || attempt + 1 >= MAX_RETRY || controller.signal.aborted) {
        // Give up quietly: the ledger keeps what it saw and offers the
        // person a reconnect button backed by attach().
        entry.conn = "offline";
        notify();
        return;
      }
      attempt += 1;
      // Full-jitter backoff caps the thundering herd.
      await new Promise((r) => setTimeout(r, 250 + Math.random() * Math.min(4000, 2 ** attempt * 250)));
      if (controller.signal.aborted) return;
      // Reconnect through the attach door: generate with the kept raw text
      // relays a running session instead of restarting it, which retry and
      // regenerate endpoints would refuse with 409 while the run is live.
      const text = entry.rawText ?? "";
      path = `/tutor/sessions/${encodeURIComponent(entry.sessionId)}/generate`;
      body = { raw_text: text };
    }
  } finally {
    controllers.delete(entry.sessionId);
  }
}

export async function startGenerate(
  port: AgentPort, sessionId: string, rawText: string, tf: TutorTimeFields = {},
) {
  const entry = ensure(sessionId, "running", rawText);
  await runStream(port, entry, {
    path: `/tutor/sessions/${encodeURIComponent(sessionId)}/generate`,
    body: { raw_text: rawText, daily_minutes: tf.daily_minutes, deadline: tf.deadline, start_date: tf.start_date },
  }, { reconnect: true });
}

/** Clarify never reconnects: the endpoint resumes a paused session and an
 * automatic re-POST could double-apply answers. A break leaves the entry
 * offline with the questions intact for a manual resubmit. */
export async function submitClarify(port: AgentPort, sessionId: string, answers: string[]) {
  const entry = ensure(sessionId, "running", entryText(sessionId));
  await runStream(port, entry, {
    path: `/tutor/sessions/${encodeURIComponent(sessionId)}/clarify`,
    body: { answers },
  }, { reconnect: false });
}

function entryText(sessionId: string) {
  return entries.get(sessionId)?.rawText;
}

export async function retry(port: AgentPort, sessionId: string) {
  const entry = ensure(sessionId, "running");
  await runStream(port, entry, {
    path: `/tutor/sessions/${encodeURIComponent(sessionId)}/retry`,
    body: {},
  }, { reconnect: true });
}

export async function regenerate(port: AgentPort, planId: string, sessionId: string, scope: string, feedback?: string) {
  const entry = ensure(sessionId, "running");
  await runStream(port, entry, {
    path: `/tutor/plans/${encodeURIComponent(planId)}/regenerate`,
    body: { scope, feedback: feedback ?? "" },
  }, { reconnect: true });
}

export async function cancelTask(port: AgentPort, sessionId: string) {
  await port.tutorCancel(sessionId).catch(() => undefined);
}

/** Reattach after a reload or a give-up: sessions the kernel still holds as
 * running get their stream back; everything else settles to its durable
 * status — a one-way server-over-local reconcile. */
export async function boot(port: AgentPort) {
  restoreTasks();
  let sessions: Awaited<ReturnType<typeof port.tutorSessions>>;
  try {
    sessions = await port.tutorSessions();
  } catch {
    return;
  }
  for (const s of sessions) {
    if (s.status === "running") {
      const entry = entries.get(s.id) ?? ensure(s.id, "running");
      entry.phase = "running";
      entry.rawText = s.raw_text ?? entry.rawText;
      entry.questions = s.clarify_questions ?? entry.questions;
      if (!controllers.has(s.id)) {
        void startGenerate(port, s.id, s.raw_text ?? entry.rawText ?? " ").catch(() => undefined);
      }
    } else {
      const entry = entries.get(s.id);
      if (!entry || entry.phase === "running" || entry.phase === "clarify") {
        const settled: TaskEntry = entry ?? ensure(s.id, "running");
        settled.phase = s.status === "done" ? "done"
          : s.status === "paused_clarify" ? "clarify"
            : s.status === "interrupted" || s.status === "error" ? "error" : "cancelled";
        settled.questions = s.clarify_questions ?? settled.questions;
        settled.planId = s.plan_id ?? settled.planId;
        settled.errorMessage = s.last_error ?? settled.errorMessage;
        settled.conn = "closed";
        notify();
      }
    }
  }
  persist();
}

export function dropTask(sessionId: string) {
  controllers.get(sessionId)?.abort();
  controllers.delete(sessionId);
  entries.delete(sessionId);
  notify();
  persist();
}

// ── self-study materials stream ────────────────────────────────────────────

export interface MaterialsProgress {
  total: number;
  doneCount: number;
  degradedCount: number;
  current?: { order: number; title: string };
  finished: boolean;
  errorCode?: string;
  errorMessage?: string;
}

/** Streams the self-study guide generation, mirroring the plan-run ledger's
 * honesty: budget errors keep the units that already landed. */
export async function runMaterials(
  port: AgentPort,
  planId: string,
  req: { refresh?: boolean; unit_ids?: string[]; stage_indexes?: number[]; node_ids?: string[] },
  onEvent: (p: MaterialsProgress) => void,
) {
  const state: MaterialsProgress = { total: 0, doneCount: 0, degradedCount: 0, finished: false };
  const emit = () => onEvent({ ...state });
  try {
    for await (const frame of port.tutorEventStream(
      `/tutor/plans/${encodeURIComponent(planId)}/materials`, req,
    )) {
      const d = frame.data;
      switch (frame.event) {
        case "unit_start":
          state.total = Number(d.total ?? state.total);
          state.current = { order: Number(d.order ?? 0), title: String(d.title ?? "") };
          break;
        case "unit_done":
          state.doneCount += 1;
          if (d.degraded === true) state.degradedCount += 1;
          state.current = undefined;
          break;
        case "done":
          state.total = Number(d.unit_count ?? state.total);
          state.degradedCount = Number(d.degraded_count ?? state.degradedCount);
          state.finished = true;
          break;
        case "error":
          state.errorCode = String(d.code ?? "error");
          state.errorMessage = String(d.message ?? "");
          state.finished = true;
          break;
        default:
          break;
      }
      emit();
      if (state.finished) return;
    }
    state.finished = true;
    emit();
  } catch (e) {
    state.finished = true;
    state.errorMessage = reason(e);
    emit();
  }
}

// The tutor's whole surface over /tutor/*: entities as the kernel writes
// them, the SSE frame the run streams carry, and the request bodies the
// writing endpoints take. Shapes are kebab-free and snake_cased exactly as
// the Go structs tag them.

export type TutorStatus = "idle" | "running" | "paused_clarify" | "done" | "error" | "interrupted";

export interface TutorSession {
  id: string;
  title: string;
  created_at: string;
  status: TutorStatus;
  current_tool?: string;
  step_index?: number;
  total_steps?: number;
  last_error?: string;
  raw_text?: string;
  plan_id?: string;
  plan_version?: number;
  clarify_questions?: string[];
  cost?: number;
  updated_at: string;
}

export interface TutorIntent {
  topic: string;
  level?: string;
  goal?: string;
  daily_minutes?: number;
  deadline?: string;
  start_date?: string;
}

export interface TutorKnowledgeNode {
  id: string;
  name: string;
  difficulty: number;
  must_learn: boolean;
  parent_id?: string;
}

export interface TutorResource {
  stage_index: number;
  title: string;
  url: string;
  type: "tutorial" | "video" | "doc" | "book";
  verified: "ok" | "uncertain" | "invalid";
  note?: string;
}

export interface TutorStage {
  index: number;
  name: string;
  goal: string;
  difficulty: number;
  estimated_weeks: number;
  topics: { name: string; why?: string; must_learn: boolean }[];
  resources?: TutorResource[];
  exercises?: { desc: string; est_hours: number }[];
  milestone: string;
}

export interface TutorScheduleDay {
  date: string;
  stage_index: number;
  tasks: string[];
  est_minutes: number;
}

export interface TutorCheck {
  status: "pass" | "warn" | "fail";
  details?: string;
}

export interface TutorValidation {
  dependency_closure: TutorCheck;
  difficulty_step: TutorCheck;
  cognitive_load: TutorCheck;
  milestone_feasibility: TutorCheck;
  gap_analysis: TutorCheck;
  passed: boolean;
  fixes?: string[];
  degraded?: boolean;
}

export interface TutorPlan {
  id: string;
  session_id: string;
  version: number;
  title: string;
  topic: string;
  created_at: string;
  updated_at: string;
  intent: TutorIntent;
  knowledge_map: { nodes: TutorKnowledgeNode[]; edges?: { from: string; to: string }[] };
  stages: TutorStage[];
  schedule: TutorScheduleDay[];
  validation_report?: TutorValidation;
  metadata: { model?: string; total_tokens?: number; cost?: number; duration_ms?: number };
}

export interface TutorPlanSummary {
  id: string;
  session_id: string;
  title: string;
  topic: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface TutorPlanVersionRow {
  version: number;
  updated_at: string;
  topic: string;
  total_tokens?: number;
}

export interface TutorMaterial {
  id: string;
  session_id: string;
  kind: "file" | "text" | "url" | "image" | "audio" | "video";
  source: string;
  parse_mode?: "full" | "fast" | "none";
  status: "pending" | "confirmed" | "parsed";
  chars?: number;
  error?: string;
  created_at: string;
}

export interface TutorQuizItem {
  id: string;
  node_id: string;
  type: "choice" | "fill" | "short" | "essay";
  question: string;
  options?: string[];
  answer?: string;
  explanation?: string;
}

export interface TutorQuizSet {
  title: string;
  quiz_count: number;
  quizzes: TutorQuizItem[];
}

export interface TutorGrade {
  correct: boolean | null;
  answer?: string;
  explanation?: string;
}

export interface TutorWrongItem extends TutorQuizItem {
  plan_id?: string;
  selected?: string;
  wrong_at?: string;
}

export interface TutorQuizStats {
  attempts: number;
  correct: number;
  wrong: number;
  accuracy?: number;
  wrong_count: number;
}

export interface TutorProgressRow {
  node_id: string;
  status: "not_started" | "learning" | "done";
  mastery: number;
  next_review?: string;
  updated_at: string;
}

export interface TutorProgressOverview {
  nodes: { node_id: string; name: string; status: TutorProgressRow["status"]; mastery: number; next_review?: string }[];
  mastery_avg: number;
  done_count: number;
  total: number;
}

export interface TutorReviewItem {
  plan_id: string;
  plan_topic: string;
  node_id: string;
  name: string;
  status: string;
  mastery: number;
  next_review: string;
  difficulty?: number;
  must_learn?: boolean;
  note?: string;
}

export interface TutorStatsSummary {
  total_minutes: number;
  today_minutes: number;
  streak_days: number;
  checkin_days: number;
}

export interface TutorPlanStat {
  plan_id: string;
  topic: string;
  minutes: number;
}

export interface TutorLearningLog {
  id: string;
  plan_id: string;
  date: string;
  minutes: number;
  note?: string;
  created_at: string;
}

export interface TutorNote {
  id: string;
  title: string;
  body: string;
  created_at: string;
  updated_at: string;
}

export interface TutorNoteImportResult {
  imported: number;
  skipped: number;
  quota_full: boolean;
}

export interface TutorSearchResults {
  q: string;
  plans: { total: number; items: { id: string; topic: string; title?: string }[] };
  nodes: { total: number; items: { id: string; plan_id: string; name: string; plan_topic: string }[] };
  notes: { total: number; items: { id: string; title: string; snippet?: string }[] };
  kb_docs: { total: number; items: { id: string; title: string; snippet?: string }[] };
}

export interface TutorUsageSummary {
  today: { date: string; cost: number; runs: number };
  daily_budget: number;
  budget_enabled: boolean;
  daily: { date: string; cost: number; runs: number }[];
  total_cost: number;
  total_runs: number;
}

export interface TutorStudyUnit {
  id: string;
  order: number;
  title: string;
  stage_index?: number;
  node_sids?: string[];
  estimated_time?: string;
  objectives?: string[];
  prerequisites?: string[];
  content_md: string;
  degraded: boolean;
}

export interface TutorMaterialsSnapshot {
  unit_count: number;
  markdown: string;
  index_md: string;
  units?: TutorStudyUnit[];
}

export interface TutorMaterialsRequest {
  refresh?: boolean;
  unit_ids?: string[];
  stage_indexes?: number[];
  node_ids?: string[];
}

/** One SSE frame off a run stream: the event name, its parsed JSON payload,
 * and the id the reconnecting variant resumes after. */
export interface TutorFrame {
  id?: string;
  event: string;
  data: Record<string, unknown>;
}

export interface TutorStreamOptions {
  signal?: AbortSignal;
  lastEventId?: string;
}

/** Structured time fields the generate entry accepts alongside raw text. */
export interface TutorTimeFields {
  daily_minutes?: number;
  deadline?: string;
  start_date?: string;
  level?: string;
}

export type TutorKbKind = "file" | "text" | "url" | "image" | "audio" | "video";

export interface TutorKbDoc {
  id: string;
  title: string;
  kind: TutorKbKind;
  ext?: string;
  source: string;
  has_original: boolean;
  parsed_chars?: number;
  status: "pending" | "confirmed" | "parsed";
  parse_mode?: "full" | "fast" | "none";
  meta: {
    source_type?: string;
    parser?: string;
    truncated?: boolean;
    deduped?: boolean;
    auto_sunk?: boolean;
  };
  content_hash?: string;
  error?: string;
  created_at: string;
  updated_at: string;
}

export interface TutorKbParsedText {
  document_id: string;
  title: string;
  text: string;
  parsed_chars: number;
  truncated: boolean;
}

export interface TutorKbCitation {
  doc_id: string;
  title: string;
}

export interface TutorKbChatResult {
  record_id: string;
  answer: string;
  citations: TutorKbCitation[];
  omitted_titles: string[];
  context_docs: number;
}

export interface TutorKbChatRecord {
  id: string;
  question: string;
  answer: string;
  citations: TutorKbCitation[];
  omitted_titles: string[];
  doc_ids?: string[];
  context_docs: number;
  created_at: string;
}

export interface TutorKbGenerateResult {
  session_id: string;
  material_count: number;
}

export interface TutorShare {
  shared: boolean;
  token?: string;
  path?: string;
  created_at?: string;
}

export interface TutorBackupCounts {
  [key: string]: number | string | string[] | undefined;
}

export interface TutorShareEntry extends TutorShare {
  plan_id?: string;
  title?: string;
  topic?: string;
}

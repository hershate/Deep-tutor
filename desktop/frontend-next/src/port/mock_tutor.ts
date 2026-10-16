import { MockWorkspace } from "./mock_workspace";
import type {
  TutorFrame, TutorLearningLog, TutorMaterialsRequest, TutorMaterialsSnapshot, TutorMaterial, TutorNote,
  TutorNoteImportResult, TutorPlan, TutorPlanSummary, TutorSession, TutorStreamOptions,
  TutorKbChatRecord, TutorKbChatResult, TutorKbDoc, TutorKbGenerateResult, TutorKbParsedText,
  TutorShareEntry, TutorBackupCounts,
} from "./tutor";

// The tutor mock: a small in-memory school. One session, one plan, a quiz
// set, notes, and a scripted generate stream — enough for the demo build
// and for interaction tests, which spy on these methods anyway. It sits
// between MockWorkspace and MockPort in the chain, so the port that claims
// AgentPort inherits the whole tutor surface.
export class MockTutor extends MockWorkspace {
  private tutorSessionsState: TutorSession[] = [];
  private tutorPlansState: TutorPlan[] = [];
  private tutorNotesState: TutorNote[] = [];
  private tutorLogsState: TutorLearningLog[] = [];
  private tutorSeq = 0;

  private id() {
    this.tutorSeq += 1;
    return `mock${this.tutorSeq.toString(36)}`;
  }

  private demoPlan(sessionId: string): TutorPlan {
    return {
      id: this.id(), session_id: sessionId, version: 1,
      title: "示例学习方案", topic: "React 基础",
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      intent: { topic: "React 基础", level: "初学者", daily_minutes: 45 },
      knowledge_map: {
        nodes: [
          { id: "k1", name: "组件与 JSX", difficulty: 2, must_learn: true },
          { id: "k2", name: "状态管理", difficulty: 3, must_learn: true },
        ],
        edges: [{ from: "k1", to: "k2" }],
      },
      stages: [
        {
          index: 0, name: "入门", goal: "写出第一个组件", difficulty: 2, estimated_weeks: 1,
          topics: [{ name: "组件与 JSX", must_learn: true }],
          resources: [{
            stage_index: 0, title: "React 官方文档", url: "https://react.dev",
            type: "doc", verified: "ok",
          }],
          exercises: [{ desc: "渲染一段列表", est_hours: 1 }],
          milestone: "能独立完成一个静态页面",
        },
      ],
      schedule: [
        { date: "2026-10-15", stage_index: 0, tasks: ["阅读官方文档", "完成练习"], est_minutes: 45 },
      ],
      validation_report: {
        dependency_closure: { status: "pass" },
        difficulty_step: { status: "pass" },
        cognitive_load: { status: "warn", details: "第 2 阶段略重" },
        milestone_feasibility: { status: "pass" },
        gap_analysis: { status: "pass" },
        passed: true,
      },
      metadata: { model: "mock", total_tokens: 1200, cost: 0.01 },
    };
  }

  tutorSessions() {
    return Promise.resolve(this.tutorSessionsState.map((s) => ({ ...s })));
  }
  tutorSession(id: string) {
    const found = this.tutorSessionsState.find((s) => s.id === id);
    return found ? Promise.resolve({ ...found }) : Promise.reject(new Error("tutor.not_found"));
  }
  tutorCreateSession(title?: string) {
    const session: TutorSession = {
      id: this.id(), title: title || "新的学习目标", status: "idle",
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    };
    this.tutorSessionsState = [session, ...this.tutorSessionsState];
    return Promise.resolve({ ...session });
  }
  tutorDeleteSession(id: string) {
    this.tutorSessionsState = this.tutorSessionsState.filter((s) => s.id !== id);
    return Promise.resolve();
  }
  tutorMaterials(sid: string): Promise<TutorMaterial[]> {
    void sid;
    return Promise.resolve([]);
  }
  tutorMaterialText(sid: string, name: string, content: string): Promise<TutorMaterial> {
    void content;
    return Promise.resolve({
      id: this.id(), session_id: sid, kind: "text", source: name || "text",
      status: "confirmed", created_at: new Date().toISOString(),
    });
  }
  tutorMaterialURL(sid: string, url: string): Promise<TutorMaterial> {
    return Promise.resolve({
      id: this.id(), session_id: sid, kind: "url", source: url,
      status: "confirmed", created_at: new Date().toISOString(),
    });
  }
  tutorMaterialUpload(sid: string, filename: string): Promise<TutorMaterial> {
    return Promise.resolve({
      id: this.id(), session_id: sid, kind: "file", source: filename,
      status: "pending", created_at: new Date().toISOString(),
    });
  }
  tutorMaterialConfirm(sid: string, mid: string, parseMode: "full" | "fast" | "none"): Promise<TutorMaterial> {
    return Promise.resolve({
      id: mid, session_id: sid, kind: "file", source: "demo.md",
      parse_mode: parseMode, status: "confirmed", created_at: new Date().toISOString(),
    });
  }
  tutorMaterialParse(sid: string, mid: string): Promise<TutorMaterial> {
    return Promise.resolve({
      id: mid, session_id: sid, kind: "file", source: "demo.md",
      parse_mode: "full", status: "parsed", chars: 800, created_at: new Date().toISOString(),
    });
  }
  tutorMaterialDelete(sid: string, mid: string): Promise<void> {
    void sid; void mid;
    return Promise.resolve();
  }
  tutorCancel(sid: string): Promise<void> {
    const session = this.tutorSessionsState.find((s) => s.id === sid);
    if (session) session.status = "interrupted";
    return Promise.resolve();
  }
  async *tutorEventStream(path: string, body: unknown, opts: TutorStreamOptions = {}): AsyncGenerator<TutorFrame, void, unknown> {
    void body; void opts;
    const sid = path.split("/")[3] ?? "";
    const session = this.tutorSessionsState.find((s) => s.id === sid);
    if (session) session.status = "running";
    const frames: TutorFrame[] = [
      { id: "1", event: "tool_start", data: { tool: "understand_intent", step_index: 1, total_steps: 2 } },
      { id: "2", event: "tool_end", data: { tool: "understand_intent", status: "ok" } },
      { id: "3", event: "tool_start", data: { tool: "build_knowledge_map", step_index: 2, total_steps: 2 } },
      { id: "4", event: "tool_end", data: { tool: "build_knowledge_map", status: "ok" } },
    ];
    for (const frame of frames) {
      if (opts.signal?.aborted) return;
      await new Promise((r) => setTimeout(r, 40));
      yield frame;
    }
    const plan = this.demoPlan(sid);
    this.tutorPlansState = [plan, ...this.tutorPlansState];
    if (session) {
      session.status = "done";
      session.plan_id = plan.id;
    }
    yield { id: "5", event: "done", data: { plan_id: plan.id } };
  }
  tutorPlans(): Promise<TutorPlanSummary[]> {
    return Promise.resolve(this.tutorPlansState.map((p) => ({
      id: p.id, session_id: p.session_id, title: p.title, topic: p.topic,
      version: p.version, created_at: p.created_at, updated_at: p.updated_at,
    })));
  }
  tutorPlan(id: string): Promise<TutorPlan> {
    const found = this.tutorPlansState.find((p) => p.id === id);
    return found ? Promise.resolve(structuredClone(found)) : Promise.reject(new Error("tutor.not_found"));
  }
  tutorPlanVersions(id: string) {
    const plan = this.tutorPlansState.find((p) => p.id === id);
    return Promise.resolve(plan
      ? [{ version: plan.version, updated_at: plan.updated_at, topic: plan.topic, total_tokens: plan.metadata.total_tokens }]
      : []);
  }
  tutorPlanVersion(id: string) {
    return this.tutorPlan(id);
  }
  tutorPlanMarkdown(id: string) {
    return this.tutorPlan(id).then((p) => `# ${p.title}\n\n${p.topic}`);
  }
  tutorPlanICS(id: string) {
    return this.tutorPlan(id).then(() => "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n");
  }
  tutorPlanPDF(id: string) {
    return this.tutorPlanMarkdown(id).then((content) => ({ content, degraded: true }));
  }
  tutorPlanApkg(id: string, wrongOnly = false) {
    void id; void wrongOnly;
    return Promise.resolve(new Blob([new Uint8Array([0x50, 0x4b, 0x03, 0x04])], { type: "application/octet-stream" }));
  }
  tutorPlanSlides(id: string) {
    void id;
    return Promise.resolve("<!DOCTYPE html><html><body>slides</body></html>");
  }
  tutorUnitSlides(pid: string, uid: string) {
    void pid; void uid;
    return Promise.resolve("<!DOCTYPE html><html><body>unit slides</body></html>");
  }
  tutorStudyMaterials(pid: string, req: TutorMaterialsRequest): Promise<TutorMaterialsSnapshot> {
    void pid; void req;
    return Promise.resolve({ unit_count: 0, markdown: "", index_md: "" });
  }
  tutorQuizzes(pid: string) {
    return this.tutorQuizGenerate(pid);
  }
  tutorQuizGenerate(pid: string) {
    return this.tutorPlan(pid).then((p) => ({
      title: p.topic,
      quiz_count: 2,
      quizzes: [
        { id: this.id(), node_id: "k1", type: "choice" as const, question: "React 组件返回什么？", options: ["JSX", "JSON", "XML"], answer: "JSX", explanation: "JSX 是组件的模板语法" },
        { id: this.id(), node_id: "k2", type: "short" as const, question: "用自己的话说说什么是状态", answer: "组件随时间变化的数据" },
      ],
    }));
  }
  tutorQuizSubmit(qid: string, planId: string, selected?: string) {
    void qid; void planId;
    const correct = selected === "JSX";
    return Promise.resolve({ correct, answer: "JSX", explanation: "JSX 是组件的模板语法" });
  }
  tutorQuizStats(pid: string) {
    void pid;
    return Promise.resolve({ attempts: 1, correct: 1, wrong: 0, accuracy: 1, wrong_count: 0 });
  }
  tutorQuizWrong(pid: string) {
    void pid;
    return Promise.resolve({ items: [], total: 0 });
  }
  tutorWrongOverview() {
    return Promise.resolve({ total: 0, items: [] });
  }
  tutorWrongQueue() {
    return Promise.resolve({ items: [], total: 0 });
  }
  tutorProgressRecord(planId: string, nodeId: string, _status: string, quizScore?: number) {
    void planId;
    return Promise.resolve({
      node_id: nodeId, status: "learning" as const, mastery: quizScore ?? 0.5, updated_at: new Date().toISOString(),
    });
  }
  tutorProgressOf(pid: string) {
    return this.tutorPlan(pid).then((p) => ({
      nodes: p.knowledge_map.nodes.map((n) => ({ node_id: n.id, name: n.name, status: "not_started" as const, mastery: 0 })),
      mastery_avg: 0, done_count: 0, total: p.knowledge_map.nodes.length,
    }));
  }
  tutorReviewDue() {
    return Promise.resolve({ items: [], total: 0 });
  }
  tutorReviewQueue() {
    const items = this.tutorPlansState.flatMap((p) =>
      p.knowledge_map.nodes.map((n) => ({
        plan_id: p.id, plan_topic: p.topic, node_id: n.id, name: n.name,
        status: "not_started", mastery: 0, next_review: "",
      })));
    return Promise.resolve({ items, total: items.length });
  }
  tutorStatsSummary() {
    const total = this.tutorLogsState.reduce((sum, l) => sum + l.minutes, 0);
    return Promise.resolve({ total_minutes: total, today_minutes: total, streak_days: 0, checkin_days: 0 });
  }
  tutorStatsCalendar() {
    return Promise.resolve({});
  }
  tutorStatsByPlan() {
    return Promise.resolve(this.tutorPlansState.map((p) => ({ plan_id: p.id, topic: p.topic, minutes: 0 })));
  }
  tutorLogs(planId?: string) {
    return Promise.resolve(planId ? this.tutorLogsState.filter((l) => l.plan_id === planId) : this.tutorLogsState);
  }
  tutorLogAdd(planId: string, minutes: number, note?: string) {
    const log: TutorLearningLog = {
      id: this.id(), plan_id: planId, date: new Date().toISOString().slice(0, 10),
      minutes, note, created_at: new Date().toISOString(),
    };
    this.tutorLogsState = [log, ...this.tutorLogsState];
    return Promise.resolve(log);
  }
  tutorLogDelete(planId: string, logId: string) {
    this.tutorLogsState = this.tutorLogsState.filter((l) => !(l.plan_id === planId && l.id === logId));
    return Promise.resolve();
  }
  tutorNotes() {
    return Promise.resolve(this.tutorNotesState.map((n) => ({ ...n })));
  }
  tutorNoteCreate(title: string, body: string) {
    const note: TutorNote = {
      id: this.id(), title, body,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    };
    this.tutorNotesState = [note, ...this.tutorNotesState];
    return Promise.resolve({ ...note });
  }
  tutorNote(id: string) {
    const found = this.tutorNotesState.find((n) => n.id === id);
    return found ? Promise.resolve({ ...found }) : Promise.reject(new Error("tutor.not_found"));
  }
  tutorNoteSave(id: string, title: string, body: string) {
    const found = this.tutorNotesState.find((n) => n.id === id);
    if (!found) return Promise.reject(new Error("tutor.not_found"));
    found.title = title;
    found.body = body;
    found.updated_at = new Date().toISOString();
    return Promise.resolve({ ...found });
  }
  tutorNoteDelete(id: string) {
    this.tutorNotesState = this.tutorNotesState.filter((n) => n.id !== id);
    return Promise.resolve();
  }
  async tutorNotesZip(): Promise<Blob> {
    return new Blob([JSON.stringify(this.tutorNotesState)], { type: "application/zip" });
  }
  tutorNotesImport(files: { name: string; contentBase64: string }[]): Promise<TutorNoteImportResult> {
    for (const f of files) {
      this.tutorNotesState = [{
        id: this.id(), title: f.name, body: atob(f.contentBase64),
        created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      }, ...this.tutorNotesState];
    }
    return Promise.resolve({ imported: files.length, skipped: 0, quota_full: false });
  }
  tutorSearch(q: string) {
    const lower = q.toLowerCase();
    const notes = this.tutorNotesState.filter((n) => n.title.toLowerCase().includes(lower) || n.body.toLowerCase().includes(lower));
    const kb = this.kbDocsState.filter((d) => d.title.toLowerCase().includes(lower));
    return Promise.resolve({
      q,
      plans: { total: 0, items: [] },
      nodes: { total: 0, items: [] },
      notes: { total: notes.length, items: notes.slice(0, 10).map((n) => ({ id: n.id, title: n.title })) },
      kb_docs: { total: kb.length, items: kb.slice(0, 10).map((d) => ({ id: d.id, title: d.title })) },
    });
  }
  tutorUsage() {
    const cost = this.tutorPlansState.reduce((sum, p) => sum + (p.metadata.cost ?? 0), 0);
    const today = { date: new Date().toISOString().slice(0, 10), cost, runs: this.tutorPlansState.length };
    return Promise.resolve({
      today, daily_budget: 10, budget_enabled: true, daily: [today], total_cost: cost, total_runs: today.runs,
    });
  }

  // ── Knowledge base ──

  private kbDocsState: TutorKbDoc[] = [];
  private kbChatsState: TutorKbChatRecord[] = [];
  private tutorSharesState: TutorShareEntry[] = [];
  private shareTokenByPlan = new Map<string, string>();

  private kbDoc(id: string) {
    const found = this.kbDocsState.find((d) => d.id === id);
    if (!found) throw new Error("tutor.not_found");
    return found;
  }

  tutorKbUpload(filename: string): Promise<TutorKbDoc> {
    const doc: TutorKbDoc = {
      id: this.id(), title: filename, kind: "file",
      ext: filename.split(".").pop()?.toLowerCase() ?? "", source: filename,
      has_original: true, status: "pending", meta: {},
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    };
    this.kbDocsState = [doc, ...this.kbDocsState];
    return Promise.resolve({ ...doc });
  }
  tutorKbAddText(title: string, text: string): Promise<TutorKbDoc> {
    const doc: TutorKbDoc = {
      id: this.id(), title: title || text.split("\n")[0].slice(0, 40), kind: "text",
      ext: "txt", source: text.slice(0, 200), has_original: false,
      parsed_chars: text.length, status: "parsed", meta: { source_type: "text", parser: "raw" },
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    };
    this.kbDocsState = [doc, ...this.kbDocsState];
    return Promise.resolve({ ...doc });
  }
  tutorKbAddURL(url: string, title?: string): Promise<TutorKbDoc> {
    const doc: TutorKbDoc = {
      id: this.id(), title: title || url.slice(0, 80), kind: "url",
      source: url, has_original: false, status: "parsed",
      parsed_chars: 1200, meta: { source_type: "url", parser: "html" },
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    };
    this.kbDocsState = [doc, ...this.kbDocsState];
    return Promise.resolve({ ...doc });
  }
  tutorKbDocs(q?: string, kind?: string): Promise<TutorKbDoc[]> {
    let docs = this.kbDocsState;
    if (kind) docs = docs.filter((d) => d.kind === kind);
    if (q) {
      const lower = q.toLowerCase();
      docs = docs.filter((d) => d.title.toLowerCase().includes(lower));
    }
    return Promise.resolve(docs.map((d) => ({ ...d })));
  }
  tutorKbDoc(id: string) {
    return Promise.resolve({ ...this.kbDoc(id) });
  }
  tutorKbRename(id: string, title: string) {
    const doc = this.kbDoc(id);
    doc.title = title;
    doc.updated_at = new Date().toISOString();
    return Promise.resolve({ ...doc });
  }
  tutorKbDelete(id: string) {
    this.kbDocsState = this.kbDocsState.filter((d) => d.id !== id);
    return Promise.resolve();
  }
  tutorKbConfirm(id: string) {
    const doc = this.kbDoc(id);
    if (doc.status === "pending") doc.status = "confirmed";
    return Promise.resolve({ ...doc });
  }
  tutorKbParse(id: string, mode: "full" | "fast" = "full") {
    const doc = this.kbDoc(id);
    doc.status = "parsed";
    doc.parse_mode = mode;
    doc.parsed_chars = doc.parsed_chars || 800;
    doc.updated_at = new Date().toISOString();
    return Promise.resolve({ ...doc });
  }
  tutorKbRawHref(id: string) {
    return `/tutor/kb/documents/${id}/raw`;
  }
  tutorKbParsed(id: string): Promise<TutorKbParsedText> {
    const doc = this.kbDoc(id);
    return Promise.resolve({
      document_id: doc.id, title: doc.title,
      text: `# ${doc.title}\n\n这是演示文档的解析文本。`, parsed_chars: doc.parsed_chars ?? 0, truncated: false,
    });
  }
  async tutorKbExportZip(): Promise<Blob> {
    return new Blob([JSON.stringify(this.kbDocsState)], { type: "application/zip" });
  }
  tutorKbGenerate(documentIds: string[], title?: string): Promise<TutorKbGenerateResult> {
    const session = {
      id: this.id(), title: title || "知识库方案", status: "idle" as const,
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    };
    this.tutorSessionsState = [session, ...this.tutorSessionsState];
    return Promise.resolve({ session_id: session.id, material_count: documentIds.length });
  }
  tutorKbChat(question: string, documentIds?: string[]): Promise<TutorKbChatResult> {
    const docs = documentIds?.length
      ? documentIds.map((id) => this.kbDoc(id))
      : this.kbDocsState.filter((d) => d.status === "parsed").slice(0, 20);
    const record: TutorKbChatRecord = {
      id: this.id(), question,
      answer: docs.length
        ? `根据《${docs[0].title}》：这是一段演示回答，仅依据所选文档作答。`
        : "知识库文档中没有足够信息。",
      citations: docs.length ? [{ doc_id: docs[0].id, title: docs[0].title }] : [],
      omitted_titles: [], context_docs: docs.length,
      created_at: new Date().toISOString(),
    };
    this.kbChatsState = [record, ...this.kbChatsState];
    return Promise.resolve({
      record_id: record.id, answer: record.answer, citations: record.citations,
      omitted_titles: [], context_docs: record.context_docs,
    });
  }
  tutorKbChats(q?: string) {
    const items = q ? this.kbChatsState.filter((r) => r.question.includes(q)) : this.kbChatsState;
    return Promise.resolve({ items: items.map((r) => ({ ...r })), total: items.length });
  }
  tutorKbChatDelete(id: string) {
    this.kbChatsState = this.kbChatsState.filter((r) => r.id !== id);
    return Promise.resolve();
  }

  tutorShareCreate(planId: string) {
    const existing = this.tutorSharesState.find((x) => x.token === this.shareTokenByPlan.get(planId));
    if (existing) return Promise.resolve({ ...existing });
    const token = `share${this.id()}`;
    const plan = this.tutorPlansState.find((p) => p.id === planId);
    const share: TutorShareEntry = {
      shared: true, token, path: `/tutor/shared/${token}`,
      created_at: new Date().toISOString(),
      plan_id: planId, title: plan?.title ?? planId, topic: plan?.topic ?? "",
    };
    this.shareTokenByPlan.set(planId, token);
    this.tutorSharesState = [share, ...this.tutorSharesState];
    return Promise.resolve({ ...share });
  }
  tutorShareStatus(planId: string) {
    const token = this.shareTokenByPlan.get(planId);
    const found = token ? this.tutorSharesState.find((x) => x.token === token) : undefined;
    return Promise.resolve(found ? { ...found } : { shared: false });
  }
  tutorShareRevoke(planId: string) {
    const token = this.shareTokenByPlan.get(planId);
    if (token) this.tutorSharesState = this.tutorSharesState.filter((x) => x.token !== token);
    this.shareTokenByPlan.delete(planId);
    return Promise.resolve();
  }
  tutorShares() {
    return Promise.resolve({ shares: this.tutorSharesState.map((x) => ({ ...x })) });
  }

  tutorBackupExport() {
    const doc = JSON.stringify({
      format: "reasonix-tutor-backup", version: 1, exported_at: new Date().toISOString(),
      sessions: this.tutorSessionsState.map((s) => ({ title: s.title, plans: [] })),
      notes: this.tutorNotesState.map((n) => ({ title: n.title, body: n.body })),
      kb: this.kbDocsState.filter((d) => d.status === "parsed").map((d) => ({ title: d.title, parsed_text: "" })),
    });
    return Promise.resolve(new Blob([doc], { type: "application/json" }));
  }
  tutorBackupPreview() {
    return Promise.resolve({
      sessions: this.tutorSessionsState.length, materials: 0, plans: this.tutorPlansState.length,
      knowledge_nodes: 0, quizzes: 0, attempts: 0, progress: 0, learning_logs: 0,
      study_units: 0, notes: this.tutorNotesState.length,
      kb_documents: this.kbDocsState.filter((d) => d.status === "parsed").length,
      exported_at: "", skipped: [],
    } as TutorBackupCounts);
  }
  tutorBackupImport() {
    return this.tutorBackupPreview();
  }
}

import { SseWorkspace } from "./sse_workspace";
import { SseHttp } from "./sse_http";
import type {
  TutorFrame, TutorStreamOptions, TutorLearningLog, TutorMaterialsRequest, TutorMaterialsSnapshot,
  TutorMaterial, TutorNote, TutorNoteImportResult, TutorPlan, TutorPlanSummary, TutorPlanVersionRow,
  TutorProgressOverview, TutorProgressRow, TutorQuizSet, TutorQuizStats, TutorReviewItem, TutorSearchResults,
  TutorSession, TutorStatsSummary, TutorGrade, TutorUsageSummary, TutorWrongItem,
  TutorKbChatRecord, TutorKbChatResult, TutorKbDoc, TutorKbGenerateResult, TutorKbParsedText,
  TutorShare, TutorShareEntry, TutorBackupCounts,
} from "./tutor";

// The tutor subject: every /tutor/* read and write except the run streams,
// which arrive through tutorEventStream below. Generation is a POST whose
// answer is an SSE body, so EventSource cannot carry it; this reader walks
// the response stream frame by frame instead and the run endpoints resume
// through Last-Event-ID.
export class SseTutor extends SseWorkspace {
  // A list endpoint may answer null for "empty" on older hosts; the sheet
  // reads .length without a null check, so null never leaves the port.
  private async list<T>(path: string): Promise<T[]> {
    const items = await this.get<T[] | null>(path);
    return items ?? [];
  }
  tutorSessions() {
    return this.list<TutorSession>("/tutor/sessions");
  }
  tutorSession(id: string) {
    return this.get<TutorSession>("/tutor/sessions/" + encodeURIComponent(id));
  }
  tutorCreateSession(title?: string) {
    return this.post0<TutorSession>("/tutor/sessions", { title });
  }
  tutorDeleteSession(id: string) {
    return this.del0<void>("/tutor/sessions/" + encodeURIComponent(id));
  }
  tutorMaterials(sid: string) {
    return this.list<TutorMaterial>("/tutor/sessions/" + encodeURIComponent(sid) + "/materials");
  }
  tutorMaterialText(sid: string, name: string, content: string) {
    return this.post0<TutorMaterial>(`/tutor/sessions/${encodeURIComponent(sid)}/materials/text`, { name, content });
  }
  tutorMaterialURL(sid: string, url: string) {
    return this.post0<TutorMaterial>(`/tutor/sessions/${encodeURIComponent(sid)}/materials/url`, { url });
  }
  tutorMaterialUpload(sid: string, filename: string, contentBase64: string) {
    return this.post0<TutorMaterial>(`/tutor/sessions/${encodeURIComponent(sid)}/materials/upload`, {
      filename, content_base64: contentBase64,
    });
  }
  tutorMaterialConfirm(sid: string, mid: string, parseMode: "full" | "fast" | "none") {
    return this.post0<TutorMaterial>(`/tutor/sessions/${encodeURIComponent(sid)}/materials/${encodeURIComponent(mid)}/confirm`, { parse_mode: parseMode });
  }
  tutorMaterialParse(sid: string, mid: string) {
    return this.post0<TutorMaterial>(`/tutor/sessions/${encodeURIComponent(sid)}/materials/${encodeURIComponent(mid)}/parse`, {});
  }
  tutorMaterialDelete(sid: string, mid: string) {
    return this.del0<void>(`/tutor/sessions/${encodeURIComponent(sid)}/materials/${encodeURIComponent(mid)}`);
  }
  tutorCancel(sid: string) {
    return this.post0<void>(`/tutor/sessions/${encodeURIComponent(sid)}/cancel`, {});
  }
  tutorPlans() {
    return this.list<TutorPlanSummary>("/tutor/plans");
  }
  tutorPlan(id: string) {
    return this.get<TutorPlan>("/tutor/plans/" + encodeURIComponent(id));
  }
  tutorPlanVersions(id: string) {
    const versions = this.get<{ versions: TutorPlanVersionRow[] }>("/tutor/plans/" + encodeURIComponent(id) + "/versions");
    return versions.then((r) => r.versions);
  }
  tutorPlanVersion(id: string, v: number) {
    return this.get<TutorPlan>(`/tutor/plans/${encodeURIComponent(id)}/versions/${v}`);
  }
  async tutorPlanMarkdown(id: string) {
    const res = await fetch(`/tutor/plans/${encodeURIComponent(id)}/export.md`, { credentials: "same-origin" });
    if (!res.ok) await SseHttp.fail("export.md", res);
    return res.text();
  }
  async tutorPlanICS(id: string) {
    const res = await fetch(`/tutor/plans/${encodeURIComponent(id)}/export.ics`, { credentials: "same-origin" });
    if (!res.ok) await SseHttp.fail("export.ics", res);
    return res.text();
  }
  async tutorPlanPDF(id: string) {
    const res = await fetch(`/tutor/plans/${encodeURIComponent(id)}/export.pdf`, { credentials: "same-origin" });
    if (!res.ok) await SseHttp.fail("export.pdf", res);
    return { content: await res.text(), degraded: !!res.headers.get("X-Tutor-Export-Warning") };
  }
  async tutorPlanApkg(id: string, wrongOnly = false) {
    const q = wrongOnly ? "?wrong_only=1" : "";
    const res = await fetch(`/tutor/plans/${encodeURIComponent(id)}/export.apkg${q}`, { credentials: "same-origin" });
    if (!res.ok) await SseHttp.fail("export.apkg", res);
    return res.blob();
  }
  async tutorPlanSlides(id: string) {
    const res = await fetch(`/tutor/plans/${encodeURIComponent(id)}/export.html`, { credentials: "same-origin" });
    if (!res.ok) await SseHttp.fail("export.html", res);
    return res.text();
  }
  async tutorUnitSlides(pid: string, uid: string) {
    const res = await fetch(
      `/tutor/plans/${encodeURIComponent(pid)}/units/${encodeURIComponent(uid)}/export.html`,
      { credentials: "same-origin" },
    );
    if (!res.ok) await SseHttp.fail("units/export.html", res);
    return res.text();
  }
  tutorStudyMaterials(pid: string, req: TutorMaterialsRequest) {
    return this.post0<TutorMaterialsSnapshot>(`/tutor/plans/${encodeURIComponent(pid)}/materials`, req);
  }
  tutorQuizzes(pid: string) {
    return this.get<TutorQuizSet>(`/tutor/plans/${encodeURIComponent(pid)}/quiz`);
  }
  tutorQuizGenerate(pid: string, refresh = false) {
    return this.post0<TutorQuizSet>(`/tutor/plans/${encodeURIComponent(pid)}/quiz`, { refresh });
  }
  tutorQuizSubmit(qid: string, planId: string, selected?: string, text?: string) {
    return this.post0<TutorGrade>(`/tutor/quizzes/${encodeURIComponent(qid)}/submit`, {
      plan_id: planId, selected, text,
    });
  }
  tutorQuizStats(pid: string) {
    return this.get<TutorQuizStats>(`/tutor/plans/${encodeURIComponent(pid)}/quiz/stats`);
  }
  tutorQuizWrong(pid: string) {
    return this.get<{ items: TutorWrongItem[]; total: number }>(`/tutor/plans/${encodeURIComponent(pid)}/quiz/wrong`);
  }
  tutorWrongOverview() {
    return this.get<{ total: number; items: { plan_id: string; topic: string; count: number }[] }>("/tutor/quizzes/wrong-overview");
  }
  tutorWrongQueue(limit?: number) {
    const q = limit ? `?limit=${limit}` : "";
    return this.get<{ items: TutorWrongItem[]; total: number }>("/tutor/quizzes/wrong-queue" + q);
  }
  tutorProgressRecord(planId: string, nodeId: string, status: string, quizScore?: number) {
    return this.post0<TutorProgressRow>("/tutor/progress", {
      plan_id: planId, node_id: nodeId, status, quiz_score: quizScore,
    });
  }
  tutorProgressOf(pid: string) {
    return this.get<TutorProgressOverview>(`/tutor/plans/${encodeURIComponent(pid)}/progress`);
  }
  tutorReviewDue(limit?: number) {
    const q = limit ? `?limit=${limit}` : "";
    return this.get<{ items: TutorReviewItem[]; total: number }>("/tutor/progress/review/due" + q);
  }
  tutorReviewQueue(limit?: number) {
    const q = limit ? `?limit=${limit}` : "";
    return this.get<{ items: TutorReviewItem[]; total: number }>("/tutor/progress/review/queue" + q);
  }
  tutorStatsSummary() {
    return this.get<TutorStatsSummary>("/tutor/progress/stats/summary");
  }
  tutorStatsCalendar() {
    return this.get<Record<string, number>>("/tutor/progress/stats/calendar");
  }
  tutorStatsByPlan() {
    return this.list<TutorPlanStatLike>("/tutor/progress/stats/by-plan");
  }
  tutorLogs(planId?: string) {
    const q = planId ? `?plan_id=${encodeURIComponent(planId)}` : "";
    return this.list<TutorLearningLog>("/tutor/progress/logs" + q);
  }
  tutorLogAdd(planId: string, minutes: number, note?: string, date?: string) {
    return this.post0<TutorLearningLog>("/tutor/progress/logs", {
      plan_id: planId, minutes, note, date,
    });
  }
  tutorLogDelete(planId: string, logId: string) {
    return this.del0<void>(`/tutor/progress/logs/${encodeURIComponent(logId)}?plan_id=${encodeURIComponent(planId)}`);
  }
  tutorNotes() {
    return this.list<TutorNote>("/tutor/notes");
  }
  tutorNoteCreate(title: string, body: string) {
    return this.post0<TutorNote>("/tutor/notes", { title, body });
  }
  tutorNote(id: string) {
    return this.get<TutorNote>("/tutor/notes/" + encodeURIComponent(id));
  }
  tutorNoteSave(id: string, title: string, body: string) {
    return this.put0<TutorNote>("/tutor/notes/" + encodeURIComponent(id), { title, body });
  }
  tutorNoteDelete(id: string) {
    return this.del0<void>("/tutor/notes/" + encodeURIComponent(id));
  }
  async tutorNotesZip() {
    const res = await fetch("/tutor/notes/export.zip", { credentials: "same-origin" });
    if (!res.ok) await SseHttp.fail("export.zip", res);
    return res.blob();
  }
  tutorNotesImport(files: { name: string; contentBase64: string }[]) {
    return this.post0<TutorNoteImportResult>("/tutor/notes/import", { files });
  }
  tutorSearch(q: string, limit?: number) {
    const qs = `?q=${encodeURIComponent(q)}${limit ? `&limit=${limit}` : ""}`;
    return this.get<TutorSearchResults>("/tutor/search" + qs);
  }
  tutorUsage(days?: number) {
    const q = days ? `?days=${days}` : "";
    return this.get<TutorUsageSummary>("/tutor/usage" + q);
  }

  // ── Knowledge base ──

  tutorKbUpload(filename: string, contentBase64: string) {
    return this.post0<TutorKbDoc>("/tutor/kb/documents/upload", {
      filename, content_base64: contentBase64,
    });
  }
  tutorKbAddText(title: string, text: string) {
    return this.post0<TutorKbDoc>("/tutor/kb/documents/text", { title, text });
  }
  tutorKbAddURL(url: string, title?: string) {
    return this.post0<TutorKbDoc>("/tutor/kb/documents/url", { url, title });
  }
  tutorKbDocs(q?: string, kind?: string, limit?: number) {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (kind) params.set("kind", kind);
    if (limit) params.set("limit", String(limit));
    const qs = params.size ? `?${params.toString()}` : "";
    return this.get<{ documents: TutorKbDoc[] }>("/tutor/kb/documents" + qs)
      .then((r) => r.documents);
  }
  tutorKbDoc(id: string) {
    return this.get<TutorKbDoc>(`/tutor/kb/documents/${encodeURIComponent(id)}`);
  }
  tutorKbRename(id: string, title: string) {
    return this.put0<TutorKbDoc>(`/tutor/kb/documents/${encodeURIComponent(id)}`, { title });
  }
  tutorKbDelete(id: string) {
    return this.del0<void>(`/tutor/kb/documents/${encodeURIComponent(id)}`);
  }
  tutorKbConfirm(id: string) {
    return this.post0<TutorKbDoc>(`/tutor/kb/documents/${encodeURIComponent(id)}/confirm`, {});
  }
  tutorKbParse(id: string, mode: "full" | "fast" = "full") {
    return this.post0<TutorKbDoc>(`/tutor/kb/documents/${encodeURIComponent(id)}/parse`, { mode });
  }
  tutorKbRawHref(id: string, download = false) {
    return `/tutor/kb/documents/${encodeURIComponent(id)}/raw${download ? "?download=1" : ""}`;
  }
  tutorKbParsed(id: string) {
    return this.get<TutorKbParsedText>(`/tutor/kb/documents/${encodeURIComponent(id)}/parsed`);
  }
  async tutorKbExportZip(ids?: string[]) {
    const qs = ids?.length ? `?ids=${ids.map(encodeURIComponent).join(",")}` : "";
    const res = await fetch("/tutor/kb/export" + qs, { credentials: "same-origin" });
    if (!res.ok) await SseHttp.fail("kb/export", res);
    return res.blob();
  }
  tutorKbGenerate(documentIds: string[], title?: string) {
    return this.post0<TutorKbGenerateResult>("/tutor/kb/generate", {
      document_ids: documentIds, title,
    });
  }
  tutorKbChat(question: string, documentIds?: string[]) {
    return this.post0<TutorKbChatResult>("/tutor/kb/chat", {
      question, document_ids: documentIds,
    });
  }
  tutorKbChats(q?: string, limit?: number, offset?: number) {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (limit) params.set("limit", String(limit));
    if (offset) params.set("offset", String(offset));
    const qs = params.size ? `?${params.toString()}` : "";
    return this.get<{ items: TutorKbChatRecord[]; total: number }>("/tutor/kb/chats" + qs);
  }
  tutorKbChatDelete(id: string) {
    return this.del0<void>(`/tutor/kb/chats/${encodeURIComponent(id)}`);
  }

  tutorShareCreate(planId: string) {
    return this.post0<TutorShare>(`/tutor/plans/${encodeURIComponent(planId)}/share`, {});
  }
  tutorShareStatus(planId: string) {
    return this.get<TutorShare>(`/tutor/plans/${encodeURIComponent(planId)}/share`);
  }
  tutorShareRevoke(planId: string) {
    return this.del0<void>(`/tutor/plans/${encodeURIComponent(planId)}/share`);
  }
  tutorShares() {
    return this.get<{ shares: TutorShareEntry[] }>("/tutor/shares");
  }

  async tutorBackupExport() {
    const res = await fetch("/tutor/backup/export", { credentials: "same-origin" });
    if (!res.ok) await SseHttp.fail("backup/export", res);
    return res.blob();
  }
  tutorBackupPreview(contentBase64: string) {
    return this.post0<TutorBackupCounts>("/tutor/backup/preview", { content_base64: contentBase64 });
  }
  tutorBackupImport(contentBase64: string) {
    return this.post0<TutorBackupCounts>("/tutor/backup/import", { content_base64: contentBase64 });
  }

  /** One connection to a run stream: POST with an SSE answer. Yields frames
   * until the stream ends (the kernel closes after the terminal event) or
   * the signal aborts. Reconnect policy belongs to the caller, which knows
   * which endpoints are idempotent. */
  async *tutorEventStream(path: string, body: unknown, opts: TutorStreamOptions = {}): AsyncGenerator<TutorFrame> {
    const headers: Record<string, string> = { "content-type": "application/json", accept: "text/event-stream" };
    if (opts.lastEventId) headers["last-event-id"] = opts.lastEventId;
    const res = await fetch(this.base + path, {
      method: "POST",
      headers,
      credentials: "same-origin",
      body: JSON.stringify(body ?? {}),
      signal: opts.signal,
    });
    if (!res.ok || !res.body) await SseHttp.fail(path, res);
    const reader = res.body!.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let event = "";
    let data = "";
    let id: string | undefined;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let at;
      while ((at = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, at).replace(/\r$/, "");
        buf = buf.slice(at + 1);
        if (line === "") {
          if (event || data) {
            let payload: Record<string, unknown> = {};
            if (data.trim() !== "") {
              try {
                payload = JSON.parse(data) as Record<string, unknown>;
              } catch {
                payload = { message: data };
              }
            }
            yield { id, event: event || "message", data: payload };
          }
          event = "";
          data = "";
          continue;
        }
        if (line.startsWith(":")) continue;
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
        else if (line.startsWith("id:")) id = line.slice(3).trim();
      }
    }
  }
}

type TutorPlanStatLike = { plan_id: string; topic: string; minutes: number };

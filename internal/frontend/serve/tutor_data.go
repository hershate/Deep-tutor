// tutor_data.go — the tutor's persisted resources over HTTP: plans and
// versions, quizzes with server grading and the wrong-book, progress with
// spaced review, learning logs, notes, and plan export.
package serve

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"reasonix/internal/runtime/tutor"
)

func (s *Server) registerTutorDataRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /tutor/plans", s.tutorPlans)
	mux.HandleFunc("GET /tutor/plans/{pid}", s.tutorPlan)
	mux.HandleFunc("DELETE /tutor/plans/{pid}", s.tutorPlanDelete)
	mux.HandleFunc("GET /tutor/plans/{pid}/versions", s.tutorPlanVersions)
	mux.HandleFunc("GET /tutor/plans/{pid}/versions/{v}", s.tutorPlanVersion)
	mux.HandleFunc("GET /tutor/plans/{pid}/export.md", s.tutorPlanExport)
	mux.HandleFunc("GET /tutor/plans/{pid}/export.ics", s.tutorPlanExportICS)
	mux.HandleFunc("GET /tutor/plans/{pid}/export.pdf", s.tutorPlanExportPDF)
	mux.HandleFunc("GET /tutor/plans/{pid}/export.apkg", s.tutorPlanExportApkg)
	mux.HandleFunc("GET /tutor/plans/{pid}/export.html", s.tutorPlanExportSlides)
	mux.HandleFunc("GET /tutor/plans/{pid}/units/{uid}/export.html", s.tutorUnitExportSlides)

	mux.HandleFunc("POST /tutor/plans/{pid}/materials", s.tutorPlanMaterials)

	mux.HandleFunc("POST /tutor/plans/{pid}/quiz", s.tutorQuizGenerate)
	mux.HandleFunc("GET /tutor/plans/{pid}/quiz", s.tutorQuizzes)
	mux.HandleFunc("GET /tutor/plans/{pid}/quiz/wrong", s.tutorQuizWrong)
	mux.HandleFunc("GET /tutor/plans/{pid}/quiz/stats", s.tutorQuizStats)
	mux.HandleFunc("POST /tutor/quizzes/{qid}/submit", s.tutorQuizSubmit)
	mux.HandleFunc("GET /tutor/quizzes/wrong-overview", s.tutorWrongOverview)
	mux.HandleFunc("GET /tutor/quizzes/wrong-queue", s.tutorWrongQueue)

	mux.HandleFunc("POST /tutor/progress", s.tutorProgressRecord)
	mux.HandleFunc("GET /tutor/plans/{pid}/progress", s.tutorProgressOf)
	mux.HandleFunc("GET /tutor/progress/review/due", s.tutorReviewDue)
	mux.HandleFunc("GET /tutor/progress/review/queue", s.tutorReviewQueue)
	mux.HandleFunc("GET /tutor/progress/stats/summary", s.tutorStatsSummary)
	mux.HandleFunc("GET /tutor/progress/stats/calendar", s.tutorStatsCalendar)
	mux.HandleFunc("GET /tutor/progress/stats/by-plan", s.tutorStatsByPlan)
	mux.HandleFunc("GET /tutor/progress/logs", s.tutorLogs)
	mux.HandleFunc("POST /tutor/progress/logs", s.tutorLogAdd)
	mux.HandleFunc("DELETE /tutor/progress/logs/{lid}", s.tutorLogDelete)

	mux.HandleFunc("GET /tutor/notes", s.tutorNotes)
	mux.HandleFunc("POST /tutor/notes", s.tutorNoteCreate)
	mux.HandleFunc("GET /tutor/notes/export.zip", s.tutorNotesExportZip)
	mux.HandleFunc("POST /tutor/notes/import", s.tutorNotesImport)
	mux.HandleFunc("GET /tutor/notes/{nid}", s.tutorNote)
	mux.HandleFunc("PUT /tutor/notes/{nid}", s.tutorNoteSave)
	mux.HandleFunc("DELETE /tutor/notes/{nid}", s.tutorNoteDelete)

	mux.HandleFunc("GET /tutor/search", s.tutorSearch)
	mux.HandleFunc("GET /tutor/usage", s.tutorUsage)
}

func (s *Server) tutorPlans(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	plans, err := svc.Store().Plans()
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	if q := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("q"))); q != "" {
		kept := plans[:0]
		for _, p := range plans {
			if strings.Contains(strings.ToLower(p.Title), q) || strings.Contains(strings.ToLower(p.Topic), q) {
				kept = append(kept, p)
			}
		}
		plans = kept
	}
	writeJSON(w, tutorList(plans))
}

func (s *Server) tutorPlan(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	plan, err := svc.Store().Plan(r.PathValue("pid"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, plan)
}

func (s *Server) tutorPlanDelete(w http.ResponseWriter, r *http.Request) {
	refuse(w, http.StatusNotImplemented, "tutor.plan_delete_unsupported", "plan deletion arrives with the workspace surface", nil)
}

func (s *Server) tutorPlanVersions(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	plan, err := svc.Store().Plan(r.PathValue("pid"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	versions := make([]map[string]any, 0, plan.Version)
	for v := plan.Version; v >= 1; v-- {
		historical, err := svc.Store().PlanVersion(plan.ID, v)
		if err != nil {
			continue
		}
		versions = append(versions, map[string]any{
			"version": historical.Version, "updated_at": historical.UpdatedAt,
			"topic": historical.Topic, "total_tokens": historical.Meta.TotalTokens,
		})
	}
	writeJSON(w, map[string]any{"versions": versions})
}

func (s *Server) tutorPlanVersion(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	v, err := strconv.Atoi(r.PathValue("v"))
	if err != nil || v < 1 {
		badValue(w, "v", "a plan version number")
		return
	}
	plan, err := svc.Store().PlanVersion(r.PathValue("pid"), v)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, plan)
}

// tutorMaterials generates the self-study guide. An SSE request streams
// unit_start/unit_done/done as each unit lands; everything else gets the
// rendered snapshot. Persisted units answer without a model unless the
// request names a subset to regenerate.
func (s *Server) tutorPlanMaterials(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		Refresh      bool     `json:"refresh"`
		UnitIDs      []string `json:"unit_ids"`
		StageIndexes []int    `json:"stage_indexes"`
		NodeIDs      []string `json:"node_ids"`
	}
	if r.Body != nil {
		defer r.Body.Close()
		_ = json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body)
	}
	req := tutor.MaterialsRequest{
		Refresh: body.Refresh, UnitIDs: body.UnitIDs,
		StageIndexes: body.StageIndexes, NodeIDs: body.NodeIDs,
	}
	wantsSSE := strings.Contains(r.Header.Get("Accept"), "text/event-stream")
	if wantsSSE {
		w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("X-Accel-Buffering", "no")
		flusher, _ := w.(http.Flusher)
		id := 0
		writeFrame := func(ev tutor.MaterialsEvent) {
			id++
			data, _ := json.Marshal(ev.Data)
			fmt.Fprintf(w, "id: %d\nevent: %s\ndata: %s\n\n", id, ev.Type, data)
			if flusher != nil {
				flusher.Flush()
			}
		}
		snapshot, err := svc.GenerateMaterials(r.Context(), r.PathValue("pid"), req, writeFrame)
		if err != nil {
			if id == 0 {
				tutorRefuse(w, err) // nothing streamed yet: a plain refusal
				return
			}
			writeFrame(tutor.MaterialsEvent{Type: "error", Data: map[string]any{
				"code": "internal_error", "message": err.Error(),
			}})
			return
		}
		_ = snapshot
		return
	}
	snapshot, err := svc.GenerateMaterials(r.Context(), r.PathValue("pid"), req, nil)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, snapshot)
}

func (s *Server) tutorQuizGenerate(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		Refresh bool `json:"refresh"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		body.Refresh = false
	}
	set, err := svc.GenerateQuizzes(r.Context(), r.PathValue("pid"), body.Refresh)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, set)
}

func (s *Server) tutorQuizzes(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	set, err := svc.Quizzes(r.PathValue("pid"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, set)
}

func (s *Server) tutorQuizSubmit(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		PlanID   string `json:"plan_id"`
		Selected string `json:"selected"`
		Text     string `json:"text"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || strings.TrimSpace(body.PlanID) == "" {
		missingField(w, "plan_id")
		return
	}
	result, err := svc.Submit(body.PlanID, r.PathValue("qid"), body.Selected, body.Text)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, result)
}

func (s *Server) tutorQuizStats(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	stats, err := svc.QuizStats(r.PathValue("pid"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, stats)
}

func (s *Server) tutorWrongOverview(w http.ResponseWriter, _ *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	overview, err := svc.WrongOverview()
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, overview)
}

func (s *Server) tutorWrongQueue(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	queue, err := svc.WrongQueue(limit)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, queue)
}

// tutorQuizWrong lists one plan's wrong quizzes: latest attempt graded false,
// payload intact for re-answering.
func (s *Server) tutorQuizWrong(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	planID := r.PathValue("pid")
	if _, err := svc.Store().Plan(planID); err != nil {
		tutorRefuse(w, err)
		return
	}
	quizzes, err := svc.Store().Quizzes(planID)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	attempts, err := svc.Store().Attempts(planID)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	latest := tutor.LatestAttempts(attempts)
	items := []map[string]any{}
	for _, q := range quizzes {
		a, ok := latest[q.ID]
		if !ok || a.Correct == nil || *a.Correct {
			continue
		}
		items = append(items, map[string]any{
			"id": q.ID, "node_id": q.NodeID, "type": string(q.Type),
			"question": q.Payload.Question, "options": q.Payload.Options,
			"answer":   q.Payload.Answer,
			"selected": a.Selected, "wrong_at": a.At,
		})
	}
	writeJSON(w, map[string]any{"items": items, "total": len(items)})
}

func (s *Server) tutorProgressRecord(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		PlanID    string   `json:"plan_id"`
		NodeID    string   `json:"node_id"`
		Status    string   `json:"status"`
		QuizScore *float64 `json:"quiz_score"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil ||
		strings.TrimSpace(body.PlanID) == "" || strings.TrimSpace(body.NodeID) == "" {
		missingField(w, "node_id")
		return
	}
	if body.Status == "" {
		body.Status = string(tutor.ProgressLearning)
	}
	row, err := svc.RecordProgress(body.PlanID, body.NodeID, tutor.ProgressStatus(body.Status), body.QuizScore)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, row)
}

func (s *Server) tutorProgressOf(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	overview, err := svc.ProgressOf(r.PathValue("pid"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, overview)
}

func (s *Server) tutorReviewDue(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	writeJSON(w, svc.ReviewDue(limit))
}

func (s *Server) tutorReviewQueue(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	writeJSON(w, svc.ReviewQueue(limit))
}

func (s *Server) tutorStatsSummary(w http.ResponseWriter, _ *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	summary, err := svc.StatsSummary()
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, summary)
}

func (s *Server) tutorStatsCalendar(w http.ResponseWriter, _ *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	calendar, err := svc.StatsCalendar()
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, calendar)
}

func (s *Server) tutorStatsByPlan(w http.ResponseWriter, _ *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	byPlan, err := svc.StatsByPlan()
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, byPlan)
}

func (s *Server) tutorLogs(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	logs, err := svc.Logs(r.URL.Query().Get("plan_id"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, tutorList(logs))
}

func (s *Server) tutorLogAdd(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		PlanID  string `json:"plan_id"`
		Date    string `json:"date"`
		Minutes int    `json:"minutes"`
		Note    string `json:"note"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil ||
		strings.TrimSpace(body.PlanID) == "" || body.Minutes <= 0 {
		missingField(w, "minutes")
		return
	}
	log := &tutor.LearningLog{PlanID: body.PlanID, Date: body.Date, Minutes: body.Minutes, Note: body.Note}
	if err := svc.AddLog(log); err != nil {
		if errors.Is(err, tutor.ErrNotFound) {
			tutorRefuse(w, err)
			return
		}
		writeErr(w, http.StatusInternalServerError, err)
		return
	}
	writeJSON(w, log)
}

func (s *Server) tutorLogDelete(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	planID := r.URL.Query().Get("plan_id")
	if planID == "" {
		missingField(w, "plan_id")
		return
	}
	if err := svc.DeleteLog(planID, r.PathValue("lid")); err != nil {
		tutorRefuse(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) tutorNotes(w http.ResponseWriter, _ *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	notes, err := svc.Store().Notes()
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, tutorList(notes))
}

func (s *Server) tutorNoteCreate(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		Title string `json:"title"`
		Body  string `json:"body"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		badBody(w)
		return
	}
	// SaveNote mints the id and timestamps on first write.
	note := &tutor.Note{Title: strings.TrimSpace(body.Title), Body: body.Body}
	if err := svc.Store().SaveNote(note); err != nil {
		writeErr(w, http.StatusInternalServerError, err)
		return
	}
	writeJSON(w, note)
}

func (s *Server) tutorNote(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	note, err := svc.Store().Note(r.PathValue("nid"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, note)
}

func (s *Server) tutorNoteSave(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	note, err := svc.Store().Note(r.PathValue("nid"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	var body struct {
		Title string `json:"title"`
		Body  string `json:"body"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		badBody(w)
		return
	}
	if strings.TrimSpace(body.Title) != "" {
		note.Title = body.Title
	}
	note.Body = body.Body
	if err := svc.Store().SaveNote(note); err != nil {
		writeErr(w, http.StatusInternalServerError, err)
		return
	}
	writeJSON(w, note)
}

func (s *Server) tutorNoteDelete(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	if err := svc.Store().DeleteNote(r.PathValue("nid")); err != nil {
		tutorRefuse(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// tutorNotesExportZip streams every free note as a portable .md archive
// (one file per note plus index.md), Deep-tutor's iter-286 shape. An empty
// note set refuses: exporting nothing is a dead-end click, not a file.
func (s *Server) tutorNotesExportZip(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	notes, err := svc.Store().Notes()
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	blob, err := tutor.ExportNotesZip(notes, time.Now())
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	stamp := time.Now().Format("20060102-150405")
	w.Header().Set("Content-Type", "application/zip")
	w.Header().Set("Content-Disposition",
		`attachment; filename="notes-`+stamp+`.zip"; filename*=UTF-8''%E7%AC%94%E8%AE%B0%E5%AF%BC%E5%87%BA-`+stamp+".zip")
	w.Header().Set("X-Tutor-Notes", strconv.Itoa(len(notes)))
	_, _ = w.Write(blob)
}

// tutorNotesImport takes {files:[{name, content_base64}]} — the house
// base64-JSON upload shape — accepting .md files and .zip archives of them.
func (s *Server) tutorNotesImport(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		Files []struct {
			Name          string `json:"name"`
			ContentBase64 string `json:"content_base64"`
		} `json:"files"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 8<<20)).Decode(&body); err != nil || len(body.Files) == 0 {
		badBody(w)
		return
	}
	if len(body.Files) > 100 {
		badValue(w, "files", "at most 100 files per import")
		return
	}
	files := make([]tutor.NoteImportFile, 0, len(body.Files))
	for _, f := range body.Files {
		data, err := base64.StdEncoding.DecodeString(f.ContentBase64)
		if err != nil {
			badValue(w, "content_base64", "valid base64 payload")
			return
		}
		files = append(files, tutor.NoteImportFile{Name: f.Name, Data: data})
	}
	result, err := tutor.ImportNotes(svc.Store(), files)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, result)
}

// tutorSearch answers one substring query across plans, knowledge nodes,
// and notes — the tutor's global search (KB documents join in S6).
func (s *Server) tutorSearch(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	results, err := tutor.SearchAll(svc.Store(), r.URL.Query().Get("q"), limit)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, results)
}

// tutorUsage serves the daily-budget view over the tutor cost ledger; the
// budget echoes the same env the service guards with.
func (s *Server) tutorUsage(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	days, _ := strconv.Atoi(r.URL.Query().Get("days"))
	summary, err := svc.Store().UsageOverview(days, tutorDailyBudgetFromEnv(), time.Now())
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, summary)
}

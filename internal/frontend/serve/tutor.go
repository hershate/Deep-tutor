// tutor.go — the tutor surface over HTTP: Deep-tutor's plan-generation API
// as a namespace of this server. SSE posts stream the pipeline's event
// vocabulary (tool_start/tool_end/plan_chunk/clarify_needed/warn/
// budget_exceeded/error/done) with resumable ids; everything else is small
// JSON resources under /tutor.
package serve

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"reasonix/internal/assembly/boot"
	"reasonix/internal/base/netclient"
	"reasonix/internal/contract/config"
	"reasonix/internal/contract/pricing"
	"reasonix/internal/contract/provider"
	"reasonix/internal/runtime/tutor"
)

// The tutor service is shared per state root, not per Server: a hub drives
// several panes in one process and the one-run-per-session rule has to hold
// across all of them.
var (
	tutorMu     sync.Mutex
	tutorShared = map[string]*tutor.Service{}
)

// tutorService returns the process's tutor service, building it on first
// use. Tests and hosts that own construction call SetTutorService instead.
func (s *Server) tutorService() *tutor.Service {
	s.mu.RLock()
	svc := s.tutorSvc
	s.mu.RUnlock()
	if svc != nil {
		return svc
	}
	root := config.TutorDir()
	if root == "" {
		return nil
	}
	tutorMu.Lock()
	defer tutorMu.Unlock()
	if svc, ok := tutorShared[root]; ok {
		return svc
	}
	store, err := tutor.OpenStore(root)
	if err != nil {
		return nil
	}
	svc = tutor.NewService(store, tutor.ServiceOptions{
		Provider:    tutorProviderFromConfig,
		Pricer:      tutorPricerFromConfig(),
		Searcher:    tutorSearcherFromConfig(),
		DailyBudget: tutorDailyBudgetFromEnv(),
	})
	tutorShared[root] = svc
	return svc
}

// tutorPricerFromConfig prices metered tutor usage with the same rate cards
// chat accounting uses: the entry's active model prices, fixed-point.
func tutorPricerFromConfig() tutor.Pricer {
	return func(modelRef string, u tutor.Usage) float64 {
		cfg, err := config.Load()
		if err != nil {
			return 0
		}
		entry, ok := cfg.ResolveModel(modelRef)
		if !ok {
			start, found := cfg.ResolveStartupChatModel()
			if !found {
				return 0
			}
			entry, ok = cfg.ResolveModel(start.Ref)
			if !ok {
				return 0
			}
		}
		card := entry.RateCardForModel(entry.Model)
		amount := pricing.OriginalCostAmount(card, pricing.UsageTokens{
			PromptTokens:     int(u.InputTokens),
			CompletionTokens: int(u.OutputTokens),
			CacheHitTokens:   int(u.CacheReadTokens),
			CacheWriteTokens: int(u.CacheWriteTokens),
		})
		return amount.Float64()
	}
}

// tutorSearcherFromConfig routes the tutor's web search through Reasonix's
// unified scheme: the agent.web_search_model selection, resolved per run so
// a settings change reaches the next generation. Construction is boot's —
// the same routing the registry's web_search tool uses — so model
// selection, credentials, and proxy have exactly one manager.
func tutorSearcherFromConfig() tutor.Searcher {
	return &tutor.WebSearcher{
		SearchModel: func() (provider.Provider, string, error) {
			cfg, err := config.Load()
			if err != nil {
				return nil, "", fmt.Errorf("tutor: config: %w", err)
			}
			return boot.NewWebSearchModel(cfg, netclient.ProxySpec{Mode: netclient.ModeAuto})
		},
		Provider: tutorProviderFromConfig,
	}
}

// tutorDailyBudgetFromEnv reads the day's cost ceiling in rate-card
// currency; TUTOR_DAILY_BUDGET<=0 disables it. Deep-tutor defaults to 10.
func tutorDailyBudgetFromEnv() float64 {
	v := strings.TrimSpace(os.Getenv("TUTOR_DAILY_BUDGET"))
	if v == "" {
		return 10.0
	}
	f, err := strconv.ParseFloat(v, 64)
	if err != nil || f < 0 {
		return 10.0
	}
	return f
}

// SetTutorService hands the server its tutor service; the desktop host or a
// test does this when it owns construction.
func (s *Server) SetTutorService(svc *tutor.Service) {
	s.mu.Lock()
	s.tutorSvc = svc
	s.mu.Unlock()
}

// tutorProviderFromConfig resolves the model a new chat session would open
// with, per run, so a settings change applies to the next generation without
// a rebuild. It follows initTitleProvider's shape.
func tutorProviderFromConfig() (tutor.LLM, error) {
	cfg, err := config.Load()
	if err != nil {
		return nil, fmt.Errorf("tutor: config: %w", err)
	}
	start, ok := cfg.ResolveStartupChatModel()
	if !ok {
		return nil, errors.New("tutor: no chat model is configured")
	}
	entry, ok := cfg.ResolveModel(start.Ref)
	if !ok || !entry.Configured() {
		return nil, errors.New("tutor: the configured chat model is not ready (missing API key)")
	}
	prov, err := provider.New(entry.Kind, provider.Config{
		Name: entry.Name, BaseURL: entry.BaseURL, Model: entry.Model,
		APIKey: entry.APIKey(), APIKeyFunc: entry.APIKey,
	})
	if err != nil {
		return nil, fmt.Errorf("tutor: provider: %w", err)
	}
	return prov, nil
}

// tutorRefuse maps tutor sentinel errors onto their HTTP refusals.
func tutorRefuse(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, tutor.ErrNotFound):
		refuse(w, http.StatusNotFound, "tutor.not_found", "that tutor resource does not exist", nil)
	case errors.Is(err, tutor.ErrBusy):
		refuse(w, http.StatusConflict, "tutor.busy", "a generation is already running for this session", nil)
	case errors.Is(err, tutor.ErrNotClarifiable):
		refuse(w, http.StatusConflict, "tutor.no_clarification", "this session has no pending clarification", nil)
	case errors.Is(err, tutor.ErrNotRetryable):
		refuse(w, http.StatusConflict, "tutor.not_retryable", "this session is not in a retryable state", nil)
	case errors.Is(err, tutor.ErrNoActiveRun):
		refuse(w, http.StatusConflict, "tutor.no_active_run", "no running generation to cancel", nil)
	case errors.Is(err, tutor.ErrBadScope):
		refuse(w, http.StatusBadRequest, "tutor.bad_scope", err.Error(), nil)
	case errors.Is(err, tutor.ErrRegenQuota):
		refuse(w, http.StatusTooManyRequests, "tutor.regen_quota", err.Error(), nil)
	case errors.Is(err, tutor.ErrNoRawText):
		refuse(w, http.StatusConflict, "tutor.no_raw_text", err.Error(), nil)
	case errors.Is(err, tutor.ErrUnsupportedFormat):
		refuse(w, http.StatusBadRequest, "tutor.unsupported_format", err.Error(), nil)
	case errors.Is(err, tutor.ErrNoSchedule):
		refuse(w, http.StatusBadRequest, "tutor.no_schedule", "the plan has no schedule to export", nil)
	case errors.Is(err, tutor.ErrNoQuizzes):
		refuse(w, http.StatusBadRequest, "tutor.no_quizzes", "the plan has no quizzes to export", nil)
	case errors.Is(err, tutor.ErrNoWrongQuizzes):
		refuse(w, http.StatusBadRequest, "tutor.no_wrong_quizzes", "nothing answered wrong yet; nothing to export", nil)
	case errors.Is(err, tutor.ErrBudgetExceeded):
		refuse(w, http.StatusPaymentRequired, "tutor.budget_exceeded", "the daily tutor budget is exhausted", nil)
	case errors.Is(err, tutor.ErrNoLLM):
		refuse(w, http.StatusServiceUnavailable, "tutor.no_llm", "no LLM provider is configured", nil)
	case errors.Is(err, tutor.ErrKbQuota):
		refuse(w, http.StatusTooManyRequests, "tutor.kb_quota", err.Error(), nil)
	case errors.Is(err, tutor.ErrKbParsing):
		refuse(w, http.StatusConflict, "tutor.kb_parsing", "this document is being parsed; try again shortly", nil)
	case errors.Is(err, tutor.ErrKbNoOriginal):
		refuse(w, http.StatusBadRequest, "tutor.kb_no_original", err.Error(), nil)
	case errors.Is(err, tutor.ErrKbEmpty), errors.Is(err, tutor.ErrKbTooLarge), errors.Is(err, tutor.ErrBadKind):
		refuse(w, http.StatusBadRequest, "tutor.kb_bad_input", err.Error(), nil)
	case errors.Is(err, tutor.ErrKbNoDocs):
		refuse(w, http.StatusBadRequest, "tutor.kb_no_docs", "the knowledge base has no usable documents yet", nil)
	case errors.Is(err, tutor.ErrKbExportEmpty):
		refuse(w, http.StatusBadRequest, "tutor.kb_export_empty", "the export selected no documents (empty library or filter)", nil)
	case errors.Is(err, tutor.ErrUpstreamTimeout):
		refuse(w, http.StatusGatewayTimeout, "tutor.upstream_timeout", "llm response timed out, retry shortly", nil)
	case errors.Is(err, tutor.ErrBadGateway):
		refuse(w, http.StatusBadGateway, "tutor.bad_gateway", "llm call failed, retry shortly", nil)
	default:
		writeErr(w, http.StatusInternalServerError, err)
	}
}

func tutorUnsupported(w http.ResponseWriter) {
	refuse(w, http.StatusServiceUnavailable, "tutor.disabled", "the tutor store is unavailable", nil)
}

func (s *Server) registerTutorRoutes(mux *http.ServeMux) {
	mux.HandleFunc("POST /tutor/sessions", s.tutorCreateSession)
	mux.HandleFunc("GET /tutor/sessions", s.tutorSessions)
	mux.HandleFunc("GET /tutor/sessions/{id}", s.tutorSession)
	mux.HandleFunc("DELETE /tutor/sessions/{id}", s.tutorDeleteSession)

	mux.HandleFunc("POST /tutor/sessions/{id}/materials/text", s.tutorMaterialText)
	mux.HandleFunc("POST /tutor/sessions/{id}/materials/url", s.tutorMaterialURL)
	mux.HandleFunc("POST /tutor/sessions/{id}/materials/upload", s.tutorMaterialUpload)
	mux.HandleFunc("GET /tutor/sessions/{id}/materials", s.tutorMaterials)
	mux.HandleFunc("POST /tutor/sessions/{id}/materials/{mid}/confirm", s.tutorMaterialConfirm)
	mux.HandleFunc("POST /tutor/sessions/{id}/materials/{mid}/parse", s.tutorMaterialParse)
	mux.HandleFunc("DELETE /tutor/sessions/{id}/materials/{mid}", s.tutorMaterialDelete)

	mux.HandleFunc("POST /tutor/sessions/{id}/generate", s.tutorGenerate)
	mux.HandleFunc("POST /tutor/sessions/{id}/clarify", s.tutorClarify)
	mux.HandleFunc("POST /tutor/sessions/{id}/retry", s.tutorRetry)
	mux.HandleFunc("POST /tutor/sessions/{id}/cancel", s.tutorCancel)
	mux.HandleFunc("POST /tutor/plans/{pid}/regenerate", s.tutorRegenerate)

	s.registerTutorDataRoutes(mux)
	s.registerTutorKbRoutes(mux)
	s.registerTutorShareRoutes(mux)
	s.registerTutorBackupRoutes(mux)
}

// tutorList keeps the wire contract that a list is an array: the page
// dereferences these without a null check, so a nil slice must never reach
// the encoder.
func tutorList[T any](items []T) []T {
	if items == nil {
		return []T{}
	}
	return items
}

func (s *Server) tutorCreateSession(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		Title string `json:"title"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		badBody(w)
		return
	}
	sess, err := svc.CreateSession(strings.TrimSpace(body.Title))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, sess)
}

func (s *Server) tutorSessions(w http.ResponseWriter, _ *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	sessions, err := svc.Sessions()
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, tutorList(sessions))
}

func (s *Server) tutorSession(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	sess, err := svc.Session(r.PathValue("id"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, sess)
}

func (s *Server) tutorDeleteSession(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	if err := svc.DeleteSession(r.PathValue("id")); err != nil {
		tutorRefuse(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) tutorMaterialText(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		Name    string `json:"name"`
		Content string `json:"content"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || strings.TrimSpace(body.Content) == "" {
		missingField(w, "content")
		return
	}
	m, err := svc.AddMaterialText(r.PathValue("id"), strings.TrimSpace(body.Name), body.Content)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, m)
}

func (s *Server) tutorMaterialURL(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		URL string `json:"url"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || strings.TrimSpace(body.URL) == "" {
		missingField(w, "url")
		return
	}
	m, err := svc.AddMaterialURL(r.PathValue("id"), body.URL)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, m)
}

// tutorMaterialUpload takes a JSON body {filename, content_base64} — the
// kernel's CSRF guard admits JSON only, and attachments already established
// base64-in-JSON as the upload shape here.
func (s *Server) tutorMaterialUpload(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		Filename      string `json:"filename"`
		ContentBase64 string `json:"content_base64"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || strings.TrimSpace(body.ContentBase64) == "" {
		missingField(w, "content_base64")
		return
	}
	data, err := base64.StdEncoding.DecodeString(body.ContentBase64)
	if err != nil {
		refuse(w, http.StatusBadRequest, "tutor.bad_upload", "content_base64 did not decode", nil)
		return
	}
	m, err := svc.AddMaterialFile(r.PathValue("id"), body.Filename, data)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, m)
}

func (s *Server) tutorMaterials(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	materials, err := svc.Materials(r.PathValue("id"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, tutorList(materials))
}

func (s *Server) tutorMaterialConfirm(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		ParseMode string `json:"parse_mode"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		body.ParseMode = ""
	}
	m, err := svc.ConfirmMaterial(r.PathValue("id"), r.PathValue("mid"), body.ParseMode)
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, m)
}

func (s *Server) tutorMaterialParse(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	m, err := svc.ParseMaterialFor(r.Context(), r.PathValue("id"), r.PathValue("mid"))
	if err != nil && m == nil {
		tutorRefuse(w, err)
		return
	}
	writeJSON(w, m)
}

func (s *Server) tutorMaterialDelete(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	if err := svc.DeleteMaterial(r.PathValue("id"), r.PathValue("mid")); err != nil {
		tutorRefuse(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// tutorGenerate streams a generation run. Occupancy refusals are 4xx JSON
// before the stream opens; once it opens, every outcome — including
// cancellation and budget stops — is an SSE event.
func (s *Server) tutorGenerate(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		RawText      string `json:"raw_text"`
		DailyMinutes int    `json:"daily_minutes"`
		Deadline     string `json:"deadline"`
		StartDate    string `json:"start_date"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || strings.TrimSpace(body.RawText) == "" {
		missingField(w, "raw_text")
		return
	}
	if err := svc.StartGenerate(r.PathValue("id"), body.RawText, tutor.IntentArgs{
		DailyMinutes: body.DailyMinutes, Deadline: body.Deadline, StartDate: body.StartDate,
	}); err != nil {
		tutorRefuse(w, err)
		return
	}
	s.tutorStream(w, r, r.PathValue("id"))
}

func (s *Server) tutorClarify(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		Answers []string `json:"answers"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || len(body.Answers) == 0 {
		missingField(w, "answers")
		return
	}
	if err := svc.Clarify(r.PathValue("id"), body.Answers); err != nil {
		tutorRefuse(w, err)
		return
	}
	s.tutorStream(w, r, r.PathValue("id"))
}

func (s *Server) tutorRetry(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	if err := svc.Retry(r.PathValue("id")); err != nil {
		tutorRefuse(w, err)
		return
	}
	s.tutorStream(w, r, r.PathValue("id"))
}

func (s *Server) tutorCancel(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	if err := svc.Cancel(r.PathValue("id")); err != nil {
		tutorRefuse(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) tutorRegenerate(w http.ResponseWriter, r *http.Request) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	var body struct {
		Scope    string `json:"scope"`
		Feedback string `json:"feedback"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || strings.TrimSpace(body.Scope) == "" {
		missingField(w, "scope")
		return
	}
	if err := svc.Regenerate(r.PathValue("pid"), body.Scope, body.Feedback); err != nil {
		tutorRefuse(w, err)
		return
	}
	// The stream belongs to the plan's session.
	plan, err := svc.Store().Plan(r.PathValue("pid"))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	s.tutorStream(w, r, plan.SessionID)
}

// tutorStream relays a session's run as SSE in Deep-tutor's frame shape
// (id/event/data), resuming from the client's last event id and ending on
// the run's terminal event.
func (s *Server) tutorStream(w http.ResponseWriter, r *http.Request, sessionID string) {
	svc := s.tutorService()
	if svc == nil {
		tutorUnsupported(w)
		return
	}
	ch, stop, err := svc.Subscribe(sessionID, lastEventID(r))
	if err != nil {
		tutorRefuse(w, err)
		return
	}
	defer stop()
	flusher, ok := w.(http.Flusher)
	if !ok {
		refuse(w, http.StatusInternalServerError, "stream.unsupported", "this transport cannot stream", nil)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	fmt.Fprint(w, ": connected\n\n")
	flusher.Flush()

	keepalive := time.NewTicker(sseKeepaliveInterval)
	defer keepalive.Stop()
	for {
		select {
		case ev, ok := <-ch:
			if !ok {
				return
			}
			fmt.Fprintf(w, "id: %d\nevent: %s\ndata: %s\n\n", ev.ID, ev.Type, ev.Data)
			flusher.Flush()
			switch ev.Type {
			case tutor.EventDone, tutor.EventError, tutor.EventClarifyNeeded:
				return
			}
		case <-keepalive.C:
			fmt.Fprint(w, ": ping\n\n")
			flusher.Flush()
		case <-r.Context().Done():
			return
		}
	}
}

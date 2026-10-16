package tutor

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"
)

// Service is the transport-agnostic tutor driver: the pipeline, its session
// state machine, and the event streams frontends relay. One Service owns one
// store root; the serve frontend holds it for the whole process, so the
// one-run-per-session occupancy rule holds across every pane.
type Service struct {
	store *Store
	opt   ServiceOptions

	mu   sync.Mutex
	runs map[string]*sessionRun

	// kbMu guards the per-document parse registry so a delete or second
	// parse cannot land mid-extraction (Deep-tutor's _PARSE_LOCKS).
	kbMu      sync.Mutex
	kbParsing map[string]bool
}

// ServiceOptions carries what a host resolves once and every run re-reads.
type ServiceOptions struct {
	// Provider resolves the LLM at run start. Nil means no provider is
	// configured: runs occupy, publish a no_llm_provider terminal, and keep
	// the raw text so a later retry works.
	Provider func() (LLM, error)
	Searcher Searcher
	Pricer   Pricer

	MaxTokens    int
	BudgetTokens int64
	MaxToolCalls int
	// DailyBudget caps a local day's recorded tutor spend in the rate card's
	// currency; 0 disables the guard. Deep-tutor's default is 10.
	DailyBudget float64
	// MaxRegenerations caps plan versions past the first; 0 means 2, like
	// Deep-tutor's settings.max_regenerations.
	MaxRegenerations int
	Now              func() time.Time
}

func (o *ServiceOptions) now() time.Time {
	if o.Now != nil {
		return o.Now()
	}
	return time.Now()
}

func (o *ServiceOptions) maxRegenerations() int {
	if o.MaxRegenerations > 0 {
		return o.MaxRegenerations
	}
	return 2
}

// budgetExceeded reports whether today's recorded spend has reached the
// daily budget; the check is advisory-read, the ledger append is what makes
// it true.
func (s *Service) budgetExceeded() bool {
	if s.opt.DailyBudget <= 0 {
		return false
	}
	today := s.opt.now().Format("2006-01-02")
	usage, err := s.store.UsageOfDay(today)
	if err != nil {
		return false
	}
	return usage.Cost >= s.opt.DailyBudget
}

// Occupancy refusals, each mapping to its own HTTP status downstream.
var (
	ErrBusy           = errors.New("tutor: a generation is already running for this session")
	ErrNotClarifiable = errors.New("tutor: this session has no pending clarification")
	ErrNotRetryable   = errors.New("tutor: this session is not in a retryable state")
	ErrNoActiveRun    = errors.New("tutor: no running generation to cancel")
	ErrBadScope       = errors.New("tutor: invalid regeneration scope")
	ErrRegenQuota     = errors.New("tutor: regeneration quota exhausted")
	ErrNoRawText      = errors.New("tutor: the original input was not kept, cannot retry")
	ErrBudgetExceeded = errors.New("tutor: daily budget exhausted")
)

// sessionRun is one occupied session: its event stream, its cancel handle,
// and the live progress fields read-side projections carry.
type sessionRun struct {
	sid    string
	stream *sessionStream
	cancel context.CancelFunc

	mu          sync.Mutex
	status      Status
	currentTool string
	stepIndex   int
	totalSteps  int
	cost        float64
	retire      *time.Timer
}

func NewService(store *Store, opt ServiceOptions) *Service {
	if opt.Provider == nil {
		opt.Provider = func() (LLM, error) { return nil, ErrNoLLM }
	}
	return &Service{store: store, opt: opt, runs: map[string]*sessionRun{}}
}

func (s *Service) Store() *Store { return s.store }

// LiveSession is the read model GET /sessions serves: the persisted session
// with the live progress of an in-flight run overlaid.
type LiveSession struct {
	GenSession
	CurrentTool string  `json:"current_tool,omitempty"`
	StepIndex   int     `json:"step_index,omitempty"`
	TotalSteps  int     `json:"total_steps,omitempty"`
	Cost        float64 `json:"cost,omitempty"`
}

// convergeStale settles a session the last process left marked running: the
// in-memory table is the authority while the process lives, so a disk
// "running" with no live run is a crash relic and reads as interrupted.
func (s *Service) convergeStale(sess *GenSession) {
	if sess.Status != StatusRunning {
		return
	}
	s.mu.Lock()
	_, live := s.runs[sess.ID]
	s.mu.Unlock()
	if live {
		return
	}
	sess.Status = StatusInterrupted
	sess.LastError = "进程重启，运行中断"
	_ = s.store.SaveSession(sess)
}

func (s *Service) Session(id string) (*LiveSession, error) {
	sess, err := s.store.Session(id)
	if err != nil {
		return nil, err
	}
	s.convergeStale(sess)
	live := &LiveSession{GenSession: *sess}
	s.mu.Lock()
	if run := s.runs[id]; run != nil {
		run.mu.Lock()
		if run.status == StatusRunning || run.status == StatusPausedClarify {
			live.Status = run.status
			live.CurrentTool = run.currentTool
			live.StepIndex = run.stepIndex
			live.TotalSteps = run.totalSteps
			live.Cost = run.cost
		}
		run.mu.Unlock()
	}
	s.mu.Unlock()
	return live, nil
}

func (s *Service) Sessions() ([]LiveSession, error) {
	list, err := s.store.Sessions()
	if err != nil {
		return nil, err
	}
	out := make([]LiveSession, 0, len(list))
	for i := range list {
		live, err := s.Session(list[i].ID)
		if err != nil {
			continue
		}
		out = append(out, *live)
	}
	return out, nil
}

func (s *Service) CreateSession(title string) (*GenSession, error) {
	return s.store.CreateSession(title)
}

// DeleteSession cancels any live run before removing the files, so no
// finalizer can write back into a deleted session.
func (s *Service) DeleteSession(id string) error {
	s.mu.Lock()
	run := s.runs[id]
	delete(s.runs, id)
	s.mu.Unlock()
	if run != nil {
		if run.retire != nil {
			run.retire.Stop()
		}
		run.cancel()
		run.stream.retire()
	}
	return s.store.DeleteSession(id)
}

// running reports whether the session has a live in-memory run.
func (s *Service) running(sid string) bool {
	s.mu.Lock()
	run := s.runs[sid]
	s.mu.Unlock()
	if run == nil {
		return false
	}
	run.mu.Lock()
	defer run.mu.Unlock()
	return run.status == StatusRunning
}

// StartGenerate begins a run: running sessions attach to the live stream,
// terminal sessions replay their terminal event (a refresh never re-bills),
// and idle or paused_clarify sessions start fresh.
func (s *Service) StartGenerate(sid, rawText string, tf IntentArgs) error {
	sess, err := s.store.Session(sid)
	if err != nil {
		return err
	}
	s.convergeStale(sess)
	if s.running(sid) {
		return nil // attach: the caller relays the existing stream
	}
	switch sess.Status {
	case StatusDone, StatusError, StatusInterrupted:
		return nil // terminal replay synthesized by Subscribe
	}
	if s.budgetExceeded() {
		return ErrBudgetExceeded
	}
	upstream := s.store.parseUpstreamFromStore(sid)
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.occupyLocked(sess, rawText, nil, tf, ToolUnderstandIntent, upstream, nil)
}

// Clarify resumes a paused session with the person's answers, exactly like
// Deep-tutor: the window reruns from understand_intent with the answers in
// its arguments and the partial outputs injected as upstream.
func (s *Service) Clarify(sid string, answers []string) error {
	if len(answers) == 0 {
		return ErrNotClarifiable // empty answers would re-trigger the pause
	}
	sess, err := s.store.Session(sid)
	if err != nil {
		return err
	}
	s.convergeStale(sess)
	if s.running(sid) {
		return ErrBusy
	}
	if sess.Status != StatusPausedClarify {
		return ErrNotClarifiable
	}
	rs, err := s.store.RunStateOf(sid)
	if err != nil || rs.RawText == "" {
		return ErrNotClarifiable
	}
	if s.budgetExceeded() {
		return ErrBudgetExceeded
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.occupyLocked(sess, rs.RawText, answers, rs.TimeFields, ToolUnderstandIntent, rs.Outputs, nil)
}

// Retry reruns a failed session from its kept raw text.
func (s *Service) Retry(sid string) error {
	sess, err := s.store.Session(sid)
	if err != nil {
		return err
	}
	s.convergeStale(sess)
	if s.running(sid) {
		return ErrBusy
	}
	if sess.Status != StatusError && sess.Status != StatusInterrupted {
		return ErrNotRetryable
	}
	rs, err := s.store.RunStateOf(sid)
	if err != nil {
		return ErrNotRetryable
	}
	if rs.RawText == "" {
		return ErrNoRawText
	}
	if s.budgetExceeded() {
		return ErrBudgetExceeded
	}
	upstream := s.store.parseUpstreamFromStore(sid)
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.occupyLocked(sess, rs.RawText, nil, rs.TimeFields, ToolUnderstandIntent, upstream, nil)
}

// Cancel stops the live run; its terminal error lands on the stream.
func (s *Service) Cancel(sid string) error {
	if !s.running(sid) {
		return ErrNoActiveRun
	}
	s.mu.Lock()
	run := s.runs[sid]
	s.mu.Unlock()
	if run == nil {
		return ErrNoActiveRun
	}
	run.cancel()
	return nil
}

// Regenerate reruns part of a delivered plan: the scope tool and everything
// downstream, with the prior outputs injected as upstream, producing the next
// version of the same plan.
func (s *Service) Regenerate(planID, scope, feedback string) error {
	if !validScope(scope) {
		return fmt.Errorf("%w: %q", ErrBadScope, scope)
	}
	summary, err := s.store.Plan(planID)
	if err != nil {
		return err
	}
	sess, err := s.store.Session(summary.SessionID)
	if err != nil {
		return err
	}
	s.convergeStale(sess)
	if s.running(sess.ID) {
		return ErrBusy
	}
	rs, err := s.store.RunStateOf(sess.ID)
	if err != nil {
		return err
	}
	if _, ok := rs.Outputs[ToolUnderstandIntent]; !ok {
		return fmt.Errorf("%w: understand_intent must stay upstream; regenerate from %s on", ErrBadScope, ToolBuildKnowledgeMap)
	}
	if summary.Version > s.opt.maxRegenerations() {
		return fmt.Errorf("%w: %d regenerations", ErrRegenQuota, s.opt.maxRegenerations())
	}
	if s.budgetExceeded() {
		return ErrBudgetExceeded
	}
	var fb []string
	if strings.TrimSpace(feedback) != "" {
		fb = []string{strings.TrimSpace(feedback)}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.occupyLocked(sess, rs.RawText, rs.Answers, rs.TimeFields, scope, rs.Outputs, fb)
}

func validScope(scope string) bool {
	for _, name := range pipeline {
		if name == scope && name != ToolParseMaterial && name != ToolUnderstandIntent {
			return true
		}
	}
	return false
}

// occupyLocked starts the run goroutine. The caller holds s.mu and has
// already ruled out a live run for this session.
func (s *Service) occupyLocked(sess *GenSession, rawText string, answers []string,
	tf IntentArgs, startTool string, upstream map[string]ToolOutput, feedback []string) error {

	if prev := s.runs[sess.ID]; prev != nil {
		if prev.retire != nil {
			prev.retire.Stop()
		}
		prev.stream.retire()
	}
	ctx, cancel := context.WithCancel(context.Background())
	run := &sessionRun{sid: sess.ID, stream: newSessionStream(), cancel: cancel, status: StatusRunning}
	s.runs[sess.ID] = run

	sess.Status = StatusRunning
	sess.RawText = rawText
	sess.CurrentTool = ""
	sess.LastError = ""
	sess.UpdatedAt = s.opt.now()
	_ = s.store.SaveSession(sess)

	in := RunInput{
		Session:         sess,
		RawText:         rawText,
		Answers:         answers,
		TimeFields:      tf,
		StartTool:       startTool,
		InitialUpstream: upstream,
		Feedback:        feedback,
	}
	go s.runPipeline(ctx, run, in)
	return nil
}

// runPipeline drives one occupation end to end: provider resolution, the
// pipeline, and the finalize path every terminal state flows through.
// Terminal events belong to finalize alone — the pipeline's own done/error
// emissions are dropped here so the stream carries exactly one of each.
func (s *Service) runPipeline(ctx context.Context, run *sessionRun, in RunInput) {
	llm, provErr := s.opt.Provider()

	emit := func(ev Event) {
		switch ev.Type {
		case EventDone, EventError, EventClarifyNeeded:
			return // finalize owns every terminal event
		}
		run.stream.publish(ev.Type, ev.Data)
		run.mu.Lock()
		switch ev.Type {
		case EventToolStart:
			if m, ok := ev.Data.(map[string]any); ok {
				run.currentTool, _ = m["tool"].(string)
				if v, ok := m["step_index"].(int); ok {
					run.stepIndex = v
				}
				if v, ok := m["total_steps"].(int); ok {
					run.totalSteps = v
				}
			}
		case EventToolEnd:
			if m, ok := ev.Data.(map[string]any); ok {
				if meta, ok := m["meta"].(toolMeta); ok {
					run.cost += meta.Cost
				}
			}
		}
		run.mu.Unlock()
	}

	if provErr != nil {
		s.finalize(ctx, run, in, StatusError, nil, map[string]ToolOutput{}, 0, 0, provErr)
		return
	}
	runner := &Runner{
		Store: s.store, LLM: llm, Searcher: s.opt.Searcher, Pricer: s.opt.Pricer,
		MaxTokens: s.opt.MaxTokens, BudgetTokens: s.opt.BudgetTokens, MaxToolCalls: s.opt.MaxToolCalls,
	}
	result := runner.Run(ctx, in, emit)
	s.finalize(ctx, run, in, result.Status, result.Plan, result.Outputs, result.Tokens, result.Cost, result.Err)
}

// finalize is the one persistence path: run state for continuations, the
// plan version on success, the session state machine, and the terminal event.
func (s *Service) finalize(ctx context.Context, run *sessionRun, in RunInput, status Status, plan *Plan,
	outputs map[string]ToolOutput, tokens int64, cost float64, runErr error) {

	s.mu.Lock()
	current, mine := s.runs[run.sid]
	s.mu.Unlock()
	if !mine || current != run {
		return // the session was deleted or re-occupied; not ours to write
	}

	sess, err := s.store.Session(run.sid)
	if err != nil {
		s.retire(run)
		return
	}
	// The live view accumulated cost from tool_end events; that is the number
	// the person watched, so it is the one that lands.
	run.mu.Lock()
	totalCost := run.cost
	run.mu.Unlock()

	rs := &RunState{
		RawText: in.RawText, Answers: in.Answers, TimeFields: in.TimeFields,
		Outputs: outputs, Status: status, Cost: totalCost,
	}
	sess.UpdatedAt = s.opt.now()
	sess.Cost = totalCost

	if status == StatusDone {
		if plan == nil {
			plan = AssemblePlan(sess, outputs)
		}
		if err := s.persistPlan(sess, plan, tokens, totalCost); err != nil {
			status, runErr = StatusError, err
		}
	}

	// Persistence precedes the terminal event: a client that has seen the
	// terminal can act on durable state — the plan id it navigates to, the
	// questions it answers — without racing the writer.
	switch status {
	case StatusDone:
		rs.PlanID = plan.ID
		sess.Status = StatusDone
		sess.PlanVersion = plan.Version
		if sess.Title == "" {
			sess.Title = plan.Topic
		}
	case StatusPausedClarify:
		sess.Status = StatusPausedClarify
		if intent := intentOf(outputs); len(intent.ClarifyQuestions) > 0 {
			sess.ClarifyQuestions = intent.ClarifyQuestions
		}
	default: // error and interrupted both rest in error, retryable
		_, msg := terminalCode(ctx, status, runErr)
		sess.Status = StatusError
		sess.LastError = msg
		rs.LastError = msg
	}
	if err := s.store.SaveRunState(run.sid, rs); err != nil {
		slog.Warn("tutor: persist run state", "session", run.sid, "err", err)
	}
	if err := s.store.SaveSession(sess); err != nil {
		slog.Warn("tutor: persist session", "session", run.sid, "err", err)
	}
	if totalCost > 0 {
		// Metering is best-effort: a ledger write failure never fails the run.
		if err := s.store.AddUsage(s.opt.now().Format("2006-01-02"), totalCost); err != nil {
			slog.Warn("tutor: record usage", "session", run.sid, "err", err)
		}
	}

	switch status {
	case StatusDone:
		id := run.stream.publish(EventDone, map[string]any{
			"plan_id": plan.ID, "version": plan.Version,
			"metadata": map[string]any{"tokens": tokens, "cost": totalCost},
		})
		run.stream.markTerminal(id)
		run.mu.Lock()
		run.status = StatusDone
		run.mu.Unlock()
	case StatusPausedClarify:
		id := run.stream.publish(EventClarifyNeeded, map[string]any{
			"questions": sess.ClarifyQuestions,
		})
		run.stream.markTerminal(id)
		run.mu.Lock()
		run.status = StatusPausedClarify
		run.mu.Unlock()
	default:
		code, msg := terminalCode(ctx, status, runErr)
		id := run.stream.publish(EventError, map[string]any{
			"code": code, "message": msg, "retryable": true,
		})
		run.stream.markTerminal(id)
		run.mu.Lock()
		run.status = StatusError
		run.mu.Unlock()
	}
	s.retire(run)
}

func terminalCode(ctx context.Context, status Status, runErr error) (code, msg string) {
	switch {
	case runErr != nil && errors.Is(runErr, ErrNoLLM):
		return "no_llm_provider", "未配置 LLM 供应商：" + runErr.Error()
	case ctx.Err() != nil:
		return "cancelled", "已取消"
	case status == StatusInterrupted:
		return "interrupted", "生成中断（预算或调用上限）"
	case runErr != nil:
		return "tool_error", runErr.Error()
	default:
		return "internal_error", "生成失败"
	}
}

// persistPlan writes the next version of the session's plan lineage.
func (s *Service) persistPlan(sess *GenSession, plan *Plan, tokens int64, cost float64) error {
	now := s.opt.now()
	if sess.PlanID != "" {
		if prev, err := s.store.Plan(sess.PlanID); err == nil {
			plan.ID = prev.ID
			plan.Version = prev.Version + 1
			plan.CreatedAt = prev.CreatedAt
		}
	}
	if plan.ID == "" {
		plan.ID = newID()
		plan.Version = 1
		plan.CreatedAt = now
	}
	plan.SessionID = sess.ID
	plan.Meta.TotalTokens = tokens
	plan.Meta.Cost = cost
	plan.UpdatedAt = now
	if err := s.store.SavePlan(plan); err != nil {
		return err
	}
	sess.PlanID = plan.ID
	sess.PlanVersion = plan.Version
	return nil
}

// retire schedules the stream's removal, keeping it replayable for late
// subscribers for the retirement window, mirroring Deep-tutor's 300s cleanup.
func (s *Service) retire(run *sessionRun) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if run.retire != nil {
		run.retire.Stop()
	}
	run.retire = time.AfterFunc(terminalRetireSec*time.Second, func() {
		s.mu.Lock()
		defer s.mu.Unlock()
		if s.runs[run.sid] == run {
			run.stream.retire()
			delete(s.runs, run.sid)
		}
	})
}

// Subscribe relays a session's run. A live run attaches to its stream — the
// resume path for a dropped connection; everything else answers with the
// one-shot terminal replay Deep-tutor serves from its persisted state, so a
// page refresh never restarts finished work.
func (s *Service) Subscribe(sid string, lastEventID int64) (<-chan StreamEvent, func(), error) {
	s.mu.Lock()
	run := s.runs[sid]
	s.mu.Unlock()
	if run != nil {
		run.mu.Lock()
		running := run.status == StatusRunning
		run.mu.Unlock()
		if running {
			ch, stop := run.stream.subscribe(lastEventID)
			return ch, stop, nil
		}
	}
	sess, err := s.store.Session(sid)
	if err != nil {
		return nil, nil, err
	}
	s.convergeStale(sess)
	if sess.Status == StatusIdle {
		ch := make(chan StreamEvent)
		close(ch)
		return ch, func() {}, nil
	}
	rs, err := s.store.RunStateOf(sid)
	if err != nil {
		return nil, nil, err
	}
	synth := newSessionStream()
	switch rs.Status {
	case StatusDone:
		id := synth.publish(EventDone, map[string]any{
			"plan_id": rs.PlanID, "metadata": map[string]any{"tokens": 0, "cost": rs.Cost},
		})
		synth.markTerminal(id)
	case StatusPausedClarify:
		id := synth.publish(EventClarifyNeeded, map[string]any{"questions": sess.ClarifyQuestions})
		synth.markTerminal(id)
	default:
		id := synth.publish(EventError, map[string]any{
			"code": "interrupted", "message": rs.LastError, "retryable": true,
		})
		synth.markTerminal(id)
	}
	ch, stop := synth.subscribe(lastEventID)
	return ch, stop, nil
}

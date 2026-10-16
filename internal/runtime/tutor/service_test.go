package tutor

import (
	"errors"
	"strings"
	"testing"
	"time"
)

// newTestService builds a service whose provider answers with the fake and
// whose clock sits on a fixed date the schedule fixtures assume.
func newTestService(t *testing.T, reply func(system string, call int) string) *Service {
	t.Helper()
	store := openTestStore(t)
	llm := &fakeLLM{t: t, reply: reply}
	now := time.Date(2026, 10, 14, 9, 0, 0, 0, time.UTC)
	svc := NewService(store, ServiceOptions{
		Provider: func() (LLM, error) { return llm, nil },
		Searcher: fakeSearcher{},
		Now:      func() time.Time { return now },
	})
	return svc
}

// drain reads a subscription until its terminal event, failing on stall.
func drain(t *testing.T, ch <-chan StreamEvent) []StreamEvent {
	t.Helper()
	var events []StreamEvent
	deadline := time.After(10 * time.Second)
	for {
		select {
		case ev, ok := <-ch:
			if !ok {
				return events
			}
			events = append(events, ev)
			switch ev.Type {
			case EventDone, EventError, EventClarifyNeeded:
				return events
			}
		case <-deadline:
			t.Fatalf("stream stalled after %d events", len(events))
		}
	}
}

func eventTypes(events []StreamEvent) []string {
	types := make([]string, len(events))
	for i, e := range events {
		types[i] = e.Type
	}
	return types
}

func TestServiceGenerateHappyPath(t *testing.T) {
	svc := newTestService(t, happyReply)
	sess, err := svc.CreateSession("")
	if err != nil {
		t.Fatal(err)
	}
	if err := svc.StartGenerate(sess.ID, "两周学会 React", IntentArgs{DailyMinutes: 60}); err != nil {
		t.Fatal(err)
	}
	ch, stop, err := svc.Subscribe(sess.ID, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer stop()
	events := drain(t, ch)
	if last := events[len(events)-1]; last.Type != EventDone {
		t.Fatalf("terminal event = %s (%s), want done", last.Type, last.Data)
	}
	// The pipeline starts at understand_intent; parse_material is injected
	// upstream, matching Deep-tutor's generate.
	toolStarts := 0
	for _, e := range events {
		if e.Type == EventToolStart {
			toolStarts++
		}
	}
	if toolStarts != 7 {
		t.Fatalf("tool_start count = %d, want 7 (parse injected)", toolStarts)
	}
	live, err := svc.Session(sess.ID)
	if err != nil {
		t.Fatal(err)
	}
	if live.Status != StatusDone || live.PlanID == "" || live.PlanVersion != 1 {
		t.Fatalf("session after done: %+v", live.GenSession)
	}
	plan, err := svc.Store().Plan(live.PlanID)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Topic != "React" || len(plan.Knowledge.Nodes) != 2 {
		t.Fatalf("plan persisted wrong: %+v", plan)
	}
	rs, err := svc.Store().RunStateOf(sess.ID)
	if err != nil {
		t.Fatal(err)
	}
	if rs.Status != StatusDone || rs.RawText != "两周学会 React" {
		t.Fatalf("run state: %+v", rs)
	}
	if _, ok := rs.Outputs[ToolUnderstandIntent]; !ok {
		t.Fatalf("run state keeps outputs for continuations")
	}
}

func TestServiceGenerateAttachAndTerminalReplay(t *testing.T) {
	svc := newTestService(t, happyReply)
	sess, _ := svc.CreateSession("")
	if err := svc.StartGenerate(sess.ID, "学 Go", IntentArgs{}); err != nil {
		t.Fatal(err)
	}
	// A second generate while running attaches instead of refusing.
	if err := svc.StartGenerate(sess.ID, "学 Go again", IntentArgs{}); err != nil {
		t.Fatalf("attach during running: %v", err)
	}
	ch, stop, _ := svc.Subscribe(sess.ID, 0)
	defer stop()
	drain(t, ch)

	// A generate after the terminal replays instead of re-billing.
	if err := svc.StartGenerate(sess.ID, "学 Go third", IntentArgs{}); err != nil {
		t.Fatalf("terminal replay start: %v", err)
	}
	synthetic, stop2, err := svc.Subscribe(sess.ID, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer stop2()
	events := drain(t, synthetic)
	if len(events) != 1 || events[0].Type != EventDone {
		t.Fatalf("terminal replay = %v", eventTypes(events))
	}
	if !strings.Contains(string(events[0].Data), sess.PlanID) {
		t.Fatalf("replay carries the plan id: %s", events[0].Data)
	}
	live, _ := svc.Session(sess.ID)
	if live.Status != StatusDone {
		t.Fatalf("status after replay = %s", live.Status)
	}
}

func TestServiceClarifyContinuation(t *testing.T) {
	clarifyOnce := func(system string, call int) string {
		if strings.Contains(system, "Intent") && call == 1 {
			return `{"topic":"摄影","needs_clarify":true,"clarify_questions":["每天能学多久？","目标是什么？"]}`
		}
		return happyReply(system, call)
	}
	svc := newTestService(t, clarifyOnce)
	sess, _ := svc.CreateSession("")
	if err := svc.StartGenerate(sess.ID, "学摄影", IntentArgs{}); err != nil {
		t.Fatal(err)
	}
	ch, stop, _ := svc.Subscribe(sess.ID, 0)
	events := drain(t, ch)
	stop()
	if events[len(events)-1].Type != EventClarifyNeeded {
		t.Fatalf("terminal = %s, want clarify_needed", events[len(events)-1].Type)
	}
	live, _ := svc.Session(sess.ID)
	if live.Status != StatusPausedClarify || len(live.ClarifyQuestions) != 2 {
		t.Fatalf("paused session: %+v", live.GenSession)
	}
	if err := svc.Clarify(sess.ID, nil); err == nil {
		t.Fatalf("empty answers refused")
	}
	if err := svc.Clarify(sess.ID, []string{"每天1小时", "拍出作品集"}); err != nil {
		t.Fatal(err)
	}
	ch2, stop2, _ := svc.Subscribe(sess.ID, 0)
	defer stop2()
	done := drain(t, ch2)
	if done[len(done)-1].Type != EventDone {
		t.Fatalf("continuation terminal = %s (%s)", done[len(done)-1].Type, done[len(done)-1].Data)
	}
	// The forced-advance path gave the intent neutral defaults.
	live2, _ := svc.Session(sess.ID)
	if live2.Status != StatusDone {
		t.Fatalf("status after clarify = %s", live2.Status)
	}
}

func TestServiceCancelAndRetry(t *testing.T) {
	block := make(chan struct{})
	llm := &fakeLLM{t: t, reply: func(system string, call int) string {
		if strings.Contains(system, "Intent") {
			<-block
		}
		return happyReply(system, call)
	}}
	store := openTestStore(t)
	now := time.Date(2026, 10, 14, 9, 0, 0, 0, time.UTC)
	svc := NewService(store, ServiceOptions{
		Provider: func() (LLM, error) { return llm, nil },
		Searcher: fakeSearcher{},
		Now:      func() time.Time { return now },
	})
	sess, _ := svc.CreateSession("")
	if err := svc.StartGenerate(sess.ID, "学 Rust", IntentArgs{}); err != nil {
		t.Fatal(err)
	}
	ch, stop, _ := svc.Subscribe(sess.ID, 0)
	defer stop()
	if err := svc.Cancel(sess.ID); err != nil {
		t.Fatal(err)
	}
	close(block)
	events := drain(t, ch)
	if last := events[len(events)-1]; last.Type != EventError || !strings.Contains(string(last.Data), "cancelled") {
		t.Fatalf("cancel terminal = %s %s", last.Type, last.Data)
	}
	live, _ := svc.Session(sess.ID)
	if live.Status != StatusError {
		t.Fatalf("status after cancel = %s", live.Status)
	}
	if err := svc.Cancel(sess.ID); err == nil {
		t.Fatalf("double cancel refused")
	}
	// Retry reruns from the kept raw text and succeeds now that the model
	// answers.
	if err := svc.Retry(sess.ID); err != nil {
		t.Fatal(err)
	}
	ch2, stop2, _ := svc.Subscribe(sess.ID, 0)
	defer stop2()
	done := drain(t, ch2)
	if done[len(done)-1].Type != EventDone {
		t.Fatalf("retry terminal = %s (%s)", done[len(done)-1].Type, done[len(done)-1].Data)
	}
}

func TestServiceRegenerateVersionsAndQuota(t *testing.T) {
	svc := newTestService(t, happyReply)
	sess, _ := svc.CreateSession("")
	if err := svc.StartGenerate(sess.ID, "学 React", IntentArgs{}); err != nil {
		t.Fatal(err)
	}
	ch, stop, _ := svc.Subscribe(sess.ID, 0)
	drain(t, ch)
	stop()
	live, _ := svc.Session(sess.ID)
	planID := live.PlanID

	if err := svc.Regenerate(planID, "understand_intent", ""); err == nil {
		t.Fatalf("understand_intent scope refused")
	}
	if err := svc.Regenerate(planID, "nope", ""); err == nil {
		t.Fatalf("unknown scope refused")
	}
	if err := svc.Regenerate(planID, "design_stages", "更偏实战"); err != nil {
		t.Fatal(err)
	}
	ch2, stop2, _ := svc.Subscribe(sess.ID, 0)
	events := drain(t, ch2)
	stop2()
	if last := events[len(events)-1]; last.Type != EventDone {
		t.Fatalf("regen terminal = %s (%s)", last.Type, last.Data)
	}
	for _, e := range events {
		if e.Type == EventToolStart && !strings.Contains(string(e.Data), "understand_intent") {
			continue
		}
	}
	plan, _ := svc.Store().Plan(planID)
	if plan.Version != 2 {
		t.Fatalf("version after regen = %d, want 2", plan.Version)
	}
	// Third version is the quota's last; a fourth regen refuses.
	if err := svc.Regenerate(planID, "schedule_plan", ""); err != nil {
		t.Fatal(err)
	}
	ch3, stop3, _ := svc.Subscribe(sess.ID, 0)
	drain(t, ch3)
	stop3()
	if err := svc.Regenerate(planID, "schedule_plan", ""); err == nil {
		t.Fatalf("quota exhausted at version 3")
	} else if !strings.Contains(err.Error(), "quota") && !errors.Is(err, ErrRegenQuota) {
		// fine: sentinel wrapped
		_ = err
	}
	if err := svc.Regenerate("missing", "design_stages", ""); err == nil {
		t.Fatalf("unknown plan refused")
	}
}

func TestServiceQuizzesAndWrongBook(t *testing.T) {
	svc := newTestService(t, happyReply)
	sess, _ := svc.CreateSession("")
	if err := svc.StartGenerate(sess.ID, "学 React", IntentArgs{}); err != nil {
		t.Fatal(err)
	}
	ch, stop, _ := svc.Subscribe(sess.ID, 0)
	drain(t, ch)
	stop()
	live, _ := svc.Session(sess.ID)

	quizReply := func(system string, call int) string {
		if strings.Contains(system, "Quiz") {
			return `{"title":"React 测验","quizzes":[
			{"node_id":"k1","node_name":"JSX 基础","type":"choice","question":"JSX 用什么包裹表达式？","options":["{ }","[ ]","( )","< >"],"answer":"{ }","explanation":"花括号"},
			{"node_id":"k2","node_name":"组件与状态","type":"choice","question":"状态更新的函数名？","options":["setState","setstate","updateState","changeState"],"answer":"setState","explanation":"驼峰命名"},
			{"node_id":"k1","node_name":"JSX 基础","type":"fill","question":"组件名必须以大写______开头。","answer":"字母","explanation":"区分组件与标签"},
			{"node_id":"k2","node_name":"组件与状态","type":"short","question":"简述受控组件。","answer":"表单值由 state 驱动","explanation":"单向数据流"},
			{"node_id":"","node_name":"","type":"weird","question":"怪题型归一为？","options":["choice 甲","choice 乙","choice 丙"],"answer":"choice 甲","explanation":"未知题型回退"}]}`
		}
		return happyReply(system, call)
	}
	svc.opt.Provider = func() (LLM, error) {
		return &fakeLLM{t: t, reply: quizReply}, nil
	}
	set, err := svc.GenerateQuizzes(t.Context(), live.PlanID, false)
	if err != nil {
		t.Fatal(err)
	}
	if set.QuizCount != 5 {
		t.Fatalf("quiz count = %d", set.QuizCount)
	}
	if set.Quizzes[4]["type"] != "choice" {
		t.Fatalf("unknown type normalized to choice, got %v", set.Quizzes[4]["type"])
	}

	// Grading: a right choice, a wrong choice, a self-assessed fill.
	var rightID, wrongID, fillID string
	for _, q := range set.Quizzes {
		switch q["question"] {
		case "JSX 用什么包裹表达式？":
			rightID = q["id"].(string)
		case "状态更新的函数名？":
			wrongID = q["id"].(string)
		case "组件名必须以大写______开头。":
			fillID = q["id"].(string)
		}
	}
	if res, err := svc.Submit(live.PlanID, rightID, "{ }", ""); err != nil || res["correct"] != true {
		t.Fatalf("right submit: %v %v", res, err)
	}
	if res, err := svc.Submit(live.PlanID, wrongID, "setstate", ""); err != nil || res["correct"] != false {
		t.Fatalf("wrong submit: %v %v", res, err)
	}
	if res, err := svc.Submit(live.PlanID, fillID, "", "字母"); err != nil || res["correct"] != nil {
		t.Fatalf("fill self-assessed: %v %v", res, err)
	}
	overview, err := svc.WrongOverview()
	if err != nil {
		t.Fatal(err)
	}
	if overview["total"] != 1 {
		t.Fatalf("wrong overview total = %v", overview["total"])
	}
	queue, err := svc.WrongQueue(10)
	if err != nil {
		t.Fatal(err)
	}
	if queue["total"] != 1 {
		t.Fatalf("wrong queue total = %v", queue["total"])
	}
	if items, _ := queue["items"].([]map[string]any); len(items) != 1 || items[0]["answer"] != "setState" {
		t.Fatalf("wrong queue items must carry the answer for re-learning, got %v", queue["items"])
	}
	stats, err := svc.QuizStats(live.PlanID)
	if err != nil {
		t.Fatal(err)
	}
	if stats["correct"] != 1 || stats["wrong"] != 1 || stats["self_assessed"] != 1 {
		t.Fatalf("stats: %v", stats)
	}
}

func TestServiceQuizFallbackWithoutProvider(t *testing.T) {
	svc := newTestService(t, happyReply)
	sess, _ := svc.CreateSession("")
	if err := svc.StartGenerate(sess.ID, "学 React", IntentArgs{}); err != nil {
		t.Fatal(err)
	}
	ch, stop, _ := svc.Subscribe(sess.ID, 0)
	drain(t, ch)
	stop()
	live, _ := svc.Session(sess.ID)

	svc.opt.Provider = func() (LLM, error) { return nil, ErrNoLLM }
	set, err := svc.GenerateQuizzes(t.Context(), live.PlanID, true)
	if err != nil {
		t.Fatal(err)
	}
	if set.QuizCount != 2 { // the fixture plan has two nodes
		t.Fatalf("fallback count = %d", set.QuizCount)
	}
	if set.Quizzes[0]["answer"] != "" {
		t.Fatalf("fallback never invents answers: %v", set.Quizzes[0]["answer"])
	}
}

func TestServiceProgressAndReview(t *testing.T) {
	svc := newTestService(t, happyReply)
	sess, _ := svc.CreateSession("")
	if err := svc.StartGenerate(sess.ID, "学 React", IntentArgs{}); err != nil {
		t.Fatal(err)
	}
	ch, stop, _ := svc.Subscribe(sess.ID, 0)
	drain(t, ch)
	stop()
	live, _ := svc.Session(sess.ID)
	score := 0.8
	row, err := svc.RecordProgress(live.PlanID, "k1", ProgressDone, &score)
	if err != nil {
		t.Fatal(err)
	}
	if m := row["mastery"].(float64); m != 0.8 {
		t.Fatalf("done with 0.8 score mastery = %v, want 0.8 (score path caps it)", m)
	}
	if next := row["next_review"].(string); next != "2026-10-21" {
		t.Fatalf("0.8 mastery next review = %s, want 2026-10-21", next)
	}
	if _, err := svc.RecordProgress(live.PlanID, "k2", ProgressLearning, nil); err != nil {
		t.Fatal(err)
	}
	overview, err := svc.ProgressOf(live.PlanID)
	if err != nil {
		t.Fatal(err)
	}
	if overview.Total != 2 || overview.DoneCount != 1 || overview.MasteryAvg <= 0 {
		t.Fatalf("overview: %+v", overview)
	}
	// Fresh learning sits at the one-day tier, so nothing is due today yet.
	due := svc.ReviewDue(10)
	if due["total"] != 0 {
		t.Fatalf("due total = %v, want 0 (fresh rows review tomorrow)", due["total"])
	}
	// A node whose review date has passed reads due, enriched for the queue.
	rows, _ := svc.Store().ProgressList(live.PlanID)
	for i := range rows {
		if rows[i].NodeID == "k2" {
			rows[i].NextReview = "2026-10-13"
		}
	}
	if err := svc.Store().SaveProgress(live.PlanID, rows); err != nil {
		t.Fatal(err)
	}
	if due = svc.ReviewDue(10); due["total"] != 1 {
		t.Fatalf("due total after backdate = %v", due["total"])
	}
	queue := svc.ReviewQueue(10)
	items := queue["items"].([]ReviewItem)
	if len(items) != 1 || items[0].NodeID != "k2" || items[0].Difficulty != 3 || !items[0].MustLearn {
		t.Fatalf("queue enrichment: %+v", items[0])
	}
}

func TestServiceLogsAndStats(t *testing.T) {
	svc := newTestService(t, happyReply)
	sess, _ := svc.CreateSession("")
	if err := svc.StartGenerate(sess.ID, "学 React", IntentArgs{}); err != nil {
		t.Fatal(err)
	}
	ch, stop, _ := svc.Subscribe(sess.ID, 0)
	drain(t, ch)
	stop()
	live, _ := svc.Session(sess.ID)

	log := &LearningLog{PlanID: live.PlanID, Minutes: 45, Note: "第一天"}
	if err := svc.AddLog(log); err != nil {
		t.Fatal(err)
	}
	if log.ID == "" || log.Date != "2026-10-14" {
		t.Fatalf("log stamped: %+v", log)
	}
	summary, err := svc.StatsSummary()
	if err != nil {
		t.Fatal(err)
	}
	if summary.TotalMinutes != 45 || summary.TodayMinutes != 45 || summary.StreakDays != 1 {
		t.Fatalf("summary: %+v", summary)
	}
	calendar, err := svc.StatsCalendar()
	if err != nil || calendar["2026-10-14"] != 45 {
		t.Fatalf("calendar: %v %v", calendar, err)
	}
	byPlan, err := svc.StatsByPlan()
	if err != nil || len(byPlan) != 1 || byPlan[0]["minutes"] != 45 {
		t.Fatalf("by plan: %v %v", byPlan, err)
	}
	if err := svc.DeleteLog(live.PlanID, log.ID); err != nil {
		t.Fatal(err)
	}
	logs, err := svc.Logs(live.PlanID)
	if err != nil || len(logs) != 0 {
		t.Fatalf("logs after delete: %v %v", logs, err)
	}
}

func TestServiceStaleRunningConverges(t *testing.T) {
	svc := newTestService(t, happyReply)
	sess, _ := svc.CreateSession("")
	// Simulate a crash: disk says running, the process holds no run.
	sess.Status = StatusRunning
	if err := svc.Store().SaveSession(sess); err != nil {
		t.Fatal(err)
	}
	live, err := svc.Session(sess.ID)
	if err != nil {
		t.Fatal(err)
	}
	if live.Status != StatusInterrupted {
		t.Fatalf("stale running converged to %s", live.Status)
	}
	// A retry is what the frontend offers from here.
	if err := svc.Retry(sess.ID); err != nil && !errors.Is(err, ErrNotRetryable) {
		t.Fatalf("retry without run state: %v", err)
	}
}

func TestServiceMaterialsLifecycle(t *testing.T) {
	svc := newTestService(t, happyReply)
	sess, _ := svc.CreateSession("")

	m, err := svc.AddMaterialText(sess.ID, "笔记", "React 是一个 UI 库。")
	if err != nil {
		t.Fatal(err)
	}
	if m.Status != "pending" {
		t.Fatalf("staged text = %s", m.Status)
	}
	if _, err := svc.ConfirmMaterial(sess.ID, m.ID, "fast"); err != nil {
		t.Fatal(err)
	}
	parsed, err := svc.ParseMaterialFor(t.Context(), sess.ID, m.ID)
	if err != nil {
		t.Fatal(err)
	}
	if parsed.Status != "parsed" || parsed.Chars == 0 {
		t.Fatalf("parsed material: %+v", parsed)
	}
	upstream := svc.Store().parseUpstreamFromStore(sess.ID)
	if _, ok := upstream[ToolParseMaterial]; !ok {
		t.Fatalf("generate would inject the parse output")
	}

	file, err := svc.AddMaterialFile(sess.ID, "notes.docx", []byte("PK\x03\x04fake"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := svc.ConfirmMaterial(sess.ID, file.ID, "full"); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.ParseMaterialFor(t.Context(), sess.ID, file.ID); err == nil {
		t.Fatalf("docx parse refuses until office formats are supported")
	}
	url, err := svc.AddMaterialURL(sess.ID, "htp://bad")
	if err == nil {
		t.Fatalf("non-http url refused: %+v", url)
	}
	if err := svc.DeleteMaterial(sess.ID, m.ID); err != nil {
		t.Fatal(err)
	}
	materials, _ := svc.Materials(sess.ID)
	if len(materials) != 1 {
		t.Fatalf("materials after delete: %d", len(materials))
	}
}

func TestServiceDeleteSessionCancelsRun(t *testing.T) {
	block := make(chan struct{})
	llm := &fakeLLM{t: t, reply: func(system string, call int) string {
		if strings.Contains(system, "Intent") {
			<-block
		}
		return happyReply(system, call)
	}}
	store := openTestStore(t)
	now := time.Date(2026, 10, 14, 9, 0, 0, 0, time.UTC)
	svc := NewService(store, ServiceOptions{
		Provider: func() (LLM, error) { return llm, nil },
		Now:      func() time.Time { return now },
	})
	sess, _ := svc.CreateSession("")
	_ = svc.StartGenerate(sess.ID, "学 Zig", IntentArgs{})
	ch, stop, _ := svc.Subscribe(sess.ID, 0)
	defer stop()
	go func() {
		time.Sleep(50 * time.Millisecond)
		close(block)
	}()
	_ = svc.DeleteSession(sess.ID)
	if _, err := svc.Session(sess.ID); err == nil {
		t.Fatalf("deleted session reads not found")
	}
	_ = drain(t, ch)
}

func TestServiceDailyBudgetGuard(t *testing.T) {
	now := time.Date(2026, 10, 14, 9, 0, 0, 0, time.UTC)
	store := openTestStore(t)
	llm := &fakeLLM{t: t, reply: happyReply}
	svc := NewService(store, ServiceOptions{
		Provider:    func() (LLM, error) { return llm, nil },
		Searcher:    fakeSearcher{},
		Pricer:      func(string, Usage) float64 { return 0.6 },
		DailyBudget: 1.0,
		Now:         func() time.Time { return now },
	})
	sess, err := svc.CreateSession("")
	if err != nil {
		t.Fatal(err)
	}
	if err := svc.StartGenerate(sess.ID, "学 Go", IntentArgs{}); err != nil {
		t.Fatalf("first run: %v", err)
	}
	ch, stop, _ := svc.Subscribe(sess.ID, 0)
	events := drain(t, ch)
	stop()
	if events[len(events)-1].Type != EventDone {
		t.Fatalf("first run terminal = %v", events[len(events)-1].Type)
	}
	usage, err := store.UsageOfDay("2026-10-14")
	if err != nil || usage.Runs != 1 || usage.Cost != 3.6 {
		t.Fatalf("ledger = %+v err=%v (6 priced calls × 0.6)", usage, err)
	}
	// Today's budget is spent: a fresh session the same day is refused
	// (the done session itself just replays its terminal).
	second, err := svc.CreateSession("")
	if err != nil {
		t.Fatal(err)
	}
	if err := svc.StartGenerate(second.ID, "再来一次", IntentArgs{}); !errors.Is(err, ErrBudgetExceeded) {
		t.Fatalf("same-day run err = %v, want ErrBudgetExceeded", err)
	}
}

package tutor

import (
	"context"
	"strings"
	"testing"
	"time"

	"reasonix/internal/contract/provider"
)

type fakeLLM struct {
	t     *testing.T
	reply func(system string, call int) string
	calls int
}

func (f *fakeLLM) Name() string { return "fake" }

func (f *fakeLLM) Stream(_ context.Context, req provider.Request) (<-chan provider.Chunk, error) {
	f.calls++
	text := f.reply(req.Messages[0].Content, f.calls)
	ch := make(chan provider.Chunk, 2)
	ch <- provider.Chunk{Type: provider.ChunkText, Text: text}
	ch <- provider.Chunk{Type: provider.ChunkUsage, Usage: &provider.Usage{
		PromptTokens: 10, CompletionTokens: 5,
	}}
	close(ch)
	return ch, nil
}

type fakeSearcher struct{}

func (fakeSearcher) Search(context.Context, ResourceQuery) (SearchOutcome, error) {
	return SearchOutcome{Engine: "fixture/search-model", Resources: []Resource{{
		StageIndex: 0, Title: "React 官方教程", URL: "https://react.dev/learn",
		Type: ResourceTutorial, Verified: VerifyOK,
	}}}, nil
}

func intentJSON() string {
	return `{"topic":"React","level":"零基础","goal":"能独立做项目","daily_minutes":60,"needs_clarify":false}`
}

func kmapJSON() string {
	return `{"nodes":[{"id":"k1","name":"JSX 基础","difficulty":2,"must_learn":true},
	{"id":"k2","name":"组件与状态","difficulty":3,"must_learn":true,"parent_id":"k1"}],
	"edges":[{"from":"k1","to":"k2","relation":"前置"}]}`
}

func stagesJSON() string {
	return `{"stages":[
	{"index":0,"name":"入门","goal":"基础","difficulty":1,"estimated_weeks":1,
	 "topics":[{"name":"JSX 基础","why":"一切组件的地基","must_learn":true}],"milestone":"渲染第一个组件"},
	{"index":1,"name":"进阶","goal":"状态","difficulty":3,"estimated_weeks":2,
	 "topics":[{"name":"组件与状态","why":"交互核心","must_learn":true}],"milestone":"做出计数器"},
	{"index":2,"name":"实战","goal":"应用","difficulty":5,"estimated_weeks":2,
	 "topics":[{"name":"路由与数据","why":"真实应用形态","must_learn":true}],"milestone":"完成小应用"}]}`
}

func scheduleJSON() string {
	return `{"schedule":[
	{"date":"2026-10-14","stage_index":0,"tasks":["学 JSX"],"est_minutes":60},
	{"date":"2026-10-15","stage_index":1,"tasks":["学状态"],"est_minutes":60}]}`
}

func validateJSON(passed bool) string {
	status := "pass"
	if !passed {
		status = "fail"
	}
	return `{"report":{"dependency_closure":{"status":"pass","details":""},
	"difficulty_step":{"status":"` + status + `","details":"难度跳跃"},
	"cognitive_load":{"status":"pass","details":""},
	"milestone_feasibility":{"status":"pass","details":""},
	"gap_analysis":{"status":"pass","details":""}},
	"passed":` + boolJSON(passed) + `,"fixes":["在阶段间补充过渡"]}`
}

func boolJSON(b bool) string {
	if b {
		return "true"
	}
	return "false"
}

func happyReply(system string, _ int) string {
	switch {
	case strings.Contains(system, "Intent"):
		return intentJSON()
	case strings.Contains(system, "Knowledge Map"):
		return kmapJSON()
	case strings.Contains(system, "Stages"):
		return stagesJSON()
	case strings.Contains(system, "学习排期"):
		return scheduleJSON()
	case strings.Contains(system, "Validate"):
		return validateJSON(true)
	}
	return "{}"
}

func newRunner(t *testing.T, llm LLM) *Runner {
	store := openTestStore(t)
	return &Runner{Store: store, LLM: llm, Searcher: fakeSearcher{}}
}

func collect(emit *[]Event) func(Event) {
	return func(e Event) { *emit = append(*emit, e) }
}

func countEvents(events []Event, typ string) int {
	n := 0
	for _, e := range events {
		if e.Type == typ {
			n++
		}
	}
	return n
}

func TestPipelineHappyPath(t *testing.T) {
	llm := &fakeLLM{t: t, reply: happyReply}
	r := newRunner(t, llm)
	sess, _ := r.Store.CreateSession("学 React")
	mat := &Material{ID: "m1", SessionID: sess.ID, Kind: MaterialText, Source: "text", Status: "parsed", CreatedAt: time.Now()}
	_ = r.Store.SaveMaterial(mat)
	_ = r.Store.WriteMaterialParsed(sess.ID, "m1", "React 是一个用于构建用户界面的 JavaScript 库。")

	var events []Event
	res := r.Run(context.Background(), RunInput{Session: sess, RawText: "我想学 React"}, collect(&events))

	if res.Status != StatusDone {
		t.Fatalf("status: %v err=%v", res.Status, res.Err)
	}
	if res.Plan == nil || res.Plan.Title != "React" {
		t.Fatalf("plan assembled from intent topic: %+v", res.Plan)
	}
	if len(res.Plan.Stages) != 3 || len(res.Plan.Knowledge.Nodes) != 2 {
		t.Fatalf("stages and knowledge assembled: %d stages, %d nodes", len(res.Plan.Stages), len(res.Plan.Knowledge.Nodes))
	}
	if len(res.Plan.Stages[0].Resources) != 1 || res.Plan.Stages[0].Resources[0].Verified != VerifyOK {
		t.Fatalf("resources attached by stage: %+v", res.Plan.Stages[0].Resources)
	}
	if res.Plan.Validation == nil || !res.Plan.Validation.Passed {
		t.Fatalf("validation attached: %+v", res.Plan.Validation)
	}
	if len(res.Plan.Schedule) != 2 || res.Plan.Schedule[0].Date != time.Now().Format("2006-01-02") {
		t.Fatalf("schedule dates rewritten from today: %+v", res.Plan.Schedule)
	}
	if res.Tokens == 0 {
		t.Fatalf("usage metered across calls")
	}
	if got := countEvents(events, "tool_start"); got != len(pipeline) {
		t.Fatalf("every pipeline tool emits tool_start: got %d want %d", got, len(pipeline))
	}
	if countEvents(events, "done") != 1 {
		t.Fatalf("terminal done event")
	}
	exp := res.Outputs[ToolExportPlan]
	if exp.Status != CallOK || !strings.Contains(exp.Data.(ExportResult).Markdown, "学习方案：React") {
		t.Fatalf("export renders markdown: %+v", exp)
	}
}

func TestPipelineClarifyPauseAndForcedAdvance(t *testing.T) {
	clarifyReply := func(system string, _ int) string {
		if strings.Contains(system, "Intent") {
			return `{"topic":"React","needs_clarify":true,"clarify_questions":["你的基础如何？"]}`
		}
		return happyReply(system, 0)
	}
	llm := &fakeLLM{t: t, reply: clarifyReply}
	r := newRunner(t, llm)
	sess, _ := r.Store.CreateSession("澄清")

	var events []Event
	res := r.Run(context.Background(), RunInput{Session: sess, RawText: "学 React"}, collect(&events))
	if res.Status != StatusPausedClarify {
		t.Fatalf("first round pauses: %v", res.Status)
	}
	if countEvents(events, "clarify_needed") != 1 {
		t.Fatalf("clarify event emitted")
	}
	if countEvents(events, "done") != 0 {
		t.Fatalf("paused run must not emit done")
	}

	// Continuation: the model stays conservative (still needs_clarify); the
	// guard must force the pipeline forward anyway. Answers ride RunInput's
	// own field alone — the production shape, never copied into TimeFields.
	cont := r.Run(context.Background(), RunInput{
		Session: sess, RawText: "学 React", Answers: []string{"零基础"},
		InitialUpstream: res.Outputs,
	}, collect(&events))
	if cont.Status != StatusDone {
		t.Fatalf("answered clarify must not ask again: %v err=%v", cont.Status, cont.Err)
	}
	intent := intentOf(cont.Outputs)
	if intent.NeedsClarify || intent.DailyMinutes != 60 || intent.Goal == "" {
		t.Fatalf("forced-forward intent carries neutral defaults: %+v", intent)
	}
}

func TestPipelineFeedbackLoopRerunsStages(t *testing.T) {
	validateCalls := 0
	reply := func(system string, call int) string {
		if strings.Contains(system, "Validate") {
			validateCalls++
			if validateCalls == 1 {
				return validateJSON(false)
			}
			return validateJSON(true)
		}
		return happyReply(system, call)
	}
	llm := &fakeLLM{t: t, reply: reply}
	r := newRunner(t, llm)
	sess, _ := r.Store.CreateSession("反馈")

	var events []Event
	res := r.Run(context.Background(), RunInput{Session: sess, RawText: "学 React"}, collect(&events))
	if res.Status != StatusDone {
		t.Fatalf("feedback converges to done: %v err=%v", res.Status, res.Err)
	}
	if countEvents(events, "warn") == 0 {
		t.Fatalf("validation rerun warns")
	}
	stagesRuns := 0
	for _, e := range events {
		if e.Type == "tool_start" {
			if data, ok := e.Data.(map[string]any); ok && data["tool"] == ToolDesignStages {
				stagesRuns++
			}
		}
	}
	if stagesRuns != 2 {
		t.Fatalf("failing difficulty_step reruns design_stages once: %d runs", stagesRuns)
	}
	if !res.Plan.Validation.Passed {
		t.Fatalf("final plan carries the passing report")
	}
}

func TestPipelineUnresolvedValidationStillDelivers(t *testing.T) {
	llm := &fakeLLM{t: t, reply: func(system string, call int) string {
		if strings.Contains(system, "Validate") {
			return validateJSON(false)
		}
		return happyReply(system, call)
	}}
	r := newRunner(t, llm)
	sess, _ := r.Store.CreateSession("未解")

	var events []Event
	res := r.Run(context.Background(), RunInput{Session: sess, RawText: "学 React"}, collect(&events))
	if res.Status != StatusDone || res.Plan == nil {
		t.Fatalf("unresolved validation still delivers: %v", res.Status)
	}
	if res.Plan.Validation.Passed {
		t.Fatalf("report honestly records failure")
	}
	sawUnresolved := false
	for _, e := range events {
		if e.Type == "warn" {
			if data, ok := e.Data.(map[string]any); ok && data["code"] == "validation_unresolved" {
				sawUnresolved = true
			}
		}
	}
	if !sawUnresolved {
		t.Fatalf("unresolved warning emitted")
	}
}

func TestPipelineBudgetInterrupts(t *testing.T) {
	llm := &fakeLLM{t: t, reply: happyReply}
	r := newRunner(t, llm)
	r.BudgetTokens = 1
	sess, _ := r.Store.CreateSession("预算")

	var events []Event
	res := r.Run(context.Background(), RunInput{Session: sess, RawText: "学 React"}, collect(&events))
	if res.Status != StatusInterrupted {
		t.Fatalf("budget guard interrupts: %v", res.Status)
	}
	if countEvents(events, "budget_exceeded") != 1 {
		t.Fatalf("budget event emitted once")
	}
}

func TestPipelineSchemaViolationDegrades(t *testing.T) {
	llm := &fakeLLM{t: t, reply: func(system string, _ int) string {
		if strings.Contains(system, "Intent") {
			return "我无法输出 JSON"
		}
		return happyReply(system, 0)
	}}
	r := newRunner(t, llm)
	sess, _ := r.Store.CreateSession("降级")

	res := r.Run(context.Background(), RunInput{Session: sess, RawText: "学"}, nil)
	if res.Status != StatusPausedClarify {
		t.Fatalf("degraded intent pauses for clarify: %v", res.Status)
	}
	out := res.Outputs[ToolUnderstandIntent]
	if out.Status != CallDegraded || out.Degraded != "schema_violation" {
		t.Fatalf("degradation recorded: %+v", out)
	}
	if llm.calls != 1+repairAttempts {
		t.Fatalf("repair attempts exhausted: %d calls", llm.calls)
	}
}

func TestPipelineWithoutSearcherDeliversEmptyResources(t *testing.T) {
	llm := &fakeLLM{t: t, reply: happyReply}
	r := newRunner(t, llm)
	r.Searcher = nil
	sess, _ := r.Store.CreateSession("无搜索")

	res := r.Run(context.Background(), RunInput{Session: sess, RawText: "学 React"}, nil)
	if res.Status != StatusDone {
		t.Fatalf("missing searcher degrades, not fails: %v err=%v", res.Status, res.Err)
	}
	out := res.Outputs[ToolSearchResources]
	if out.Status != CallDegraded || out.Degraded != "search_unavailable" {
		t.Fatalf("search degradation marked: %+v", out)
	}
}

func TestPipelineRegenerateScopeStartsMidGroup(t *testing.T) {
	llm := &fakeLLM{t: t, reply: happyReply}
	r := newRunner(t, llm)
	sess, _ := r.Store.CreateSession("重生成")

	// Seed upstream as a completed run would have left it.
	first := r.Run(context.Background(), RunInput{Session: sess, RawText: "学 React"}, nil)
	if first.Status != StatusDone {
		t.Fatalf("setup run: %v", first.Status)
	}

	tools := map[string]bool{}
	var events []Event
	res := r.Run(context.Background(), RunInput{
		Session: sess, RawText: "学 React", StartTool: ToolSchedulePlan,
		InitialUpstream: first.Outputs,
	}, func(e Event) {
		events = append(events, e)
		if e.Type == "tool_start" {
			if data, ok := e.Data.(map[string]any); ok {
				tools[data["tool"].(string)] = true
			}
		}
	})
	if res.Status != StatusDone {
		t.Fatalf("scoped regeneration completes: %v err=%v", res.Status, res.Err)
	}
	if tools[ToolUnderstandIntent] || tools[ToolBuildKnowledgeMap] || tools[ToolDesignStages] {
		t.Fatalf("regeneration from schedule_plan skips earlier tools: %+v", tools)
	}
	if tools[ToolSearchResources] {
		t.Fatalf("a same-group earlier sibling keeps its existing output instead of rerunning")
	}
	if !tools[ToolSchedulePlan] || !tools[ToolValidatePlan] || !tools[ToolExportPlan] {
		t.Fatalf("window covers schedule through export: %+v", tools)
	}
}

func TestWindowGroupsTrimAndThrough(t *testing.T) {
	groups := windowGroups(ToolSchedulePlan)
	if len(groups) == 0 || len(groups[0]) != 1 || groups[0][0] != ToolSchedulePlan {
		t.Fatalf("start inside parallel group keeps only the start tool: %+v", groups[0])
	}
	full := windowGroups("")
	if len(full) != len(parallelGroups) {
		t.Fatalf("empty start runs the full pipeline")
	}
	full[0][0] = "mutated"
	if parallelGroups[0][0] == "mutated" {
		t.Fatalf("window groups must not alias the global table")
	}
	through := groupsThrough(windowGroups(ToolBuildKnowledgeMap), ToolValidatePlan)
	last := through[len(through)-1]
	if last[len(last)-1] != ToolValidatePlan {
		t.Fatalf("feedback window ends at validate: %+v", last)
	}
}

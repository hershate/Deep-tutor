package tutor

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"sync"
)

// Event is one SSE-visible pipeline event; Type carries Deep-tutor's event
// vocabulary so frontends can be ported without renaming.
type Event struct {
	Type string
	Data any
}

// errPauseClarify stops the pipeline walk when the intent step needs input;
// Run maps it to the paused_clarify session state.
var errPauseClarify = errors.New("tutor: clarify pause")

// Runner executes the generation pipeline. It is single-run: the serve layer
// owns session state transitions and persistence around one Run call.
type Runner struct {
	Store        *Store
	LLM          LLM
	Searcher     Searcher
	Pricer       Pricer
	MaxTokens    int
	BudgetTokens int64 // 0 disables the budget guard
	MaxToolCalls int   // 0 means the default of 20
}

// RunInput is one pipeline invocation: a fresh generate, a clarify
// continuation, a retry, or a scoped regeneration.
type RunInput struct {
	Session         *GenSession
	RawText         string
	Answers         []string
	TimeFields      IntentArgs
	StartTool       string
	InitialUpstream map[string]ToolOutput
	// Feedback is the person's regeneration note, handed to the start tool's
	// fixes — Deep-tutor's args_by_tool[start_tool]={"fixes": [feedback]}.
	Feedback []string
}

// RunResult is the terminal outcome plus the full outputs map, so callers
// can persist partial state for clarify continuations and regenerations.
type RunResult struct {
	Status  Status
	Plan    *Plan
	Outputs map[string]ToolOutput
	Tokens  int64
	Cost    float64
	Err     error
}

const defaultMaxToolCalls = 20

func (r *Runner) Run(ctx context.Context, in RunInput, emit func(Event)) RunResult {
	if emit == nil {
		emit = func(Event) {}
	}
	result := RunResult{Status: StatusDone, Outputs: map[string]ToolOutput{}}
	for name, out := range in.InitialUpstream {
		result.Outputs[name] = reviveOutput(name, out)
	}

	maxCalls := r.MaxToolCalls
	if maxCalls == 0 {
		maxCalls = defaultMaxToolCalls
	}
	calls := 0

	runErr := r.runGroups(ctx, in, emit, result.Outputs, windowGroups(in.StartTool), maxCalls, &calls, &result, 1, "", true)
	switch {
	case runErr == nil:
		if result.Status == StatusInterrupted {
			return result
		}
		if intent := intentOf(result.Outputs); intent.NeedsClarify {
			result.Status = StatusPausedClarify
			emit(Event{Type: "clarify_needed", Data: map[string]any{"questions": intent.ClarifyQuestions}})
			return result
		}
		result.Status = StatusDone
		result.Plan = AssemblePlan(in.Session, result.Outputs)
		emit(Event{Type: "done", Data: map[string]any{
			"tokens": result.Tokens, "cost": result.Cost, "tools": calls,
		}})
		return result
	case errors.Is(runErr, errPauseClarify):
		result.Status = StatusPausedClarify
		emit(Event{Type: "clarify_needed", Data: map[string]any{
			"questions": intentOf(result.Outputs).ClarifyQuestions,
		}})
		return result
	default:
		result.Status = StatusError
		result.Err = runErr
		emit(Event{Type: "error", Data: map[string]any{
			"code": "tool_error", "message": runErr.Error(), "retryable": true,
		}})
		return result
	}
}

// runGroups walks execution groups once. The validate feedback loop re-enters
// with a narrower window; shared counters keep quotas honest across both.
// feedbackTarget names the one tool validate's fixes are handed to;
// allowFeedback is false inside a feedback window so reruns cannot nest.
func (r *Runner) runGroups(ctx context.Context, in RunInput, emit func(Event),
	outputs map[string]ToolOutput, groups [][]string, maxCalls int,
	calls *int, result *RunResult, attempt int, feedbackTarget string, allowFeedback bool) error {

	for _, group := range groups {
		if *calls >= maxCalls {
			result.Status = StatusInterrupted
			emit(Event{Type: "error", Data: map[string]any{
				"code": "max_tool_calls", "message": fmt.Sprintf("工具调用达上限 %d", maxCalls),
			}})
			return nil
		}

		type done struct {
			name string
			out  ToolOutput
		}
		results := make([]done, len(group))
		var wg sync.WaitGroup
		for i, name := range group {
			*calls++
			emit(Event{Type: "tool_start", Data: map[string]any{
				"tool": name, "step_index": pipelineStep(name),
				"total_steps": len(pipeline), "attempt": attempt,
			}})
			wg.Add(1)
			go func(i int, name string) {
				defer wg.Done()
				results[i] = done{name, r.execTool(ctx, in, outputs, name, attempt, feedbackTarget)}
			}(i, name)
		}
		wg.Wait()

		for _, res := range results {
			outputs[res.name] = res.out
			emit(Event{Type: "tool_end", Data: map[string]any{
				"tool": res.name, "step_index": pipelineStep(res.name),
				"total_steps": len(pipeline), "attempt": attempt,
				"status": string(res.out.Status), "meta": res.out.Meta,
			}})
			result.Tokens += res.out.Meta.Tokens
			result.Cost += res.out.Meta.Cost
		}

		if r.BudgetTokens > 0 && result.Tokens > r.BudgetTokens {
			result.Status = StatusInterrupted
			emit(Event{Type: "budget_exceeded", Data: map[string]any{
				"spent": result.Tokens, "limit": r.BudgetTokens,
			}})
			return nil
		}

		// Deterministic failure: the first error in pipeline order ends the
		// run; parallel siblings' results still count toward metering.
		for _, name := range pipeline {
			for _, res := range results {
				if res.name == name && res.out.Status == CallError {
					return fmt.Errorf("tutor: %s: %s", name, res.out.Error)
				}
			}
		}

		if in.Session != nil {
			partial := AssemblePlan(in.Session, outputs)
			emit(Event{Type: "plan_chunk", Data: map[string]any{"partial_plan": partial}})
		}

		if slices.Contains(group, ToolUnderstandIntent) && intentOf(outputs).NeedsClarify {
			return errPauseClarify
		}
		if allowFeedback && slices.Contains(group, ToolValidatePlan) {
			if err := r.feedbackLoop(ctx, in, emit, outputs, maxCalls, calls, result); err != nil {
				return err
			}
		}
	}
	return nil
}

// feedbackLoop reruns the failing section up to maxFeedbackRounds times,
// feeding validate's fixes to the feedback target only.
func (r *Runner) feedbackLoop(ctx context.Context, in RunInput, emit func(Event),
	outputs map[string]ToolOutput, maxCalls int, calls *int, result *RunResult) error {

	for round := 1; round <= maxFeedbackRounds; round++ {
		data := validateOf(outputs)
		if data.Passed {
			return nil
		}
		target, check := firstFailingCheck(data)
		if target == "" {
			return nil
		}
		emit(Event{Type: "warn", Data: map[string]any{
			"code":    "validation_failed",
			"message": fmt.Sprintf("第 %d 轮校验未通过（%s），正在重跑 %s", round, check, target),
		}})
		if *calls >= maxCalls {
			result.Status = StatusInterrupted
			emit(Event{Type: "error", Data: map[string]any{
				"code": "max_tool_calls", "message": fmt.Sprintf("工具调用达上限 %d", maxCalls),
			}})
			return nil
		}
		window := groupsThrough(windowGroups(target), ToolValidatePlan)
		if err := r.runGroups(ctx, in, emit, outputs, window, maxCalls, calls, result, round+1, target, false); err != nil {
			return err
		}
		if result.Status == StatusInterrupted {
			return nil
		}
	}
	if data := validateOf(outputs); !data.Passed {
		emit(Event{Type: "warn", Data: map[string]any{
			"code": "validation_unresolved", "message": "校验仍未通过，按当前方案交付并附报告",
		}})
	}
	return nil
}

func (r *Runner) execTool(ctx context.Context, in RunInput, outputs map[string]ToolOutput,
	name string, attempt int, feedbackTarget string) ToolOutput {

	switch name {
	case ToolParseMaterial:
		return r.execParseMaterial(ctx, in)
	case ToolUnderstandIntent:
		return r.execUnderstandIntent(ctx, in, attempt)
	case ToolBuildKnowledgeMap:
		var fixes []string
		if feedbackTarget == name {
			fixes = validateOf(outputs).Fixes
		}
		if in.StartTool == name {
			fixes = append(fixes, in.Feedback...)
		}
		return r.execKnowledgeMap(ctx, in, outputs, fixes)
	case ToolDesignStages:
		var fixes []string
		if feedbackTarget == name {
			fixes = validateOf(outputs).Fixes
		}
		if in.StartTool == name {
			fixes = append(fixes, in.Feedback...)
		}
		return r.execDesignStages(ctx, in, outputs, fixes, attempt)
	case ToolSearchResources:
		return r.execSearchResources(ctx, in, outputs)
	case ToolSchedulePlan:
		return r.execSchedulePlan(ctx, in, outputs)
	case ToolValidatePlan:
		return r.execValidatePlan(ctx, in, outputs)
	case ToolExportPlan:
		return r.execExportPlan(ctx, in, outputs)
	default:
		return errorOutput(name, fmt.Errorf("tutor: unknown tool %q", name), toolMeta{})
	}
}

func validateOf(outputs map[string]ToolOutput) ValidatePlanData {
	out, ok := outputs[ToolValidatePlan]
	if !ok || out.Status == CallError {
		return ValidatePlanData{Passed: true}
	}
	switch v := out.Data.(type) {
	case ValidatePlanData:
		return v
	case *ValidatePlanData:
		if v != nil {
			return *v
		}
	}
	return ValidatePlanData{Passed: true}
}

var checkOrder = []string{
	"dependency_closure", "difficulty_step", "cognitive_load",
	"milestone_feasibility", "gap_analysis",
}

func firstFailingCheck(data ValidatePlanData) (target, check string) {
	report := data.Report
	statuses := map[string]string{
		"dependency_closure":    report.DependencyClosure.Status,
		"difficulty_step":       report.DifficultyStep.Status,
		"cognitive_load":        report.CognitiveLoad.Status,
		"milestone_feasibility": report.MilestoneFeasibility.Status,
		"gap_analysis":          report.GapAnalysis.Status,
	}
	for _, name := range checkOrder {
		if statuses[name] == "fail" {
			return feedbackTargets[name], name
		}
	}
	return "", ""
}

// windowGroups returns fresh execution groups from startTool (empty = full
// pipeline). A start inside a parallel group keeps that tool and its later
// group siblings only. Returned groups never alias the global table.
func windowGroups(startTool string) [][]string {
	groups := make([][]string, len(parallelGroups))
	for i, g := range parallelGroups {
		groups[i] = slices.Clone(g)
	}
	if startTool == "" {
		return groups
	}
	var out [][]string
	for _, group := range groups {
		if idx := slices.Index(group, startTool); idx >= 0 {
			out = append(out, group[idx:])
			continue
		}
		if len(out) > 0 {
			out = append(out, group)
		}
	}
	return out
}

// groupsThrough keeps groups up to and including the one holding endTool,
// trimming that final group at endTool.
func groupsThrough(groups [][]string, endTool string) [][]string {
	var out [][]string
	for _, group := range groups {
		if idx := slices.Index(group, endTool); idx >= 0 {
			out = append(out, group[:idx+1])
			return out
		}
		out = append(out, group)
	}
	return out
}

func pipelineStep(tool string) int {
	for i, name := range pipeline {
		if name == tool {
			return i + 1
		}
	}
	return len(pipeline)
}

// reviveOutput restores typed data after a JSON round trip through the
// session store, so clarify continuations and regenerations keep working
// with the same typed values.
func reviveOutput(tool string, out ToolOutput) ToolOutput {
	if out.Data == nil {
		return out
	}
	raw, ok := out.Data.(map[string]any)
	if !ok {
		return out
	}
	encoded, err := json.Marshal(raw)
	if err != nil {
		return out
	}
	revive := func(v any) bool { return json.Unmarshal(encoded, v) == nil }
	switch tool {
	case ToolParseMaterial:
		var v ParsedMaterial
		if revive(&v) {
			out.Data = &v
		}
	case ToolUnderstandIntent:
		var v Intent
		if revive(&v) {
			out.Data = v
		}
	case ToolBuildKnowledgeMap:
		var v KnowledgeMap
		if revive(&v) {
			out.Data = v
		}
	case ToolDesignStages:
		var v stagesData
		if revive(&v) {
			out.Data = v
		}
	case ToolSearchResources:
		var v ResourcesResult
		if revive(&v) {
			out.Data = v
		}
	case ToolSchedulePlan:
		var v ScheduleData
		if revive(&v) {
			out.Data = &v
		}
	case ToolValidatePlan:
		var v ValidatePlanData
		if revive(&v) {
			out.Data = v
		}
	case ToolExportPlan:
		var v ExportResult
		if revive(&v) {
			out.Data = v
		}
	}
	return out
}

package boot

import (
	"context"
	"encoding/json"
	"path/filepath"
	"testing"
	"time"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/state/sessionstore"
)

func TestEffectSurfaceDraftVerbatimAndPersisted(t *testing.T) {
	isolateConfigHome(t)
	dir := robustTempDir(t)
	t.Chdir(dir)
	const spec = `{"spec_version":1,"body":[{"kind":"editable_text","key":"draft","label":"Draft","field":{"type":"string","required":true}},{"kind":"diff_preview","diff":{"before":"old\n","after":"new\n"}}]}`
	const draft = "  edited\n\n@file /command <script>\t "
	probe := &capabilityProbeProvider{calls: []string{chartCall(t, "tool:render_surface", spec)}}
	prompts := make(chan event.Ask, 1)
	ctrl := buildChartAssembly(t, "surface-draft-effect", probe, dir, event.FuncSink(func(e event.Event) {
		if e.Kind == event.AskRequest {
			prompts <- e.Ask
		}
	}))
	t.Cleanup(ctrl.Close)
	ctrl.EnableInteractiveApproval()
	path := filepath.Join(dir, ".reasonix", "sessions", "draft.jsonl")
	ctrl.SetSessionPath(path)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- ctrl.Run(ctx, "edit the draft") }()
	select {
	case ask := <-prompts:
		if ask.Questions[0].Surface.Body[1].Diff == nil {
			t.Fatal("diff did not reach frontend")
		}
		value, _ := json.Marshal(map[string]string{"draft": draft})
		if err := ctrl.AnswerQuestion(ask.ID, []event.AskAnswer{{QuestionID: "surface", Value: value}}); err != nil {
			t.Fatal(err)
		}
	case err := <-done:
		t.Fatalf("run ended before draft: %v", err)
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	assertDraftResult(t, probe.toolResults(), draft)
	ctrl.Close()
	loaded, err := sessionstore.LoadSession(path)
	if err != nil {
		t.Fatal(err)
	}
	var results []string
	for _, m := range loaded.Messages {
		if m.Role == provider.RoleTool {
			results = append(results, m.Content)
		}
	}
	assertDraftResult(t, results, draft)
}

func assertDraftResult(t *testing.T, results []string, want string) {
	t.Helper()
	for _, result := range results {
		var answer struct {
			Values map[string]string `json:"values"`
		}
		if json.Unmarshal([]byte(result), &answer) == nil && answer.Values["draft"] == want {
			return
		}
	}
	t.Fatalf("verbatim draft absent from %v", results)
}

package boot

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/contract/surfacespec"
	"reasonix/internal/state/sessionstore"
)

const surfaceSpecJSON = `{"spec_version":1,"title":"Settings","body":[{"kind":"field","key":"count","label":"Count","field":{"type":"number","required":true,"min":1,"max":5}},{"kind":"choice_card","key":"mode","label":"Mode","field":{"type":"choice","options":[{"value":"fast","label":"Fast","preview":"Short run"},{"value":"full","label":"Full"}]}}]}`

func TestEffectRenderSurfaceHiddenTypedReplayAndResume(t *testing.T) {
	isolateConfigHome(t)
	dir := robustTempDir(t)
	t.Chdir(dir)
	probe := &capabilityProbeProvider{calls: []string{
		`{"action":"inspect","capability_id":"tool:render_surface"}`,
		chartCall(t, "tool:render_surface", surfaceSpecJSON),
		chartCall(t, "tool:render_surface", strings.Replace(surfaceSpecJSON, `"title"`, `"url":"https://invalid.test","title"`, 1)),
	}}
	prompts := make(chan event.Ask, 8)
	sink := event.FuncSink(func(e event.Event) {
		if e.Kind == event.AskRequest {
			prompts <- e.Ask
		}
	})
	ctrl := buildChartAssembly(t, "boot-surface-effect", probe, dir, sink)
	if !ctrl.NativeSurfaces() {
		t.Fatal("native capability missing")
	}
	t.Cleanup(ctrl.Close)
	ctrl.EnableInteractiveApproval()
	path := filepath.Join(dir, ".reasonix", "sessions", "surface.jsonl")
	ctrl.SetSessionPath(path)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- ctrl.Run(ctx, "configure the run") }()
	var ask event.Ask
	select {
	case ask = <-prompts:
	case err := <-done:
		t.Fatalf("run ended before prompt: %v", err)
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	if ask.Questions[0].Surface == nil {
		t.Fatal("native spec did not reach frontend sink")
	}
	if err := ctrl.AnswerQuestion(ask.ID, []event.AskAnswer{{QuestionID: "surface", Value: json.RawMessage(`{"count":"2"}`)}}); !errors.Is(err, surfacespec.ErrValueInvalid) {
		t.Fatal(err)
	}
	ctrl.ReplayPendingPrompts()
	select {
	case replay := <-prompts:
		if replay.ID != ask.ID || replay.Questions[0].Surface == nil {
			t.Fatal(replay)
		}
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	if err := ctrl.AnswerQuestion(ask.ID, []event.AskAnswer{{QuestionID: "surface", Value: json.RawMessage(`{"count":2,"mode":"fast"}`)}}); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	probe.mu.Lock()
	first := probe.reqs[0]
	probe.mu.Unlock()
	for _, schema := range first.Tools {
		if schema.Name == "render_surface" || strings.Contains(string(schema.Parameters), "spec_version") {
			t.Fatalf("surface schema leaked into prefix: %s", schema.Name)
		}
	}
	for _, message := range first.Messages {
		if strings.Contains(message.Content, "render_surface") {
			t.Fatal("surface hint leaked into prefix")
		}
	}
	results := probe.toolResults()
	if len(results) != 3 || !strings.Contains(results[0], "choice_card") || !strings.Contains(results[1], `"count":2`) || !strings.Contains(results[2], string(surfacespec.CodeSchemaInvalid)) {
		t.Fatalf("results: %v", results)
	}
	ctrl.Close()
	loaded, err := sessionstore.LoadSession(path)
	if err != nil {
		t.Fatal(err)
	}
	assertPersistedSurface(t, loaded.Messages)
	resume := &capabilityProbeProvider{}
	ctrl = buildChartAssembly(t, "boot-surface-effect-resume", resume, dir)
	defer ctrl.Close()
	if err := ctrl.Resume(loaded, path); err != nil {
		t.Fatal(err)
	}
	if err := ctrl.Run(context.Background(), "and now?"); err != nil {
		t.Fatal(err)
	}
	resume.mu.Lock()
	defer resume.mu.Unlock()
	assertPersistedSurface(t, resume.reqs[0].Messages)
}

func assertPersistedSurface(t *testing.T, messages []provider.Message) {
	t.Helper()
	foundSpec, foundAnswer := false, false
	for _, m := range messages {
		for _, call := range m.ToolCalls {
			var proxy struct {
				ID   string          `json:"capability_id"`
				Args json.RawMessage `json:"arguments"`
			}
			if call.Name == "use_capability" && json.Unmarshal([]byte(call.Arguments), &proxy) == nil && proxy.ID == "tool:render_surface" {
				if _, err := surfacespec.Parse(proxy.Args); err == nil {
					foundSpec = true
				}
			}
		}
		if m.Role == provider.RoleTool && strings.Contains(m.Content, `"values":{"count":2,"mode":"fast"}`) {
			foundAnswer = true
		}
	}
	if !foundSpec || !foundAnswer {
		t.Fatalf("persisted spec=%v answer=%v", foundSpec, foundAnswer)
	}
}

func TestEffectRenderSurfaceDisabledWithoutNativeFrontend(t *testing.T) {
	isolateConfigHome(t)
	dir := robustTempDir(t)
	t.Chdir(dir)
	probe := &capabilityProbeProvider{calls: []string{`{"action":"inspect","capability_id":"tool:render_surface"}`}}
	ctrl := buildChartAssembly(t, "boot-surface-disabled", probe, dir)
	ctrl.Close()
	ctrl, err := Build(context.Background(), Options{Sink: event.Discard, WorkspaceRoot: dir})
	if err != nil {
		t.Fatal(err)
	}
	defer ctrl.Close()
	if ctrl.NativeSurfaces() {
		t.Fatal("unsupported frontend enabled surfaces")
	}
	if err := ctrl.Run(context.Background(), "inspect the capability"); err != nil {
		t.Fatal(err)
	}
	results := probe.toolResults()
	if len(results) != 1 || strings.Contains(results[0], "choice_card") || strings.Contains(results[0], "spec_version") {
		t.Fatalf("unsupported frontend received schema: %v", results)
	}
}

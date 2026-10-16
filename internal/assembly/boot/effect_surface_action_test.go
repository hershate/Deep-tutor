package boot

import (
	"context"
	"encoding/json"
	"path/filepath"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/contract/surfacespec"
	"reasonix/internal/session/control"
	"reasonix/internal/state/sessionstore"
	"strings"
	"testing"
	"time"
)

func TestEffectSurfaceActionNonblockingTailAndReplay(t *testing.T) {
	isolateConfigHome(t)
	dir := robustTempDir(t)
	t.Chdir(dir)
	const spec = `{"spec_version":1,"body":[{"kind":"editable_text","key":"draft","label":"Draft","field":{"type":"string","required":true}},{"kind":"button","label":"Continue","actionId":"continue"}]}`
	const draft = "  @missing /command\n\n<script>\t "
	probe := &capabilityProbeProvider{calls: []string{chartCall(t, "tool:render_surface", spec)}}
	asks := 0
	done := make(chan struct{}, 8)
	ctrl := buildChartAssembly(t, "surface-action-effect", probe, dir, event.FuncSink(func(e event.Event) {
		if e.Kind == event.TurnDone {
			done <- struct{}{}
		}
		if e.Kind == event.AskRequest {
			asks++
		}
	}))
	defer ctrl.Close()
	ctrl.EnableInteractiveApproval()
	path := filepath.Join(dir, ".reasonix", "sessions", "actions.jsonl")
	ctrl.SetSessionPath(path)
	if err := ctrl.SetInboxPaused(true); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := ctrl.Run(ctx, "show a draft and keep working"); err != nil {
		t.Fatal(err)
	}
	if asks != 0 {
		t.Fatal("nonblocking surface opened ask")
	}
	state, err := ctrl.SurfaceActionState("probe-0")
	if err != nil {
		t.Fatal(err)
	}
	values, _ := json.Marshal(map[string]string{"draft": draft})
	action := state.Action
	action.ActionID = "continue"
	action.RequestID = "request-1"
	action.Values = values
	rec, err := ctrl.SubmitSurfaceAction(control.SurfaceActionRequest{Action: action, Delivery: "queue"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if rec.ItemID == "" {
		t.Fatal("missing receipt")
	}
	select {
	case <-done:
	default:
	}
	if err := ctrl.SetInboxPaused(false); err != nil {
		t.Fatal(err)
	}
	select {
	case <-done:
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	probe.mu.Lock()
	defer probe.mu.Unlock()
	found := false
	firstTools, _ := json.Marshal(probe.reqs[0].Tools)
	for _, req := range probe.reqs {
		tools, _ := json.Marshal(req.Tools)
		if string(tools) != string(firstTools) {
			t.Fatal("provider tool prefix changed")
		}
		if req.Messages[0].Content != probe.reqs[0].Messages[0].Content {
			t.Fatal("system prefix changed")
		}
		for _, m := range req.Messages {
			if m.LocalOnly {
				t.Fatal("local action receipt reached provider")
			}
			start := strings.Index(m.Content, "<surface_action>\n")
			if start < 0 {
				continue
			}
			tail := m.Content[start+len("<surface_action>\n"):]
			body, _, complete := strings.Cut(tail, "\n</surface_action>")
			if !complete {
				t.Fatal("incomplete note")
			}
			var got surfacespec.Action
			if json.Unmarshal([]byte(body), &got) != nil {
				t.Fatal("not a structured note")
			}
			var answer map[string]string
			if json.Unmarshal(got.Values, &answer) != nil || answer["draft"] != draft || got.ActionID != "continue" {
				t.Fatalf("changed action: %+v", got)
			}
			found = true
		}
	}
	if !found {
		t.Fatal("surface action did not reach provider")
	}
	loaded, err := sessionstore.LoadSession(path)
	if err != nil {
		t.Fatal(err)
	}
	receipt := false
	for _, m := range loaded.Snapshot() {
		if m.Role == provider.RoleUser && m.LocalOnly && m.Name == surfacespec.ActionMessageName {
			receipt = true
		}
	}
	if !receipt {
		t.Fatal("action receipt did not persist")
	}
}

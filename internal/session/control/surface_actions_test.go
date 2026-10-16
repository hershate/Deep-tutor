package control

import (
	"encoding/json"
	"errors"
	"path/filepath"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/contract/surfacespec"
	"reasonix/internal/contract/tool"
	"reasonix/internal/runtime/agent"
	"reasonix/internal/state/sessionstore"
	"testing"
)

func TestSurfaceActionQueueRetryWithdrawReplay(t *testing.T) {
	const spec = `{"spec_version":1,"body":[{"kind":"editable_text","key":"draft","label":"Draft","field":{"type":"string","required":true}},{"kind":"button","label":"Continue","actionId":"continue"}]}`
	sess := sessionstore.NewSession("system")
	sess.Add(provider.Message{Role: provider.RoleUser, Content: "task"})
	sess.Add(provider.Message{Role: provider.RoleAssistant, ToolCalls: []provider.ToolCall{{ID: "call-1", Name: surfacespec.ToolName, Arguments: spec}}})
	sess.Add(provider.Message{Role: provider.RoleTool, ToolCallID: "call-1", Content: `{"surface_id":"s","nonblocking":true}`})
	path := filepath.Join(t.TempDir(), "session.jsonl")
	registry := tool.NewRegistry()
	registry.Add(agent.NewRenderSurfaceTool())
	exec := agent.New(nil, registry, sess, agent.Options{}, event.Discard)
	c := New(Options{Registry: registry, Executor: exec, Runner: exec, SessionPath: path, Sink: event.Discard})
	defer c.Close()
	if err := c.SetInboxPaused(true); err != nil {
		t.Fatal(err)
	}
	parsed, _ := surfacespec.Parse([]byte(spec))
	if err := c.PublishNativeSurface("call-1", parsed.ID()); err != nil {
		t.Fatal(err)
	}
	state, err := c.SurfaceActionState("call-1")
	if err != nil {
		t.Fatal(err)
	}
	req := SurfaceActionRequest{Action: surfacespec.Action{SurfaceID: state.Action.SurfaceID, SessionID: state.Action.SessionID, CallID: "call-1", ActionID: "continue", RequestID: "retry-1", Values: json.RawMessage(`{"draft":"  @missing /command\n"}`)}, Delivery: "queue"}
	rec, err := c.SubmitSurfaceAction(req, nil)
	if err != nil {
		t.Fatal(err)
	}
	duplicate, err := c.SubmitSurfaceAction(req, nil)
	if err != nil || duplicate.ItemID != rec.ItemID || !duplicate.Idempotent {
		t.Fatalf("retry: %+v %v", duplicate, err)
	}
	_, env, err := c.ReadInboxItem(rec.ItemID)
	if err != nil || len(env.ReferenceErrors) != 0 || env.FrozenRefBlock != "" {
		t.Fatalf("interpreted field data: %+v %v", env, err)
	}
	if _, _, _, err := applyInboxReferences(env); err != nil {
		t.Fatal(err)
	}
	state, err = c.SurfaceActionState("call-1")
	if err != nil || state.Status != "accepted" {
		t.Fatalf("state: %+v %v", state, err)
	}
	bad := req
	bad.Action.SessionID = "other"
	if _, err = c.SubmitSurfaceAction(bad, nil); !errors.Is(err, surfacespec.ErrExpired) {
		t.Fatalf("cross-session: %v", err)
	}
	if err = c.DeleteInboxItem(rec.ItemID); err != nil {
		t.Fatal(err)
	}
	state, err = c.SurfaceActionState("call-1")
	if err != nil || state.Status != "withdrawn" {
		t.Fatalf("withdraw: %+v %v", state, err)
	}
	loaded, err := sessionstore.LoadSession(path)
	if err != nil {
		t.Fatal(err)
	}
	exec.SetSession(loaded)
	state, err = c.SurfaceActionState("call-1")
	if err != nil || state.Status != "withdrawn" {
		t.Fatalf("replay: %+v %v", state, err)
	}
	if state.Active {
		t.Fatal("cold history regained active controls")
	}
	if err := c.PublishNativeSurface("call-1", parsed.ID()); err != nil {
		t.Fatal(err)
	}
	req.Action.RequestID = "new-after-withdraw"
	next, err := c.SubmitSurfaceAction(req, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err = c.CancelWithInboxItems([]string{next.ItemID}, "http"); err != nil {
		t.Fatal(err)
	}
	state, err = c.SurfaceActionState("call-1")
	if err != nil || state.Status != "withdrawn" {
		t.Fatalf("cancel: %+v %v", state, err)
	}

}

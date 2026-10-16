package control

import (
	"context"
	"encoding/json"
	"errors"
	"testing"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/contract/tool"
	"reasonix/internal/runtime/agent"
	"reasonix/internal/runtime/agent/testutil"
	"reasonix/internal/state/sessionstore"
)

type shrinkTool struct{ sess *sessionstore.Session }

func (*shrinkTool) Name() string        { return "shrink" }
func (*shrinkTool) Description() string { return "stands in for a compaction" }
func (*shrinkTool) Schema() json.RawMessage {
	return json.RawMessage(`{"type":"object","properties":{}}`)
}
func (*shrinkTool) ReadOnly() bool { return true }
func (s *shrinkTool) Execute(context.Context, json.RawMessage) (string, error) {
	msgs := s.sess.Snapshot()
	s.sess.Replace(msgs[:1])
	return "folded", nil
}

func durationTurn(t *testing.T, turns ...testutil.Turn) event.Event {
	t.Helper()
	sess := sessionstore.NewSession("system")
	for range 4 {
		sess.Add(provider.Message{Role: provider.RoleUser, Content: "earlier"})
		sess.Add(provider.Message{Role: provider.RoleAssistant, Content: "earlier answer"})
	}
	reg := tool.NewRegistry()
	reg.Add(&shrinkTool{sess: sess})
	exec := agent.New(testutil.NewMock("m", turns...), reg, sess, agent.Options{}, event.Discard)
	done := make(chan event.Event, 1)
	c := New(Options{Runner: exec, Executor: exec, Sink: event.FuncSink(func(e event.Event) {
		if e.Kind == event.TurnDone {
			done <- e
		}
	})})
	defer c.Close()
	c.Send("go")
	return waitForDone(t, done)
}

func TestTurnDoneCarriesTheRunTime(t *testing.T) {
	if got := durationTurn(t, testutil.Turn{Text: "done"}).WorkDurationMs; got <= 0 {
		t.Fatalf("TurnDone.WorkDurationMs = %d, want a measured run time", got)
	}
}

func TestTurnDoneKeepsTheRunTimeWhenTheSessionShrinksMidTurn(t *testing.T) {
	done := durationTurn(t,
		testutil.Turn{ToolCalls: []provider.ToolCall{{ID: "c1", Name: "shrink", Arguments: `{}`}}},
		testutil.Turn{Text: "done"},
	)
	if done.Err != nil || done.WorkDurationMs <= 0 {
		t.Fatalf("TurnDone err=%v WorkDurationMs=%d, want a run time after a compaction-style shrink", done.Err, done.WorkDurationMs)
	}
}

func TestTurnDoneOmitsTheRunTimeOfATurnThatFailed(t *testing.T) {
	done := durationTurn(t, testutil.ErrorTurn(errors.New("boom")))
	if done.Err == nil || done.WorkDurationMs != 0 {
		t.Fatalf("TurnDone err=%v WorkDurationMs=%d, want a failure with no run time", done.Err, done.WorkDurationMs)
	}
}

func TestTurnWorkMsSumsRunsAndSkipsStoppedTurns(t *testing.T) {
	var c guardedTurnCompletion
	c.workMs.Add(40_000)
	c.workMs.Add(25_000)
	if got := turnWorkMs(nil, false, &c); got != 65_000 {
		t.Fatalf("two runs of one turn = %d, want 65000", got)
	}
	if got := turnWorkMs(nil, true, &c); got != 0 {
		t.Fatalf("a cancelled turn reports %d, want 0 even when its answer landed first", got)
	}
	if got := turnWorkMs(errors.New("x"), false, &c); got != 0 {
		t.Fatalf("a failed turn reports %d, want 0", got)
	}
}

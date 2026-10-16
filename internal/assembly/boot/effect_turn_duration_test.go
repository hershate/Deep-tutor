package boot

import (
	"context"
	"testing"
	"time"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/eventwire"
	"reasonix/internal/contract/provider"
)

// The run time on the closing frame is the figure the session keeps, so a
// reloaded transcript says what the live one did.
func TestEffectTurnDoneCarriesTheRunTimeTheSessionKeeps(t *testing.T) {
	isolateConfigHome(t)
	dir := robustTempDir(t)
	t.Chdir(dir)
	provider.Register("boot-turn-time", func(provider.Config) (provider.Provider, error) { return thinkingProvider{}, nil })
	writeFile(t, dir, "reasonix.toml", `
default_model = "test-model"

[agent]
system_prompt = "BASE"

[codegraph]
enabled = false

[[providers]]
name = "test-model"
kind = "boot-turn-time"
model = "x"
`)
	approveWorkspace(t, dir)
	ended := make(chan event.Event, 4)
	ctrl, err := Build(context.Background(), Options{Sink: event.FuncSink(func(e event.Event) {
		if e.Kind == event.TurnDone {
			ended <- e
		}
	})})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	defer ctrl.Close()

	sentAt := time.Now().UnixMilli()
	ctrl.Send("think, then answer")
	var done event.Event
	select {
	case done = <-ended:
	case <-time.After(30 * time.Second):
		t.Fatal("the turn never ended")
	}
	if done.Err != nil {
		t.Fatalf("TurnDone.Err = %v", done.Err)
	}
	var kept int64
	for _, m := range ctrl.History() {
		if m.Role == provider.RoleAssistant && m.Content == "answer" {
			kept = m.WorkDurationMs
		}
	}
	if floor := thinkFor.Milliseconds() - 10; done.WorkDurationMs < floor {
		t.Fatalf("TurnDone.WorkDurationMs = %d, want at least %d", done.WorkDurationMs, floor)
	}
	if done.WorkDurationMs != kept {
		t.Fatalf("the live frame says %d ms but the session kept %d ms", done.WorkDurationMs, kept)
	}
	if done.WorkStartedAt < sentAt || done.WorkStartedAt > sentAt+int64(time.Minute/time.Millisecond) {
		t.Fatalf("TurnDone.WorkStartedAt = %d, want the moment the turn began, not before %d", done.WorkStartedAt, sentAt)
	}
	var landed int64
	for _, m := range ctrl.History() {
		if m.Role == provider.RoleUser && !m.HostAuthored {
			landed = m.CreatedAt
		}
	}
	if landed == 0 || landed < done.WorkStartedAt || landed-done.WorkStartedAt > 5000 {
		t.Fatalf("the user message landed at %d but the frame says the turn began at %d", landed, done.WorkStartedAt)
	}
	if got := eventwire.ToWire(done).WorkStartedAt; got != done.WorkStartedAt {
		t.Fatalf("the wire frame says %d, want %d", got, done.WorkStartedAt)
	}
	if got := eventwire.ToWire(done).WorkDurationMs; got != done.WorkDurationMs {
		t.Fatalf("the wire frame says %d ms, want %d", got, done.WorkDurationMs)
	}
}

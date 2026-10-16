package boot

import (
	"context"
	"slices"
	"sync"
	"testing"
	"time"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/session/control"
	"reasonix/internal/state/sessioninbox"
)

// A queued item the host wrote (a background job's completion, say) runs as a
// whole turn of its own. A client that never hears it start sees answer text
// arrive while it believes the session idle: the send arrow stays, the stop
// button never appears and the sidebar counts nothing running.
func TestEffectAHostQueuedTurnAnnouncesItsStartBeforeItsOutput(t *testing.T) {
	isolateConfigHome(t)
	dir := robustTempDir(t)
	t.Chdir(dir)
	kind := uniqueKind("host-turn-start")
	rec := &effectRecordingProvider{}
	provider.Register(kind, func(provider.Config) (provider.Provider, error) { return rec, nil })
	writeFile(t, dir, "reasonix.toml", "default_model = \"test-model\"\n[agent]\nsystem_prompt = \"BASE\"\n[[providers]]\nname = \"test-model\"\nkind = \""+kind+"\"\nmodel = \"x\"\n")
	approveWorkspace(t, dir)
	var mu sync.Mutex
	var kinds []event.Kind
	done := make(chan struct{})
	var once sync.Once
	sink := event.FuncSink(func(e event.Event) {
		switch e.Kind {
		case event.TurnStarted, event.TurnDone, event.Reasoning, event.Text:
			mu.Lock()
			kinds = append(kinds, e.Kind)
			mu.Unlock()
		}
		if e.Kind == event.TurnDone {
			once.Do(func() { close(done) })
		}
	})
	ctrl, err := Build(context.Background(), Options{Sink: sink})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	defer ctrl.Close()
	ctrl.EnsureSessionPath()
	if _, err := ctrl.TryEnqueueFollowup(control.InboxRequest{Origin: sessioninbox.OriginHost, Display: "job finished", Raw: "job finished", Submit: "job finished"}); err != nil {
		t.Fatal(err)
	}
	select {
	case <-done:
	case <-time.After(30 * time.Second):
		t.Fatal("the host-queued turn never finished")
	}
	mu.Lock()
	defer mu.Unlock()
	start := slices.Index(kinds, event.TurnStarted)
	out := slices.IndexFunc(kinds, func(k event.Kind) bool { return k == event.Text || k == event.Reasoning })
	end := slices.Index(kinds, event.TurnDone)
	if start < 0 || out < 0 || !(start < out && out < end) {
		t.Fatalf("event order %v: want a turn start before the output and the turn's end", kinds)
	}
	if len(slices.DeleteFunc(slices.Clone(kinds), func(k event.Kind) bool { return k != event.TurnStarted })) != 1 {
		t.Fatalf("event order %v: want exactly one start", kinds)
	}
}

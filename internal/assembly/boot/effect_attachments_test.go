package boot

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
)

type effectCaptureSink struct {
	mu     sync.Mutex
	events []event.Event
	done   chan struct{}
}

func (s *effectCaptureSink) Emit(e event.Event) {
	s.mu.Lock()
	s.events = append(s.events, e)
	s.mu.Unlock()
	if e.Kind == event.TurnDone {
		close(s.done)
	}
}

// TestEffectTurnStartedNamesTheStoredAttachment pins the structured field at
// the frontend sink of the real assembly, while the model keeps reading the
// @path text exactly as before.
func TestEffectTurnStartedNamesTheStoredAttachment(t *testing.T) {
	isolateConfigHome(t)
	dir := robustTempDir(t)
	t.Chdir(dir)
	rel := ".reasonix/attachments/clipboard-20260101-000000.000000-000001.png"
	if err := os.MkdirAll(filepath.Join(dir, filepath.Dir(rel)), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, rel), []byte("png-bytes"), 0o644); err != nil {
		t.Fatal(err)
	}
	rec := &effectRecordingProvider{}
	provider.Register("boot-effect-attach", func(provider.Config) (provider.Provider, error) { return rec, nil })
	writeFile(t, dir, "reasonix.toml", `
default_model = "test-model"

[agent]
system_prompt = "BASE"

[[providers]]
name = "test-model"
kind = "boot-effect-attach"
model = "x"
`)
	approveWorkspace(t, dir)

	sink := &effectCaptureSink{done: make(chan struct{})}
	ctrl, err := Build(context.Background(), Options{Sink: sink})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	defer ctrl.Close()
	ctrl.Submit("describe @" + rel + " and @.reasonix/attachments/clipboard-missing.png")
	select {
	case <-sink.done:
	case <-time.After(30 * time.Second):
		t.Fatal("turn did not finish")
	}

	var got []event.Attachment
	sink.mu.Lock()
	for _, e := range sink.events {
		if e.Kind == event.TurnStarted && e.AuthoredTurn != nil {
			got = e.Attachments
		}
	}
	sink.mu.Unlock()
	if len(got) != 1 || got[0].Path != rel {
		t.Fatalf("turn_started attachments = %+v", got)
	}
	if !strings.Contains(userMessages(agentRequests(rec.requests())[0]), "@"+rel) {
		t.Fatal("the model no longer reads the @path text")
	}
}

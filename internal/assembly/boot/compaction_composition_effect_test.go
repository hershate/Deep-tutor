package boot

import (
	"context"
	"encoding/json"
	"strings"
	"sync"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/runtime/agent"
	"reasonix/internal/state/sessionstore"
)

// A session whose prompt is over the compaction threshold and has nothing left
// to fold used to answer /compact with only "nothing has closed". Through the
// real Build, the frontend sink must also receive what the tokens are.
func TestEffectCompactOverThresholdReportsWhatHoldsTheContext(t *testing.T) {
	t.Parallel()
	home := statedBootHome(t)
	dir := testenv.TempDir(t)
	rec := &effectRecordingProvider{}
	kind := "boot-composition-" + t.Name()
	provider.Register(kind, func(provider.Config) (provider.Provider, error) { return rec, nil })
	writeFile(t, dir, "reasonix.toml", `
default_model = "test-model"

[agent]
system_prompt = "BASE"

[[providers]]
name = "test-model"
kind = "`+kind+`"
model = "x"
context_window = 20000
`)
	approveWorkspaceAt(t, home, dir)

	var mu sync.Mutex
	var notices []event.Event
	sink := event.FuncSink(func(e event.Event) {
		if e.Kind == event.Notice {
			mu.Lock()
			notices = append(notices, e)
			mu.Unlock()
		}
	})
	ctrl, err := Build(context.Background(), Options{Home: home, WorkspaceRoot: dir, Sink: sink})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	defer ctrl.Close()
	ctrl.SetSessionPath(sessionstore.NewSessionPath(ctrl.SessionDir(), ctrl.Label()))

	big := func(tokens int) {
		t.Helper()
		if err := ctrl.Run(context.Background(), "go "+strings.Repeat("log line ", tokens*4/9)); err != nil {
			t.Fatalf("turn: %v", err)
		}
	}
	// The first fold leaves a checkpoint; the next turn is then one recent turn
	// larger than the threshold, which the fold keeps whole.
	big(12000)
	if _, err := ctrl.Compact(context.Background(), agent.CompactRequest{}); err != nil {
		t.Fatalf("first compact: %v", err)
	}
	mu.Lock()
	notices = nil
	mu.Unlock()
	big(12000)
	if _, err := ctrl.Compact(context.Background(), agent.CompactRequest{}); err != nil && !agent.IsCompactionDeclined(err) {
		t.Logf("compact: %v", err)
	}

	mu.Lock()
	defer mu.Unlock()
	for _, n := range notices {
		if n.Code != event.NoticeCodeContextComposition {
			continue
		}
		c, ok := decodeCompositionDetail(n.Detail)
		if !ok || c.Total < c.At || c.Parts[0].Name != "user" {
			t.Fatalf("composition = %+v, %v; want a prompt over its threshold led by the user's message", c, ok)
		}
		if !strings.Contains(n.Text, "compaction threshold") {
			t.Fatalf("notice text %q is not the English fallback sentence", n.Text)
		}
		return
	}
	t.Fatalf("the sink never received the composition among %d notices", len(notices))
}

func decodeCompositionDetail(detail string) (event.ContextComposition, bool) {
	var c event.ContextComposition
	err := json.Unmarshal([]byte(detail), &c)
	return c, err == nil && c.Total > 0 && len(c.Parts) > 0
}

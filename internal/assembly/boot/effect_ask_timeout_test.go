package boot

import (
	"context"
	"encoding/json"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
)

// askingProvider asks one question, then ends once it has seen the result.
type askingProvider struct {
	mu   sync.Mutex
	reqs []provider.Request
}

func (p *askingProvider) Name() string { return "boot-asking" }

func (p *askingProvider) Stream(_ context.Context, req provider.Request) (<-chan provider.Chunk, error) {
	p.mu.Lock()
	p.reqs = append(p.reqs, req)
	asked := len(effectToolResults(req)) > 0
	p.mu.Unlock()
	ch := make(chan provider.Chunk, 3)
	if asked {
		ch <- provider.Chunk{Type: provider.ChunkText, Text: "carrying on under an assumption"}
	} else {
		args, _ := json.Marshal(map[string]any{"questions": []map[string]any{{
			"header": "Plan", "question": "Which migration?", "reason": "user_decision",
			"options": []map[string]string{{"label": "Batched"}, {"label": "At once"}},
		}}})
		ch <- provider.Chunk{Type: provider.ChunkToolCall, ToolCall: &provider.ToolCall{ID: "ask-1", Name: "ask", Arguments: string(args)}}
	}
	ch <- provider.Chunk{Type: provider.ChunkDone}
	close(ch)
	return ch, nil
}

type askSinkRecorder struct {
	mu     sync.Mutex
	events []event.Event
}

func (s *askSinkRecorder) Emit(ev event.Event) {
	s.mu.Lock()
	s.events = append(s.events, ev)
	s.mu.Unlock()
}

func (s *askSinkRecorder) has(pred func(event.Event) bool) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return slices.ContainsFunc(s.events, pred)
}

// A question nobody answers within the user's ask timeout is skipped, through
// the real assembly: the frontend sees the question and then its timed-out
// receipt, and the model's next request carries a result that says it was a
// timeout rather than an answer.
func TestEffectUnansweredAskIsSkippedAsATimeoutThroughRealBuild(t *testing.T) {
	isolateConfigHome(t)
	dir := robustTempDir(t)
	t.Chdir(dir)
	rec := &askingProvider{}
	provider.Register("boot-asking", func(provider.Config) (provider.Provider, error) { return rec, nil })
	writeUserConfig(t, "[agent]\nask_timeout_seconds = 10\n")
	writeFile(t, dir, "reasonix.toml", `
default_model = "test-model"

[agent]
system_prompt = "BASE"

[codegraph]
enabled = false

[[providers]]
name = "test-model"
kind = "boot-asking"
model = "x"
`)
	approveWorkspace(t, dir)

	sink := &askSinkRecorder{}
	ctrl, err := Build(context.Background(), Options{Sink: sink})
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	defer ctrl.Close()
	ctrl.EnableInteractiveApproval()

	start := time.Now()
	if err := ctrl.Run(context.Background(), "migrate it"); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if time.Since(start) < 9*time.Second {
		t.Fatalf("the question was skipped after %v, before the 10s the user set", time.Since(start))
	}
	if !sink.has(func(ev event.Event) bool { return ev.Kind == event.AskRequest }) {
		t.Fatal("the question never reached the frontend")
	}
	if !sink.has(func(ev event.Event) bool {
		return ev.DecisionReceipt != nil && ev.DecisionReceipt.Kind == "ask" && ev.DecisionReceipt.Outcome == "timed_out"
	}) {
		t.Fatal("the frontend was never told the question timed out")
	}
	rec.mu.Lock()
	last := rec.reqs[len(rec.reqs)-1]
	rec.mu.Unlock()
	results := effectToolResults(last)
	if len(results) != 1 {
		t.Fatalf("model saw %d tool results, want the ask's", len(results))
	}
	for _, want := range []string{"skipped:", "10 seconds", "timeout, not an answer", "no option was chosen"} {
		if !strings.Contains(results[0], want) {
			t.Fatalf("the model's result lacks %q:\n%s", want, results[0])
		}
	}
}

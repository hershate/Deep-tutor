package agent

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/contract/tool"
	"reasonix/internal/state/sessionstore"
)

func compositionAgent(t *testing.T) *Agent {
	t.Helper()
	sess := sessionstore.NewSession("system prompt")
	a := New(nil, nil, sess, Options{ContextWindow: 128000}, nil)
	sess.Add(provider.Message{Role: provider.RoleUser, Content: "first request", CreatedAt: 1})
	sess.Add(provider.Message{Role: provider.RoleAssistant, Content: "ok", ToolCalls: []provider.ToolCall{{ID: "a", Name: "read_file"}}})
	sess.Add(provider.Message{Role: provider.RoleTool, ToolCallID: "a", Content: strings.Repeat("x", 4000)})
	sess.Add(provider.Message{Role: provider.RoleUser, Content: "second request", CreatedAt: 2})
	sess.Add(provider.Message{Role: provider.RoleAssistant, Content: "again", ToolCalls: []provider.ToolCall{{ID: "b", Name: "grep"}}})
	sess.Add(provider.Message{Role: provider.RoleTool, ToolCallID: "b", Content: strings.Repeat("y", 40000)})
	a.window().activeTurnCreatedAt.Store(2)
	return a
}

// A prompt over its threshold is only fixable once it says what holds it: the
// turn still running cannot be folded, so its share is reported on its own, and
// the biggest single messages are named with the tool that produced them.
func TestContextCompositionSplitsTheRunningTurnAndNamesTheLargestResults(t *testing.T) {
	c := compositionAgent(t).window().contextComposition()
	if c.Total <= 0 || c.At <= 0 {
		t.Fatalf("composition must carry the total and the threshold: %+v", c)
	}
	if c.Active <= 0 || c.Active >= c.Total {
		t.Fatalf("active = %d of %d, want the running turn counted apart from the rest", c.Active, c.Total)
	}
	if len(c.Parts) == 0 || c.Parts[0].Name != "tool_output" {
		t.Fatalf("parts = %+v, want tool output first: it is the largest class", c.Parts)
	}
	for i := 1; i < len(c.Parts); i++ {
		if c.Parts[i].Tokens > c.Parts[i-1].Tokens {
			t.Fatalf("parts must be ordered largest first: %+v", c.Parts)
		}
	}
	if len(c.Largest) == 0 || c.Largest[0].Tool != "grep" || !c.Largest[0].Active {
		t.Fatalf("largest = %+v, want the 40KB grep result of the running turn first", c.Largest)
	}
	if len(c.Largest) > 3 {
		t.Fatalf("largest = %d items, want at most three", len(c.Largest))
	}
}

func TestContextCompositionPayloadRoundTrips(t *testing.T) {
	c := compositionAgent(t).window().contextComposition()
	got, ok := decodeComposition(c.Encode())
	if !ok || got.Total != c.Total || len(got.Parts) != len(c.Parts) {
		t.Fatalf("round trip = %+v, %v", got, ok)
	}
}

// /compact on a prompt that is over its threshold but has nothing foldable used
// to answer only "nothing has closed"; it also says what the tokens are.
func TestManualCompactOverThresholdSaysWhatHoldsTheContext(t *testing.T) {
	sess := sessionstore.NewSession("sys")
	sess.Add(provider.Message{Role: provider.RoleUser, Content: strings.Repeat("pasted log line\n", 4500), CreatedAt: 1})
	var notices []event.Event
	sink := event.FuncSink(func(e event.Event) {
		if e.Kind == event.Notice {
			notices = append(notices, e)
		}
	})
	a := New(&fakeProvider{reply: "- summary"}, tool.NewRegistry(), sess, Options{ContextWindow: 20000}, sink)
	a.window().activeTurnCreatedAt.Store(1)
	if a.window().estimatedVisibleRequestTokens(a.window().modelVisibleMessages()) < a.window().compactTrigger() {
		t.Fatal("fixture is under the threshold; it proves nothing")
	}
	_, _ = a.CompactNow(context.Background(), CompactRequest{})
	for _, n := range notices {
		if n.Code != event.NoticeCodeContextComposition {
			continue
		}
		c, ok := decodeComposition(n.Detail)
		if !ok || c.Parts[0].Name != "user" || c.Active <= 0 {
			t.Fatalf("composition = %+v, %v; want the running turn's user message to lead", c, ok)
		}
		return
	}
	t.Fatalf("no composition notice among %d notices", len(notices))
}

func decodeComposition(detail string) (event.ContextComposition, bool) {
	var c event.ContextComposition
	err := json.Unmarshal([]byte(detail), &c)
	return c, err == nil && c.Total > 0 && len(c.Parts) > 0
}

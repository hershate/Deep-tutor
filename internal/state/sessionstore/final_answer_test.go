package sessionstore

import (
	"slices"
	"testing"

	"reasonix/internal/contract/provider"
)

func TestTurnWorkMs(t *testing.T) {
	user := func(s string) provider.Message { return provider.Message{Role: provider.RoleUser, Content: s} }
	answer := func(ms int64) provider.Message {
		return provider.Message{Role: provider.RoleAssistant, Content: "a", WorkDurationMs: ms}
	}
	step := provider.Message{Role: provider.RoleAssistant, ToolCalls: []provider.ToolCall{{ID: "c"}}, WorkDurationMs: 3000}
	msgs := []provider.Message{
		user("first"), step, answer(9000), // 0 1 2: one run
		user("second"), answer(4000), // 3 4
		user("third"), answer(5000), // 5 6
		user(MidTurnSteerPrefix + " hold on"),
		answer(8000),                                            // 7 8: the steer stays inside the run, so its answers are one run
		user(SyntheticUserPrefixes[0] + " go on"), answer(2000), // 9 10: a continuation run of the same turn
		user("fourth"), // 11: no answer
	}
	want := []int64{0, 0, 9000, 0, 4000, 0, 0, 0, 0, 0, 10000, 0}
	if got := TurnWorkMs(msgs); !slices.Equal(got, want) {
		t.Fatalf("TurnWorkMs = %v, want %v", got, want)
	}
}

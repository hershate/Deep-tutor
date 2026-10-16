package control

import (
	"strings"
	"testing"

	"reasonix/internal/contract/event"
	"reasonix/internal/safety/permission"
)

func refusedResult(name, args, code string) event.Event {
	return event.Event{Kind: event.ToolResult, Tool: event.Tool{Name: name, Args: args, Err: "refused", RefusalCode: code}}
}

func TestRefusedCallsRideTheNextTurnTailOnce(t *testing.T) {
	c := New(Options{})
	c.sink.Emit(refusedResult("bash", `{"command":"git push origin main"}`, permission.RefusalDenyRule))
	c.sink.Emit(refusedResult("write_file", `{"path":"a.txt"}`, permission.RefusalUnattended))
	c.sink.Emit(event.Event{Kind: event.ToolResult, Tool: event.Tool{Name: "bash", Args: `{"command":"ls"}`, Err: "exit 2"}})
	c.sink.Emit(refusedResult("bash", `{"command":"make deploy"}`, permission.RefusalParked))
	c.sink.Emit(refusedResult("bash", `{"command":"go test ./..."}`, permission.RefusalUntrustedFolder))

	synthetic := c.ComposeSynthetic("keep going")
	if strings.Contains(synthetic, "<refused-calls>") {
		t.Fatalf("a synthetic turn consumed what the user's next message is owed:\n%s", synthetic)
	}
	got := c.Compose("next")
	for _, want := range []string{"<refused-calls>", "git push origin main", permission.RefusalDenyRule, "a.txt", "do not retry them"} {
		if !strings.Contains(got, want) {
			t.Errorf("composed turn lacks %q:\n%s", want, got)
		}
	}
	if strings.Contains(got, `"ls"`) {
		t.Errorf("a failure that was not a host refusal was reported as one:\n%s", got)
	}
	for _, retryable := range []string{"make deploy", "go test ./..."} {
		if strings.Contains(got, retryable) {
			t.Errorf("a refusal that is retryable after approval or trust was told as final: %q\n%s", retryable, got)
		}
	}
	if !strings.HasSuffix(got, "next") && !strings.Contains(got, "next") {
		t.Fatalf("user text lost: %s", got)
	}
	c.settleTurnProjections()
	if again := c.Compose("later"); strings.Contains(again, "<refused-calls>") {
		t.Errorf("a settled refusal was projected again:\n%s", again)
	}
}

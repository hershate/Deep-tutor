package control

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/tool"
	"reasonix/internal/runtime/agent"
	"reasonix/internal/state/memory"
	"reasonix/internal/state/sessionstore"
)

// When this turn already carries the fact a memory write just produced,
// automatic recall is suppressed. That is a real recall decision, so it has to
// reach the same audit channel as a recall that returned hits. The note comes
// from a real producer (SaveMemory), not from the manager's internals.
func TestComposeAuditsTheSuppressionThatANewFactCauses(t *testing.T) {
	root := testenv.TempDir(t)
	userDir := filepath.Join(root, "user")
	cwd := filepath.Join(root, "project")
	if err := os.MkdirAll(cwd, 0o755); err != nil {
		t.Fatal(err)
	}
	ag := agent.New(nil, tool.NewRegistry(), sessionstore.NewSession("sys"), agent.Options{}, event.Discard)
	sink := &recallAuditSink{Sink: event.Discard}
	c := New(Options{
		Runner:   ag,
		Executor: ag,
		Sink:     sink,
		Memory:   memory.Load(memory.Options{CWD: cwd, UserDir: userDir}),
	})

	if _, err := c.SaveMemory(memory.Memory{
		Name:        "alpha",
		Description: "the fact that was just written",
		Type:        memory.TypeReference,
		Scope:       memory.FactScopeProject,
		Body:        "the fact that was just written",
	}); err != nil {
		t.Fatalf("SaveMemory: %v", err)
	}

	if tail := c.Compose("what is the canonical fast check command"); !strings.Contains(tail, "alpha") {
		t.Fatalf("the composed turn did not carry the saved fact, so recall was not suppressed:\n%s", tail)
	}
	if len(sink.audits) != 1 {
		t.Fatalf("audits = %d, want exactly one audit for the suppressed recall", len(sink.audits))
	}
	audit := sink.audits[0]
	if !strings.Contains(audit.Suppressed, "memory update") {
		t.Fatalf("the audit does not say why recall was suppressed: %+v", audit)
	}
	if len(audit.Hits) != 0 {
		t.Fatalf("a suppressed recall reported hits: %+v", audit.Hits)
	}
	if strings.Contains(audit.Suppressed, "the fact that was just written") {
		t.Fatalf("the audit carries fact content: %q", audit.Suppressed)
	}
}

package delegation

import (
	"encoding/json"
	"strings"
	"testing"

	"reasonix/internal/contract/tool"
)

// Several sub-agents run side by side and a row that says only "task" does not
// tell them apart. The kernel names each one from the task's own description:
// the model wrote it to say what is running.
func TestSubAgentProfileCarriesALabelFromTheTaskDescription(t *testing.T) {
	task := NewTaskTool(&mockProvider{name: "sub"}, nil, tool.NewRegistry(), 20, 0, 0, 0, 0.0, "", "sys", nil, 0, "", "", nil)
	label := func(args map[string]string) string {
		raw, _ := json.Marshal(args)
		return task.ResolveProfile(raw).Label
	}
	cases := []struct {
		name string
		args map[string]string
		want string
	}{
		{"the description", map[string]string{"description": "Audit the retry backoff", "prompt": "Read retry.go and report"}, "Audit the retry backoff"},
		{"only its first line", map[string]string{"description": "Audit the retry backoff\nthen the circuit breaker", "prompt": "x"}, "Audit the retry backoff"},
		{"trimmed", map[string]string{"description": "  \n  Check the docs build  \n", "prompt": "x"}, "Check the docs build"},
		{"the prompt's first line when nothing was described", map[string]string{"prompt": "Find every caller of Resolve.\nReport file and line."}, "Find every caller of Resolve."},
		{"nothing to say", map[string]string{"description": "  ", "prompt": " "}, ""},
	}
	for _, c := range cases {
		if got := label(c.args); got != c.want {
			t.Errorf("%s: label = %q, want %q", c.name, got, c.want)
		}
	}
	long := strings.Repeat("investigate the flaky test ", 20)
	if got := label(map[string]string{"description": long}); len([]rune(got)) > 120 || !strings.HasPrefix(long, strings.TrimSuffix(got, "…")) {
		t.Errorf("a long description must be cut to a bounded prefix, got %d runes: %q", len([]rune(got)), got)
	}
}

func TestABatchOfSubAgentsHasNoSingleLabel(t *testing.T) {
	task := NewTaskTool(&mockProvider{name: "sub"}, nil, tool.NewRegistry(), 20, 0, 0, 0, 0.0, "", "sys", nil, 0, "", "", nil)
	raw := json.RawMessage(`{"tasks":[{"prompt":"a","description":"first"},{"prompt":"b","description":"second"}]}`)
	if p := NewFleetTool(task).ResolveProfile(raw); p.Label != "" || p.Count != 2 {
		t.Fatalf("fleet profile = %+v, want a count and no label", p)
	}
	if p := NewParallelTasksTool(task, tool.NewRegistry()).ResolveProfile(raw); p.Label != "" || p.Count != 2 {
		t.Fatalf("parallel profile = %+v, want a count and no label", p)
	}
}

package delegation

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/event"
	"reasonix/internal/contract/provider"
	"reasonix/internal/contract/tool"
	"reasonix/internal/runtime/agent"
	"reasonix/internal/state/sessionstore"
	"reasonix/internal/tools/builtin"
)

func TestLegacyTaskToolFileAccessPolicy(t *testing.T) {
	for _, policy := range []string{"omitted", "nil", "configured"} {
		t.Run(policy, func(t *testing.T) {
			root := testenv.TempDir(t)
			path := filepath.Join(root, "private", "note.txt")
			if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
				t.Fatal(err)
			}
			const original = "INTERNAL_NOTE=legacy-canary-do-not-disclose\n"
			if err := os.WriteFile(path, []byte(original), 0o600); err != nil {
				t.Fatal(err)
			}
			ws := builtin.Workspace{Dir: root, ForbidReadRoots: []string{filepath.Dir(path)}}
			reg := tool.NewRegistry()
			reg.Add(ws.Tools("edit_file")[0])
			prov := &scriptedProvider{name: "legacy", turns: [][]provider.Chunk{
				{toolCallChunk("legacy-edit", "edit_file", `{"path":"private/note.txt","old_string":"INTERNAL_NOTE=missing-value","new_string":"changed"}`), {Type: provider.ChunkDone}},
				{{Type: provider.ChunkText, Text: "blocked"}, {Type: provider.ChunkDone}},
			}}
			var checks []tool.TargetAccessCheck
			wantCode := "workspace.target_access_unconfigured"
			if policy == "nil" {
				checks = []tool.TargetAccessCheck{nil}
			}
			if policy == "configured" {
				checks = []tool.TargetAccessCheck{ws.TargetAccessCheck()}
				wantCode = builtin.CodeReadForbidden
			}
			task := NewTaskTool(prov, nil, reg, 5, 0, 0, 0, 0, "", "sys", nil, 0, "", "", nil, checks...)
			opts := task.subagentOptions(context.Background(), 5, nil, 0, 1, "legacy", nil)
			var result event.Tool
			preview := ""
			sink := event.FuncSink(func(e event.Event) {
				if e.Kind == event.ToolDispatch {
					preview += e.Tool.Diff
				}
				if e.Kind == event.ToolResult {
					result = e.Tool
				}
			})
			child := agent.New(prov, reg, sessionstore.NewSession("sys"), opts, sink)
			if err := child.Run(context.Background(), "perform the requested edit"); err != nil {
				t.Fatal(err)
			}
			if result.Executed || result.RefusalCode != wantCode {
				t.Errorf("legacy entry used the wrong access policy: %+v", result)
			}
			if strings.Contains(result.Output+result.Err+preview, "legacy-canary-do-not-disclose") {
				t.Error("legacy entry disclosed forbidden contents")
			}
			for _, req := range prov.requests {
				for _, msg := range req.Messages {
					if msg.Role == provider.RoleTool && strings.Contains(msg.Content, "legacy-canary-do-not-disclose") {
						t.Error("model received forbidden contents")
					}
				}
			}
			if got, err := os.ReadFile(path); err != nil || string(got) != original {
				t.Fatalf("protected file changed: %q error=%v", got, err)
			}
		})
	}
}

func TestLegacyTaskToolPreservesConfiguredPolicy(t *testing.T) {
	calls := 0
	check := func(context.Context, tool.Tool, json.RawMessage) error { calls++; return nil }
	task := NewTaskTool(nil, nil, tool.NewRegistry(), 5, 0, 0, 0, 0, "", "sys", nil, 0, "", "", nil, check)
	opts := task.subagentOptions(context.Background(), 5, nil, 0, 1, "legacy", nil)
	if err := opts.CheckTargetAccess(context.Background(), nil, nil); err != nil || calls != 1 {
		t.Fatalf("configured policy was replaced: calls=%d error=%v", calls, err)
	}
}

func TestLegacyTaskToolMissingPolicyKeepsNonFileCalls(t *testing.T) {
	task := NewTaskTool(nil, nil, tool.NewRegistry(), 5, 0, 0, 0, 0, "", "sys", nil, 0, "", "", nil)
	opts := task.subagentOptions(context.Background(), 5, nil, 0, 1, "legacy", nil)
	if err := opts.CheckTargetAccess(context.Background(), fakeTool{name: "echo", readOnly: true}, nil); err != nil {
		t.Fatalf("non-file call refused: %v", err)
	}
}

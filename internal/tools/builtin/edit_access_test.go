package builtin

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/contract/tool"
)

func TestEditToolsCheckReadAccessWithoutAgent(t *testing.T) {
	for _, name := range []string{"edit_file", "multi_edit", "notebook_edit", "delete_range", "delete_symbol"} {
		t.Run(name, func(t *testing.T) {
			root := testenv.TempDir(t)
			path := filepath.Join(root, "private.go")
			const original = "package main\n// PRIVATE_CANARY\nfunc RemoveMe() {}\n"
			if err := os.WriteFile(path, []byte(original), 0o600); err != nil {
				t.Fatal(err)
			}
			probe := &writeSourceProbeOverlay{}
			ws := Workspace{Dir: root, ForbidReadRoots: []string{path}, FileOverlay: probe}
			target := ws.Tools(name)[0]
			args := argsJSON(t, map[string]any{"path": path, "old_string": "missing", "new_string": "changed", "edits": []map[string]string{{"old_string": "missing", "new_string": "changed"}}, "cell_number": 0, "new_source": "changed", "start_anchor": "package main", "end_anchor": "func RemoveMe() {}", "name": "RemoveMe"})
			for _, operation := range []string{"preview", "execute"} {
				t.Run(operation, func(t *testing.T) {
					var err error
					if operation == "preview" {
						_, err = target.(tool.Previewer).Preview(context.Background(), args)
					} else {
						_, err = target.Execute(context.Background(), args)
					}
					var refusal tool.Refusal
					if !errors.As(err, &refusal) || refusal.Code != CodeReadForbidden {
						t.Fatalf("direct %s bypassed read policy: %v", operation, err)
					}
					if probe.reads != 0 || probe.writes != 0 {
						t.Fatalf("denied operation reached the editor buffer: %+v", probe)
					}
					if got, err := os.ReadFile(path); err != nil || string(got) != original {
						t.Fatalf("protected file changed: %q error=%v", got, err)
					}
				})
			}
		})
	}
}

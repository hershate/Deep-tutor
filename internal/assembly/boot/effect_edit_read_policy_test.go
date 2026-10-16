package boot

import (
	"encoding/json"
	"maps"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/contract/provider"
	"reasonix/internal/session/control"
)

func TestEffectNotebookAndDeletionReadPolicy(t *testing.T) {
	cases := []struct {
		name, file, original, marker string
		params                       map[string]any
	}{
		{"notebook_edit", "note.ipynb", `{"cells":[{"cell_type":"code","source":["PRIVATE_CANARY"],"metadata":{},"outputs":[],"execution_count":null}],"metadata":{},"nbformat":4,"nbformat_minor":5}`, "UPDATED_CELL", map[string]any{"cell_number": 0, "new_source": "UPDATED_CELL"}},
		{"delete_range", "note.txt", "keep\nBEGIN\nPRIVATE_CANARY\nEND\nkeep\n", "keep", map[string]any{"start_anchor": "BEGIN", "end_anchor": "END"}},
		{"delete_symbol", "note.go", "package main\nfunc Keep() {}\nfunc RemoveMe() { println(\"PRIVATE_CANARY\") }\n", "func Keep", map[string]any{"name": "RemoveMe"}},
	}
	for _, tc := range cases {
		for _, forbidden := range []bool{false, true} {
			label := "allowed"
			if forbidden {
				label = "forbidden"
			}
			t.Run(tc.name+"/"+label, func(t *testing.T) {
				path := filepath.Join("private", tc.file)
				params := map[string]any{"path": path}
				maps.Copy(params, tc.params)
				args, err := json.Marshal(params)
				if err != nil {
					t.Fatal(err)
				}
				readArgs, _ := json.Marshal(map[string]string{"path": path})
				cfg := "[sandbox]\nbash = \"off\"\n"
				if forbidden {
					cfg += "forbid_read = [\"private\"]\n"
				}
				run := buildPostureRun(t, cfg, provider.ToolCall{ID: "source", Name: "read_file", Arguments: string(readArgs)}, provider.ToolCall{ID: "change", Name: tc.name, Arguments: string(args)})
				absolute := filepath.Join(run.dir, path)
				if err := os.MkdirAll(filepath.Dir(absolute), 0755); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(absolute, []byte(tc.original), 0600); err != nil {
					t.Fatal(err)
				}
				run.runIn(t, control.ToolApprovalYolo)
				result := run.results["change"]
				got, err := os.ReadFile(absolute)
				if err != nil {
					t.Fatal(err)
				}
				if forbidden {
					if result.Executed || result.RefusalCode != "workspace.read_forbidden" || string(got) != tc.original {
						t.Fatalf("denied operation changed file or executed: %+v file=%q", result, got)
					}
					if strings.Contains(run.modelSaw(t, "change"), "PRIVATE_CANARY") {
						t.Fatal("model received forbidden contents")
					}
					for _, msg := range run.ctrl.History() {
						for _, call := range msg.ToolCalls {
							if call.ID == "change" && strings.Contains(call.Diff, "PRIVATE_CANARY") {
								t.Fatal("denied preview disclosed contents")
							}
						}
					}
				} else if !result.Executed || result.Err != "" || strings.Contains(string(got), "PRIVATE_CANARY") || !strings.Contains(string(got), tc.marker) {
					t.Fatalf("allowed operation failed: %+v file=%q", result, got)
				}
			})
		}
	}
}

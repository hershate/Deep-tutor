package serve

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/base/testenv"
	"reasonix/internal/state/sessionstore"
	"reasonix/internal/state/store"
)

// A conversation whose log still lives in the 1.x store is read here and never
// written, so the tree says which store each row came from rather than letting
// it look like one of this build's own.
func TestWorkspacePageMarksRowsKeptInThe1xStore(t *testing.T) {
	home := testenv.TempDir(t)
	t.Setenv("REASONIX_HOME", home)
	t.Setenv("REASONIX_STATE_HOME", home)
	root := testenv.TempDir(t)
	dir := SessionDirFor(root)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}

	native := filepath.Join(dir, "20260925-120000-native.jsonl")
	if err := sessionstore.NewSession("sys").Save(native); err != nil {
		t.Fatal(err)
	}

	// A 1.x session: the .jsonl checkpoint is empty and the schema-2 event log
	// beside it is what this build reads but never writes.
	foreign := filepath.Join(dir, "20260925-130000-old.jsonl")
	if err := os.WriteFile(foreign, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	lines := []string{
		`{"schema_version":2,"at":"2026-09-25T13:00:00Z","type":"log","generation":1}`,
		`{"schema_version":2,"at":"2026-09-25T13:00:00Z","type":"writer","writer":"w1","pid":42}`,
		`{"schema_version":2,"at":"2026-09-25T13:00:00Z","type":"message","id":"m1","parent":"","head":"main","msgs":[{"role":"system","content":"sys"}]}`,
	}
	if err := os.WriteFile(store.SessionEventLog(foreign), []byte(strings.Join(lines, "\n")+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if !sessionstore.IsForeignSessionLog(foreign) {
		t.Fatal("fixture is not read as a 1.x log")
	}

	page := NewHub(HubOptions{}).workspacePage(root, filepath.Base(root), nil, treeWindow{}, 0)
	legacy := map[string]bool{}
	for _, row := range page.Sessions {
		legacy[filepath.Base(row.Path)] = row.Legacy
	}
	if !legacy["20260925-130000-old.jsonl"] {
		t.Errorf("a 1.x row is not marked: %v", legacy)
	}
	if legacy["20260925-120000-native.jsonl"] {
		t.Errorf("this build's own row is marked: %v", legacy)
	}
}

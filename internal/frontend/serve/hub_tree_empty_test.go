package serve

import (
	"encoding/json"
	"net/http/httptest"
	"os"
	"testing"

	"reasonix/internal/base/testenv"
)

// The frontend iterates every workspace's sessions, so a folder with no
// conversation yet must send an empty list: null blanks the whole window.
func TestTreeSendsAnEmptyListForAFolderWithoutSessions(t *testing.T) {
	for _, tc := range []struct {
		name    string
		makeDir bool
	}{
		{"no session folder", false},
		{"empty session folder", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("REASONIX_HOME", testenv.TempDir(t))
			root := testenv.TempDir(t)
			if tc.makeDir {
				if err := os.MkdirAll(SessionDirFor(root), 0o755); err != nil {
					t.Fatal(err)
				}
			}
			h := NewHub(HubOptions{})
			hubRuntime(t, h, root)

			srv := httptest.NewServer(operatorHandler(h))
			defer srv.Close()
			tree := hubGet[[]map[string]json.RawMessage](t, srv, "/tree")
			if len(tree) != 1 {
				t.Fatalf("/tree = %d workspaces, want the one", len(tree))
			}
			if got := string(tree[0]["sessions"]); got != "[]" {
				t.Fatalf("sessions = %s, want []", got)
			}
		})
	}
}

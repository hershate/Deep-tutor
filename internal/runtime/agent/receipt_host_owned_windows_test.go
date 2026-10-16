//go:build windows

package agent

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// A junction is neither a symlink nor a regular directory to Go, and the store
// reached through one is not the host's.
func TestJunctionOnTheWayToTheStoreStaysListed(t *testing.T) {
	f := newReceiptFixture(t)
	real := f.write(t, filepath.Join("src", "api.py"))
	link := filepath.Join(f.root, ".reasonix", "tasks", "x")
	if err := os.MkdirAll(filepath.Dir(link), 0o755); err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command("cmd", "/c", "mklink", "/J", link, filepath.Dir(real)).CombinedOutput(); err != nil {
		t.Fatalf("mklink /J: %v: %s", err, out)
	}
	written := filepath.Join(link, "api.py")
	f.record(written)
	requireListed(t, f, written)
}

// The ledger folds case; a host-owned verdict spelled differently from the
// path it judged still has to meet it there.
func TestHostOwnedVerdictSurvivesTheLedgersCaseFolding(t *testing.T) {
	f := newReceiptFixture(t)
	f.record(f.write(t, filepath.Join(".REASONIX", "Tasks", "Step-5", "Snapshot.json")))
	if rep := f.report(); rep.Mutations != 0 || len(rep.Changes) != 0 {
		t.Fatalf("mutations = %d, changes = %q, want none", rep.Mutations, changedPaths(rep))
	}
}

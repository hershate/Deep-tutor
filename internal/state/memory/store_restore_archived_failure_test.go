package memory

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"reasonix/internal/base/testenv"
)

// assertNoDoubleIdentity holds the invariant a failed recovery must keep: the id
// is not active and archived at the same time. A derived index line may remain;
// the double-failure test covers repairing it on retry.
func assertNoDoubleIdentity(t *testing.T, s Store, dir, archivePath string) {
	t.Helper()
	_, active := s.Read("alpha")
	_, archived := os.Stat(archivePath)
	if active {
		t.Fatalf("a failed recovery left %q active as well as archived at %s", "alpha", archivePath)
	}
	if archived != nil {
		t.Fatalf("the archived copy disappeared although the recovery failed: %v", archived)
	}
	if body, err := os.ReadFile(filepath.Join(dir, indexFile)); err == nil && strings.Contains(string(body), "alpha.md") {
		t.Fatalf("a failed recovery left the index naming the fact as active:\n%s", body)
	}
}

func restoreFixture(t *testing.T) (Store, string, string) {
	t.Helper()
	dir := testenv.TempDir(t)
	s := Store{Dir: dir}
	if _, err := s.Save(Memory{Name: "alpha", Description: "gone", Type: TypeProject, Body: "body"}); err != nil {
		t.Fatal(err)
	}
	archivePath, err := s.Archive("alpha")
	if err != nil {
		t.Fatal(err)
	}
	return s, dir, archivePath
}

func TestRestoreArchivedIndexFailureLeavesNoDoubleIdentity(t *testing.T) {
	s, dir, archivePath := restoreFixture(t)
	fired := false
	withFault(t, func(op, path string) error {
		if op == "publish-index" && !fired {
			fired = true
			return &os.PathError{Op: "rename", Path: path, Err: os.ErrPermission}
		}
		return nil
	})
	_, err := s.RestoreArchived(archivePath)
	memoryFault = nil
	if err == nil {
		t.Fatal("recovery reported success although the index could not be written")
	}
	assertNoDoubleIdentity(t, s, dir, archivePath)
	if _, retryErr := s.RestoreArchived(archivePath); retryErr != nil {
		t.Fatalf("the failed recovery left no way to retry: %v", retryErr)
	}
	if _, ok := s.Read("alpha"); !ok {
		t.Fatal("the retry did not restore the fact")
	}
}

func TestRestoreArchivedArchiveRemovalFailureLeavesNoDoubleIdentity(t *testing.T) {
	s, dir, archivePath := restoreFixture(t)
	withFault(t, func(op, path string) error {
		if op == "remove" && path == archivePath {
			return &os.PathError{Op: "remove", Path: path, Err: os.ErrPermission}
		}
		return nil
	})
	_, err := s.RestoreArchived(archivePath)
	memoryFault = nil
	if err == nil {
		t.Fatal("recovery reported success although the archived copy could not be removed")
	}
	assertNoDoubleIdentity(t, s, dir, archivePath)
	if _, retryErr := s.RestoreArchived(archivePath); retryErr != nil {
		t.Fatalf("the failed recovery left no way to retry: %v", retryErr)
	}
	active, ok := s.Read("alpha")
	if !ok {
		t.Fatal("the retry did not restore the fact")
	}
	if _, stillArchived := os.Stat(archivePath); stillArchived == nil {
		t.Fatalf("the retry restored %+v but left the archived copy in place", active)
	}
}

// The rollback is not itself guaranteed to succeed: when the first index write
// and the rollback's index write both fail, the fact must still not be active,
// and a later retry must repair the index line the rollback could not drop.
func TestRestoreArchivedDoubleIndexFailureConvergesOnRetry(t *testing.T) {
	s, dir, archivePath := restoreFixture(t)
	withFault(t, func(op, path string) error {
		if op == "publish-index" {
			return &os.PathError{Op: "rename", Path: path, Err: os.ErrPermission}
		}
		return nil
	})
	_, err := s.RestoreArchived(archivePath)
	memoryFault = nil
	if err == nil {
		t.Fatal("recovery reported success although no index write could succeed")
	}
	if _, active := s.Read("alpha"); active {
		t.Fatal("a failed recovery left the fact active")
	}
	if _, archived := os.Stat(archivePath); archived != nil {
		t.Fatalf("the archived copy disappeared although the recovery failed: %v", archived)
	}
	// The retry must both complete the recovery and fix whatever the rollback
	// could not: the index must end up naming the restored fact, once.
	if _, retryErr := s.RestoreArchived(archivePath); retryErr != nil {
		t.Fatalf("the retry did not converge: %v", retryErr)
	}
	if _, ok := s.Read("alpha"); !ok {
		t.Fatal("the retry did not restore the fact")
	}
	body := mustReadString(t, filepath.Join(dir, indexFile))
	if n := strings.Count(body, "alpha.md"); n != 1 {
		t.Fatalf("the index names the restored fact %d times:\n%s", n, body)
	}
}

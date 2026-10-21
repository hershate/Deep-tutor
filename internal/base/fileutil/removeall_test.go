package fileutil

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

// RemoveAll clears the transient lock a scanner holds and removes the tree.
func TestRemoveAllRetriesTheTransientLock(t *testing.T) {
	dir := t.TempDir()
	sub := filepath.Join(dir, "a", "b")
	if err := os.MkdirAll(sub, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(sub, "f.txt"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}

	origTree := removeAllTree
	origRetries := maxReplaceRetries
	defer func() {
		removeAllTree = origTree
		maxReplaceRetries = origRetries
	}()
	maxReplaceRetries = 3

	calls := 0
	removeAllTree = func(path string) error {
		calls++
		if calls < 3 {
			return errors.New("directory not empty")
		}
		return origTree(path)
	}
	if err := RemoveAll(dir); err != nil {
		t.Fatalf("RemoveAll: %v", err)
	}
	if calls < 3 {
		t.Fatalf("RemoveAll gave up after %d attempts", calls)
	}
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Fatal("the tree survived the removal")
	}
}

// A permanent failure is reported, not retried into oblivion.
func TestRemoveAllReportsThePermanentFailure(t *testing.T) {
	origTree := removeAllTree
	origRetries := maxReplaceRetries
	defer func() {
		removeAllTree = origTree
		maxReplaceRetries = origRetries
	}()
	maxReplaceRetries = 2

	want := errors.New("permission denied")
	calls := 0
	removeAllTree = func(path string) error {
		calls++
		return want
	}
	if err := RemoveAll(t.TempDir()); !errors.Is(err, want) {
		t.Fatalf("RemoveAll error = %v, want %v", err, want)
	}
	if calls != 3 {
		t.Fatalf("RemoveAll tried %d times, want 3", calls)
	}
}

// A path that is already gone is a success, not a race to re-report.
func TestRemoveAllAcceptsAMissingPath(t *testing.T) {
	missing := filepath.Join(t.TempDir(), "never-existed")
	if err := RemoveAll(missing); err != nil {
		t.Fatalf("RemoveAll: %v", err)
	}
}

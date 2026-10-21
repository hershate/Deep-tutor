package testenv

import (
	"os"
	"time"

	"deep-tutor/internal/base/scratchroot"
)

// TB is the subset of *testing.T that TempDir needs, declared here so this
// package stays free of a testing import the way TestingM already does.
type TB interface {
	Helper()
	Cleanup(func())
	Fatalf(format string, args ...any)
	Logf(format string, args ...any)
}

const (
	tempDirRemoveAttempts = 100
	tempDirRemoveBackoff  = 20 * time.Millisecond
)

// TempDir is a drop-in for t.TempDir whose cleanup retries RemoveAll. A test
// can finish while the OS still holds a file it wrote: Windows keeps a deleted
// file's directory entry until its last handle closes, so removing the
// directory fails as non-empty when every file is already gone.
//
// The directory is created under the project scratch area rather than the
// operating system's temporary directory, so a test run does not fill the
// system volume with trees it intends to delete.
func TempDir(t TB) string {
	t.Helper()
	root, err := scratchroot.Ensure()
	if err != nil {
		t.Fatalf("testenv.TempDir: %v", err)
		return ""
	}
	dir, err := os.MkdirTemp(root, "deep-tutor-test-*")
	if err != nil {
		t.Fatalf("testenv.TempDir: %v", err)
		return ""
	}
	t.Cleanup(func() {
		var rmErr error
		for range tempDirRemoveAttempts {
			if rmErr = os.RemoveAll(dir); rmErr == nil {
				return
			}
			time.Sleep(tempDirRemoveBackoff)
		}
		t.Logf("testenv.TempDir: cleanup did not converge for %s: %v", dir, rmErr)
	})
	return dir
}

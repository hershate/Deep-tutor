package control

import (
	"os"
	"path/filepath"
	"testing"
)

// A granted folder may not overlap a directory every sandboxed command can
// write (temp, tool caches), so tests take their extra folders from the
// package directory instead of from the temp tree.
func tempAside(t *testing.T) (extraFolder func() string) {
	t.Helper()
	return func() string {
		dir, err := os.MkdirTemp(".", "extra-*")
		if err != nil {
			t.Fatal(err)
		}
		abs, err := filepath.Abs(dir)
		if err != nil {
			t.Fatal(err)
		}
		real, err := filepath.EvalSymlinks(abs)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = os.RemoveAll(real) })
		return real
	}
}

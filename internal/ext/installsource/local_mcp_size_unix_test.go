//go:build !windows

package installsource

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"

	"reasonix/internal/base/testenv"
)

// A path that is not a regular file would pass a size check made first.
func TestBoundedReadStopsAnEndlessSource(t *testing.T) {
	dir := testenv.TempDir(t)
	link := filepath.Join(dir, ".mcp.json")
	if err := os.Symlink("/dev/zero", link); err != nil {
		t.Fatal(err)
	}
	if _, _, err := readMCPJSON(link); !errors.Is(err, ErrSourceUnreadable) {
		t.Fatalf("symlink to /dev/zero: err = %v, want ErrSourceUnreadable", err)
	}

	fifo := filepath.Join(dir, "fifo.json")
	if err := syscall.Mkfifo(fifo, 0o600); err != nil {
		t.Fatal(err)
	}
	go func() {
		w, err := os.OpenFile(fifo, os.O_WRONLY, 0)
		if err != nil {
			return
		}
		defer w.Close()
		chunk := []byte(strings.Repeat(" ", 1<<16))
		for {
			if _, err := w.Write(chunk); err != nil {
				return
			}
		}
	}()
	if _, _, err := readMCPJSON(fifo); !errors.Is(err, ErrSourceUnreadable) {
		t.Fatalf("FIFO: err = %v, want ErrSourceUnreadable", err)
	}
}

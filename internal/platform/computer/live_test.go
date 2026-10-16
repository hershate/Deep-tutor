//go:build darwin || windows || linux

package computer

import (
	"os"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"
)

// liveSession drives the real driver named by REASONIX_LIVE_COMPUTER, which
// needs a desktop (and on macOS two permissions), so ordinary runs skip. One
// session for the run, which is how a host holds one: on macOS, while one
// driver process has captured, a capture in another never returns.
func liveSession(t *testing.T) *Session {
	t.Helper()
	path := os.Getenv("REASONIX_LIVE_COMPUTER")
	if path == "" {
		t.Skip("set REASONIX_LIVE_COMPUTER to a pinned CUA Driver executable to run live tests")
	}
	liveOnce.Do(func() {
		driver := NewDriver(path, os.Environ())
		if socket := os.Getenv("REASONIX_LIVE_COMPUTER_SOCKET"); socket != "" {
			driver = NewEmbeddedDriver(path, socket, os.Getenv("REASONIX_LIVE_COMPUTER_ABORT_SOCKET"), os.Environ())
		}
		nativeDriver = driver
		live = NewSession(driver)
	})
	return live
}

var (
	liveOnce sync.Once
	live     *Session
)

func waitLog(t *testing.T, path, needle string) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if body, _ := os.ReadFile(path); strings.Contains(string(body), needle) {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	body, _ := os.ReadFile(path)
	t.Fatalf("the target never logged %q:\n%s", needle, body)
}

func lineRef(t *testing.T, lines []string, needle string) string {
	t.Helper()
	for _, l := range lines {
		if strings.Contains(l, needle) {
			if m := regexp.MustCompile(`\[(r[A-Za-z0-9]+)\]`).FindStringSubmatch(l); m != nil {
				return m[1]
			}
		}
	}
	t.Fatalf("no ref on a line containing %q:\n%s", needle, strings.Join(lines, "\n"))
	return ""
}

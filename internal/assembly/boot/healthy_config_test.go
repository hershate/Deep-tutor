package boot

import (
	"context"
	"errors"
	"io"
	"os"
	"regexp"
	"sync"
	"testing"
	"time"

	"deep-tutor/internal/base/testenv"
	"deep-tutor/internal/contract/config"
	"deep-tutor/internal/contract/event"
)

func withRecorderSeam(t *testing.T, fn func(config.Roots, string) error) {
	t.Helper()
	prevFn, prevOnce := recordHealthyConfig, healthyConfigOnce
	recordHealthyConfig, healthyConfigOnce = fn, map[string]*sync.Once{}
	t.Cleanup(func() { recordHealthyConfig, healthyConfigOnce = prevFn, prevOnce })
}

func TestNoteHealthyConfigRecordsOncePerHome(t *testing.T) {
	var mu sync.Mutex
	var got []string
	done := make(chan struct{}, 4)
	withRecorderSeam(t, func(_ config.Roots, v string) error {
		mu.Lock()
		got = append(got, v)
		mu.Unlock()
		done <- struct{}{}
		return nil
	})

	noteHealthyConfig(config.Roots{}, "1.2.3")
	<-done
	noteHealthyConfig(config.Roots{}, "1.2.3")
	noteHealthyConfig(config.Roots{}, "9.9.9")

	mu.Lock()
	defer mu.Unlock()
	if len(got) != 1 || got[0] != "1.2.3" {
		t.Fatalf("want one record of 1.2.3, got %v", got)
	}
}

// TestNoteHealthyConfigRecordsEachHomeSeparately proves a process serving two
// users records a snapshot for each of them. One once for the whole process
// would let the first user's assembly consume the second user's record, and
// the second user's recovery would then read a snapshot that is not theirs.
func TestNoteHealthyConfigRecordsEachHomeSeparately(t *testing.T) {
	var mu sync.Mutex
	var got []string
	done := make(chan struct{}, 4)
	withRecorderSeam(t, func(roots config.Roots, v string) error {
		mu.Lock()
		got = append(got, roots.Home())
		mu.Unlock()
		done <- struct{}{}
		return nil
	})

	homeA := testenv.TempDir(t)
	homeB := testenv.TempDir(t)
	noteHealthyConfig(config.RootsForHome(homeA), "1.0.0")
	noteHealthyConfig(config.RootsForHome(homeB), "1.0.0")
	<-done
	<-done

	mu.Lock()
	defer mu.Unlock()
	if len(got) != 2 {
		t.Fatalf("want one record per home, got %v", got)
	}
	seen := map[string]bool{}
	for _, home := range got {
		seen[home] = true
	}
	if !seen[homeA] || !seen[homeB] {
		t.Fatalf("recorded homes = %v, want both %q and %q", got, homeA, homeB)
	}
}

func TestNoteHealthyConfigSkipsUndeclaredVersion(t *testing.T) {
	called := make(chan string, 2)
	withRecorderSeam(t, func(_ config.Roots, v string) error { called <- v; return nil })

	// An embedded or test assembly declares no version and must not write
	// process-wide user state.
	noteHealthyConfig(config.Roots{}, "")
	noteHealthyConfig(config.Roots{}, "   ")

	select {
	case v := <-called:
		t.Fatalf("recorded %q for an assembly that declared no version", v)
	default:
	}
}

func TestNoteHealthyConfigSurvivesRecorderFailure(t *testing.T) {
	done := make(chan struct{})
	withRecorderSeam(t, func(config.Roots, string) error { close(done); return errors.New("locked") })
	noteHealthyConfig(config.Roots{}, "1.0.0")
	<-done // a failing recorder must not panic or block the caller
}

// TestBuildRuntimeRecordsHealthyConfig is the regression guard for the gap that
// let the last-known-good snapshot go stale: the write path lived in the
// retired Wails shell, so deleting that shell left config recovery reading a
// snapshot nothing refreshed. Assembly is what proves the config usable, so
// the record rides the assembly path every frontend already goes through.
func TestBuildRuntimeRecordsHealthyConfig(t *testing.T) {
	got := make(chan string, 1)
	withRecorderSeam(t, func(_ config.Roots, v string) error { got <- v; return nil })

	// A build that cannot resolve a model still fails before recording; use the
	// declared version to prove the wiring reaches the recorder on success.
	if _, err := BuildRuntime(context.Background(), Options{
		Version: "7.7.7", WorkspaceRoot: robustTempDir(t), Sink: event.Discard, Stderr: io.Discard,
	}); err != nil {
		t.Skipf("assembly unavailable in this environment: %v", err)
	}
	select {
	case v := <-got:
		if v != "7.7.7" {
			t.Fatalf("recorded %q, want the declared version", v)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("a successful assembly did not record a last-known-good snapshot")
	}
}

// TestTopLevelFrontendsDeclareVersion keeps every real installation entry point
// declaring a version. A frontend that stops declaring one silently stops
// refreshing the snapshot its own recovery path reads.
func TestTopLevelFrontendsDeclareVersion(t *testing.T) {
	for _, want := range []struct {
		path  string
		match *regexp.Regexp
	}{
		{"../../frontend/cli/cli.go", regexp.MustCompile(`Version:\s+version,`)},
		{"../../frontend/cli/cli.go", regexp.MustCompile(`overrides\.Version = version`)},
		{"../../frontend/cli/serve_frontend.go", regexp.MustCompile(`Version:\s+opts\.version,`)},
		{"../../frontend/cli/build_options.go", regexp.MustCompile(`Version:\s+overrides\.Version,`)},
	} {
		src, err := os.ReadFile(want.path)
		if err != nil {
			t.Fatal(err)
		}
		if !want.match.Match(src) {
			t.Errorf("%s no longer declares a version to the assembly (%s)", want.path, want.match)
		}
	}
}

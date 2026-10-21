package config

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"deep-tutor/internal/base/filelock"
)

// The registry of configuration-edit locks used to only grow: one file per
// configuration path ever edited, kept forever because deleting a lock file
// breaks the exclusion of a holder that still has it open. A harness that
// gives every run its own home therefore grew it without bound.

func TestSweepReclaimsIdleLockFilesAndKeepsWhatIsInUse(t *testing.T) {
	dir := t.TempDir()
	idle := filepath.Join(dir, "idle.lock")
	fresh := filepath.Join(dir, "fresh.lock")
	held := filepath.Join(dir, "held.lock")
	nonEmpty := filepath.Join(dir, "non-empty.lock")
	for _, path := range []string{idle, fresh, held, nonEmpty} {
		if err := os.WriteFile(path, nil, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(nonEmpty, []byte("not ours"), 0o600); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-48 * time.Hour)
	for _, path := range []string{idle, held, nonEmpty} {
		if err := os.Chtimes(path, old, old); err != nil {
			t.Fatal(err)
		}
	}
	// A lock somebody holds is not reclaimable, however old it is.
	release, err := filelock.TryAcquire(held)
	if err != nil {
		t.Fatalf("acquire the held lock: %v", err)
	}
	defer release()

	sweepConfigEditLocksOnce(dir)

	if _, err := os.Stat(idle); !os.IsNotExist(err) {
		t.Errorf("the idle lock file was not reclaimed: %v", err)
	}
	for name, path := range map[string]string{
		"fresh":     fresh,
		"held":      held,
		"non-empty": nonEmpty,
	} {
		if _, err := os.Stat(path); err != nil {
			t.Errorf("the %s lock file was reclaimed: %v", name, err)
		}
	}
}

func TestTouchKeepsAnActivelyUsedLockFileOutOfTheSweep(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.lock")
	if err := os.WriteFile(path, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-48 * time.Hour)
	if err := os.Chtimes(path, old, old); err != nil {
		t.Fatal(err)
	}

	touchConfigEditLock(path)
	sweepConfigEditLocksOnce(dir)

	if _, err := os.Stat(path); err != nil {
		t.Fatalf("a lock file in use was reclaimed: %v", err)
	}
}

func TestSweepRunsOncePerRegistryPerProcess(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "idle.lock")
	if err := os.WriteFile(path, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-48 * time.Hour)
	if err := os.Chtimes(path, old, old); err != nil {
		t.Fatal(err)
	}

	sweepConfigEditLocks(dir)
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("the idle lock file was not reclaimed: %v", err)
	}

	// A second call is a no-op: the sweep is a once-per-process cost.
	again := filepath.Join(dir, "again.lock")
	if err := os.WriteFile(again, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(again, old, old); err != nil {
		t.Fatal(err)
	}
	sweepConfigEditLocks(dir)
	if _, err := os.Stat(again); err != nil {
		t.Fatalf("the second sweep reclaimed a file: %v", err)
	}
}

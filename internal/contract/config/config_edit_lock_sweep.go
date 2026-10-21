package config

import (
	"os"
	"path/filepath"
	"sync"
	"time"

	"deep-tutor/internal/base/filelock"
)

const (
	// configEditLockIdleAfter is how long a lock file may sit unused before it
	// is reclaimed. A configuration nobody touched for a day is not a
	// configuration in use.
	configEditLockIdleAfter = 24 * time.Hour
	// configEditLockSweepBatch bounds how many files one sweep reclaims, so a
	// registry with a long backlog cannot stall the edit that triggered it.
	configEditLockSweepBatch = 1000
	// configEditLockSweepBudget bounds the time one sweep may spend.
	configEditLockSweepBudget = 2 * time.Second
)

var configEditLockSwept struct {
	sync.Mutex
	dirs map[string]bool
}

// sweepConfigEditLocks reclaims the lock files nobody is using.
//
// A lock file outlives its use: deleting one breaks the exclusion of a holder
// that still has it open, so the registry keeps every file it ever made. It is
// per OS user and per distinct configuration path, and a harness that gives
// every run its own home gives every run its own path — the directory then
// grows without bound and never shrinks. On a machine that ran the harness
// for a season it reached tens of thousands of files.
//
// Two conditions together make a file reclaimable: its timestamp says nobody
// has used it for a while, and its lock can be taken, which means no holder is
// inside. A file in use is never touched.
//
// The sweep runs once per registry per process and reclaims a bounded batch
// each time, so the backlog drains over successive edits instead of in one
// stall.
func sweepConfigEditLocks(dir string) {
	configEditLockSwept.Lock()
	if configEditLockSwept.dirs == nil {
		configEditLockSwept.dirs = map[string]bool{}
	}
	already := configEditLockSwept.dirs[dir]
	configEditLockSwept.dirs[dir] = true
	configEditLockSwept.Unlock()
	if already {
		return
	}
	sweepConfigEditLocksOnce(dir)
}

// sweepConfigEditLocksOnce is the work, without the once-per-process guard.
func sweepConfigEditLocksOnce(dir string) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	cutoff := time.Now().Add(-configEditLockIdleAfter)
	deadline := time.Now().Add(configEditLockSweepBudget)
	reclaimed := 0
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		if reclaimed >= configEditLockSweepBatch || time.Now().After(deadline) {
			return
		}
		info, err := entry.Info()
		if err != nil || info.Size() != 0 || info.ModTime().After(cutoff) {
			continue
		}
		path := filepath.Join(dir, entry.Name())
		release, err := filelock.TryAcquire(path)
		if err != nil {
			// Somebody holds it: leave it alone.
			continue
		}
		release()
		if os.Remove(path) == nil {
			reclaimed++
		}
	}
}

// touchConfigEditLock keeps a lock file's timestamp meaningful.
//
// The sweep reclaims files that have not been used for a while, and a
// timestamp that never moves is indistinguishable from one nobody has touched
// in months: the configuration a user edits every day would be reclaimed
// underneath them.
func touchConfigEditLock(path string) {
	now := time.Now()
	_ = os.Chtimes(path, now, now)
}
